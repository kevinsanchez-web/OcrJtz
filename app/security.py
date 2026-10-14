"""Seguridad: autenticación por API key, límite de peticiones, bloqueo por intentos fallidos y cabeceras."""
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import (API_CLIENTS, MAX_AUTH_FAILURES, MAX_CONCURRENT_JOBS, RATE_LIMIT_PER_MIN,
                        RATE_LIMIT_READS_PER_MIN, TRANSIT_CLIENT)

logger = logging.getLogger("document-api")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Límite de trabajos de OCR simultáneos (el OCR es pesado). Siempre queda un cupo libre para la app de tránsito:
# las demás apps no pueden ocupar todos los trabajos a la vez, así su carga no interrumpe el flujo de tránsito.
_job_lock = threading.Lock()
_active_total = 0
_active_other = 0
_MAX_OTHER = max(1, MAX_CONCURRENT_JOBS - 1)


def acquire_job(client: Optional[str]) -> bool:
    global _active_total, _active_other
    other = client != TRANSIT_CLIENT
    with _job_lock:
        if _active_total >= MAX_CONCURRENT_JOBS or (other and _active_other >= _MAX_OTHER):
            return False
        _active_total += 1
        _active_other += other
        return True


def release_job(client: Optional[str]) -> None:
    global _active_total, _active_other
    with _job_lock:
        _active_total -= 1
        _active_other -= client != TRANSIT_CLIENT

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


_LOOPBACK = ("127.0.0.1", "::1", "localhost")


def client_ip(request: Request) -> str:
    """IP real del cliente. Detrás de un túnel de Cloudflare (que se conecta desde la propia máquina) todas las
    peticiones parecen venir de 127.0.0.1; entonces se usa `CF-Connecting-IP`. Ese encabezado SOLO se acepta si la
    conexión directa es local: desde cualquier otro equipo se podría falsificar para esquivar el bloqueo."""
    direct = request.client.host if request.client else "unknown"
    if direct in _LOOPBACK:
        real = request.headers.get("cf-connecting-ip", "").strip()
        if real and len(real) <= 45:
            return real
    return direct


def require_api_key(request: Request, key: Optional[str] = Security(api_key_header)) -> None:
    """Dependencia de FastAPI: exige una API key válida y aplica los límites por IP."""
    ip = client_ip(request)
    too_many = HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiadas solicitudes",
        headers={"Retry-After": "60"},
    )
    # Bloqueo temporal tras muchos intentos fallidos (anti fuerza bruta)
    if not _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES, record=False):
        logger.warning("Peticion BLOQUEADA por demasiados intentos fallidos desde %s", ip)
        raise too_many

    # Se compara contra todas las claves sin cortocircuitar, en tiempo constante; la clave dice qué app llama
    client: Optional[str] = None
    if key:
        for name, k in API_CLIENTS:
            if secrets.compare_digest(key.encode(), k.encode()):
                client = name
    valid = client is not None
    if not valid:
        logger.warning("Peticion RECHAZADA (clave inválida o ausente) desde %s", ip)
        _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    request.state.client = client
    # El límite por minuto es por app e IP: otra app en el mismo equipo no gasta el cupo de la de tránsito
    # Subir archivos (POST) es lo costoso y tiene el límite estricto; consultar estados y estadísticas (GET), que los
    # paneles hacen cada segundo, tiene uno holgado: antes el propio panel se bloqueaba con "429 Demasiadas solicitudes"
    is_read = request.method in ("GET", "HEAD")
    bucket = f"{'get' if is_read else 'post'}:{client}@{ip}"
    limit = RATE_LIMIT_READS_PER_MIN if is_read else RATE_LIMIT_PER_MIN
    if limit > 0 and not _hit(_requests_by_ip, bucket, 60, limit):  # 0 = sin límite por minuto
        logger.warning("Peticion LIMITADA (demasiadas por minuto) de %s desde %s", client, ip)
        raise too_many


async def security_headers(request: Request, call_next):
    """Middleware: las respuestas contienen datos personales, nunca deben cachearse."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
