"""Seguridad: autenticación por API key, límite de peticiones, bloqueo por intentos fallidos y cabeceras."""
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import API_KEYS, MAX_AUTH_FAILURES, MAX_CONCURRENT_JOBS, RATE_LIMIT_PER_MIN

logger = logging.getLogger("document-api")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Límite de trabajos de OCR simultáneos (el OCR es pesado)
job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)

# Contadores en memoria (por proceso): ventana deslizante por IP
_rate_lock = threading.Lock()
_requests_by_ip: Dict[str, Deque[float]] = defaultdict(deque)
_failures_by_ip: Dict[str, Deque[float]] = defaultdict(deque)


def _hit(bucket: Dict[str, Deque[float]], ip: str, window: float, limit: int, record: bool = True) -> bool:
    """Ventana deslizante en memoria. Devuelve True si NO se ha superado el límite."""
    now = time.monotonic()
    with _rate_lock:
        q = bucket[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        if record:
            q.append(now)
        elif not q:
            bucket.pop(ip, None)
        return True


def require_api_key(request: Request, key: Optional[str] = Security(api_key_header)) -> None:
    """Dependencia de FastAPI: exige una API key válida y aplica los límites por IP."""
    ip = request.client.host if request.client else "unknown"
    too_many = HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiadas solicitudes",
        headers={"Retry-After": "60"},
    )
    # Bloqueo temporal tras muchos intentos fallidos (anti fuerza bruta)
    if not _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES, record=False):
        logger.warning("Peticion BLOQUEADA por demasiados intentos fallidos desde %s", ip)
        raise too_many

    # Se compara contra todas las claves sin cortocircuitar, en tiempo constante
    valid = False
    if key:
        for k in API_KEYS:
            valid |= secrets.compare_digest(key.encode(), k.encode())
    if not valid:
        logger.warning("Peticion RECHAZADA (clave inválida o ausente) desde %s", ip)
        _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not _hit(_requests_by_ip, ip, 60, RATE_LIMIT_PER_MIN):
        logger.warning("Peticion LIMITADA (demasiadas por minuto) desde %s", ip)
        raise too_many


async def security_headers(request: Request, call_next):
    """Middleware: las respuestas contienen datos personales, nunca deben cachearse."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
