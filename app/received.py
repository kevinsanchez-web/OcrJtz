"""Documentos recibidos recientemente, SOLO EN MEMORIA, para verlos en el panel de escritorio.

Apagado por defecto (KEEP_RECEIVED=1 lo enciende). Guarda el archivo original y su resultado (datos personales) hasta
RECEIVED_TTL segundos, con tope de cantidad y de tamaño; nunca toca el disco. Solo el panel local puede leerlo
(ver `local_panel_only` en app/security.py).
"""
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from app.config import KEEP_RECEIVED, RECEIVED_MAX_BYTES, RECEIVED_MAX_ITEMS, RECEIVED_TTL

_lock = threading.Lock()
_items: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def grade(result: Any, error: Optional[dict]) -> str:
    """Semáforo del documento: green (lectura segura), amber (conviene revisar) o red (no se leyó o falló)."""
    if error is not None or result is None:
        return "red"
    docs = getattr(result, "documents", None) or []
    if docs:
        return "amber" if any(d.needsReview or d.confidence < 0.8 for d in docs) else "green"
    pages = getattr(result, "pages", None) or []
    if pages:  # OCR general sin tipo conocido: por la confianza media de lectura
        avg = sum(p.confidence for p in pages) / len(pages)
        return "green" if avg >= 0.9 else "amber" if avg >= 0.7 else "red"
    return "red"


def _purge() -> None:
    now = time.time()
    for k in [k for k, v in _items.items() if now - v["ts"] > RECEIVED_TTL]:
        del _items[k]
    while len(_items) > RECEIVED_MAX_ITEMS:
        _items.popitem(last=False)
    while len(_items) > 1 and sum(v["size"] for v in _items.values()) > RECEIVED_MAX_BYTES:
        _items.popitem(last=False)


def add(rid: str, client: str, endpoint: str, filename: Optional[str], contents: bytes, result: Any = None,
        error: Optional[dict] = None, seconds: float = 0.0, cached: bool = False) -> None:
    if not KEEP_RECEIVED:
        return
    item = {"id": rid, "ts": time.time(), "client": client, "endpoint": endpoint, "name": filename or "",
            "size": len(contents), "data": contents, "result": result, "error": error,
            "seconds": round(seconds, 1), "cached": cached, "grade": grade(result, error)}
    with _lock:
        _items[rid] = item
        _items.move_to_end(rid)
        _purge()


def meta(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item["result"]
    docs = [{"type": d.documentType, "confidence": d.confidence, "needsReview": d.needsReview, "page": d.page}
            for d in (getattr(result, "documents", None) or [])]
    return {"id": item["id"], "ts": item["ts"], "client": item["client"], "endpoint": item["endpoint"],
            "name": item["name"], "size": item["size"], "seconds": item["seconds"], "cached": item["cached"],
            "grade": item["grade"], "docs": docs, "error": item["error"]}


def listing() -> List[Dict[str, Any]]:
    """Metadatos de los recibidos, el más reciente primero (sin el archivo ni el resultado)."""
    with _lock:
        _purge()
        return [meta(v) for v in reversed(_items.values())]


def get(rid: str) -> Optional[Dict[str, Any]]:
    with _lock:
        _purge()
        return _items.get(rid)


def clear() -> None:
    with _lock:
        _items.clear()
