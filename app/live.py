"""Seguimiento EN VIVO de lo que hace la API con cada documento (etapas, OCR y texto del VLM mientras se genera),
para verlo en el panel de escritorio. Solo en memoria y solo con KEEP_RECEIVED=1 (apagado = no hace nada).

Lo que escribe el VLM contiene datos personales: se trata igual que los documentos recibidos (app/received.py) y lo lee
únicamente el panel local (`local_panel_only`). El documento actual viaja en una variable de contexto, así que el OCR y
el cliente del VLM solo llaman a `note`/`vlm_*` sin pasar parámetros por cada función (igual que app/progress.py).
"""
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from app.config import KEEP_RECEIVED

MAX_ENTRIES = 30
MAX_EVENTS = 80
MAX_VLM_CALLS = 6
MAX_VLM_CHARS = 4000
KEEP_AFTER_END = 90.0  # segundos que sigue visible un documento terminado (el panel lo cambia por su resultado)

_current: ContextVar[Optional[str]] = ContextVar("live_current", default=None)
_lock = threading.Lock()
_entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def active() -> bool:
    """True si hay un documento en seguimiento en este hilo (el cliente del VLM lo usa para pedir la respuesta en flujo)."""
    return KEEP_RECEIVED and _current.get() is not None


def begin(key: str, client: str, endpoint: str, filename: Optional[str], size: int) -> None:
    if not KEEP_RECEIVED:
        return
    now = time.time()
    with _lock:
        _entries[key] = {"id": key, "client": client, "endpoint": endpoint, "name": filename or "", "size": size,
                         "state": "queued", "started": now, "finished": None, "stage": "En cola", "page": None,
                         "total": None, "events": [{"t": 0.0, "text": "En cola"}], "vlm": []}
        _purge(now)


def attach(key: str) -> None:
    """Desde aquí, en este hilo, las notas se anotan en `key`."""
    if KEEP_RECEIVED:
        _current.set(key)


def detach() -> None:
    _current.set(None)


def _purge(now: float) -> None:
    for k in [k for k, v in _entries.items() if v["finished"] and now - v["finished"] > KEEP_AFTER_END]:
        del _entries[k]
    while len(_entries) > MAX_ENTRIES:
        _entries.popitem(last=False)


def _add_event(e: Dict[str, Any], text: str) -> None:
    e["events"].append({"t": round(time.time() - e["started"], 1), "text": text})
    if len(e["events"]) > MAX_EVENTS:
        del e["events"][1:len(e["events"]) - MAX_EVENTS + 1]


def start(key: str) -> None:
    with _lock:
        e = _entries.get(key)
        if e:
            e["state"] = "running"
            e["stage"] = "Empieza el análisis"
            _add_event(e, "Empieza el análisis")


def note(text: str) -> None:
    """Etapa actual del documento en seguimiento (queda también en su línea de tiempo)."""
    key = _current.get()
    if not key or not KEEP_RECEIVED:
        return
    with _lock:
        e = _entries.get(key)
        if e:
            e["stage"] = text
            _add_event(e, text)


def page(n: int, total: int) -> None:
    key = _current.get()
    if not key or not KEEP_RECEIVED:
        return
    with _lock:
        e = _entries.get(key)
        if e:
            e["page"], e["total"] = n, total
            e["stage"] = f"Página {n} de {total}"
            _add_event(e, f"Página {n} de {total}")


def vlm_begin(label: str) -> None:
    key = _current.get()
    if not key or not KEEP_RECEIVED:
        return
    with _lock:
        e = _entries.get(key)
        if e:
            e["vlm"].append({"label": label, "text": "", "done": False, "t0": time.time(), "seconds": None})
            del e["vlm"][:-MAX_VLM_CALLS]
            e["stage"] = f"VLM ({label}): esperando al modelo..."
            _add_event(e, e["stage"])


def vlm_chunk(piece: str) -> None:
    key = _current.get()
    if not key or not KEEP_RECEIVED:
        return
    with _lock:
        e = _entries.get(key)
        if e and e["vlm"] and not e["vlm"][-1]["done"]:
            call = e["vlm"][-1]
            if len(call["text"]) < MAX_VLM_CHARS:
                call["text"] += piece
            e["stage"] = f"VLM ({call['label']}): generando..."


def vlm_end(ok: bool) -> None:
    key = _current.get()
    if not key or not KEEP_RECEIVED:
        return
    with _lock:
        e = _entries.get(key)
        if e and e["vlm"] and not e["vlm"][-1]["done"]:
            call = e["vlm"][-1]
            call["done"] = True
            call["seconds"] = round(time.time() - call["t0"], 1)
            e["stage"] = f"VLM ({call['label']}): {'listo' if ok else 'sin resultado'} en {call['seconds']} s"
            _add_event(e, e["stage"])


def end(key: str, state: str, detail: str = "") -> None:
    """state: 'done' | 'error'."""
    if not KEEP_RECEIVED:
        return
    now = time.time()
    with _lock:
        e = _entries.get(key)
        if e:
            e["state"] = state
            e["finished"] = now
            e["stage"] = "Terminado" if state == "done" else f"Falló: {detail}" if detail else "Falló"
            _add_event(e, e["stage"])
        _purge(now)


def snapshot() -> List[Dict[str, Any]]:
    """Documentos en seguimiento (en curso y recién terminados), el más reciente primero."""
    now = time.time()
    with _lock:
        _purge(now)
        out = []
        for e in reversed(_entries.values()):
            out.append({"id": e["id"], "client": e["client"], "endpoint": e["endpoint"], "name": e["name"],
                        "size": e["size"], "state": e["state"], "stage": e["stage"], "page": e["page"],
                        "total": e["total"], "elapsed": round((e["finished"] or now) - e["started"], 1),
                        "events": list(e["events"]),
                        "vlm": [{"label": v["label"], "text": v["text"], "done": v["done"], "seconds": v["seconds"]}
                                for v in e["vlm"]]})
        return out


def clear() -> None:
    with _lock:
        _entries.clear()
