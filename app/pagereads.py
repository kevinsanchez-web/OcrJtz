"""Lecturas de página (cajas con posición, tamaño, giros) que el análisis de tránsito deja para quien las pida.

`analyze_image` anota lo que leyó con `note`; quien orquesta la página lo recoge con `take`. Así se pueden devolver
las posiciones incluso de las páginas en las que no se reconoció ningún documento (con `lines=true`).
"""
from contextvars import ContextVar
from typing import Any, Dict, Optional

_last: ContextVar[Optional[Dict[str, Any]]] = ContextVar("last_page_read", default=None)


def note(read: Optional[Dict[str, Any]]) -> None:
    _last.set(read)


def take() -> Optional[Dict[str, Any]]:
    """La última lectura anotada (y la olvida)."""
    read = _last.get()
    _last.set(None)
    return read
