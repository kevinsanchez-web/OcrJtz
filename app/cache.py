"""Caché en MEMORIA de resultados: el mismo archivo con los mismos parámetros se responde al instante.

Solo vive en memoria y se pierde al reiniciar (los resultados contienen datos personales: nunca se escriben a
disco). Cada app tiene su propio espacio: una app no puede recibir un resultado que pidió otra.
"""
import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

from app.config import CACHE_ENABLED, CACHE_MAX_ENTRIES, CACHE_TTL_SECONDS

_lock = threading.Lock()
_store: "OrderedDict[str, tuple]" = OrderedDict()  # clave -> (expira, resultado)
_VERSION = "1"  # súbelo si cambia el formato de salida, para no servir resultados viejos


def make_key(contents: bytes, *parts: Any) -> str:
    h = hashlib.sha256(contents).hexdigest()
    return "|".join([_VERSION, h, *map(str, parts)])


def get(key: str) -> Optional[Any]:
    if not CACHE_ENABLED:
        return None
    with _lock:
        item = _store.get(key)
        if not item:
            return None
        if item[0] < time.time():
            del _store[key]
            return None
        _store.move_to_end(key)
        return item[1]


def put(key: str, value: Any) -> None:
    if not CACHE_ENABLED:
        return
    with _lock:
        _store[key] = (time.time() + CACHE_TTL_SECONDS, value)
        _store.move_to_end(key)
        while len(_store) > CACHE_MAX_ENTRIES:
            _store.popitem(last=False)
