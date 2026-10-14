"""Avance de un análisis (página N de M) sin pasar un parámetro por cada función: usa una variable de contexto.

El trabajo asíncrono (jobs.py) registra su función de avance en su hilo; el análisis solo llama a `report`.
"""
from contextvars import ContextVar
from typing import Callable, Optional

_callback: ContextVar[Optional[Callable[[int, int], None]]] = ContextVar("progress_callback", default=None)


def set_callback(cb: Optional[Callable[[int, int], None]]) -> None:
    _callback.set(cb)


def report(done: int, total: int) -> None:
    """Avisa que se llevan `done` de `total` páginas. No hace nada si nadie escucha; nunca lanza excepciones."""
    cb = _callback.get()
    if cb:
        try:
            cb(done, total)
        except Exception:
            pass
