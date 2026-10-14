"""Historial de peticiones en SQLite (data/history.db): para estadísticas y para saber qué pasó.

Solo guarda metadatos (app, endpoint, tamaño, tiempo, estado, cuántos documentos): NUNCA el contenido de los
archivos ni los datos extraídos, que pueden ser personales.
"""
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("document-api")

DB_PATH = Path(os.getenv("HISTORY_DB") or Path(__file__).resolve().parent.parent / "data" / "history.db")
KEEP_ROWS = 5000
_lock = threading.Lock()
_started = time.time()
_conn: Optional[sqlite3.Connection] = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("""CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, rid TEXT, client TEXT, endpoint TEXT,
            bytes INTEGER, seconds REAL, status INTEGER, docs INTEGER, pages INTEGER, error TEXT)""")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(ts)")
        _conn.commit()
    return _conn


def record(rid: str, client: str, endpoint: str, size: int, seconds: float, status: int,
           docs: int = 0, pages: int = 0, error: str = "") -> None:
    """Anota una petición. Nunca lanza excepciones: el historial no debe afectar a la API."""
    try:
        with _lock:
            db = _db()
            db.execute("INSERT INTO requests (ts, rid, client, endpoint, bytes, seconds, status, docs, pages, error) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), rid, client, endpoint, size, round(seconds, 2), status, docs, pages, error[:200]))
            db.execute("DELETE FROM requests WHERE id <= (SELECT MAX(id) FROM requests) - ?", (KEEP_ROWS,))
            db.commit()
    except Exception:
        logger.exception("No se pudo guardar en el historial")


def stats(client: Optional[str] = None, recent: int = 15) -> Dict[str, Any]:
    """Resumen: totales, por app, y las últimas peticiones. `client`: limitar a una app (None = todas)."""
    where, args = ("WHERE client = ?", (client,)) if client else ("", ())
    with _lock:
        db = _db()
        total, ok, avg, last_ts = db.execute(
            f"SELECT COUNT(*), SUM(status < 400), AVG(CASE WHEN status < 400 THEN seconds END), MAX(ts) "
            f"FROM requests {where}", args).fetchone()
        day = db.execute(f"SELECT COUNT(*) FROM requests {where + ' AND' if where else 'WHERE'} ts > ?",
                         args + (time.time() - 86400,)).fetchone()[0]
        by_client = [dict(zip(("client", "requests", "errors", "avgSeconds", "lastTs"), r)) for r in db.execute(
            f"SELECT client, COUNT(*), SUM(status >= 400), AVG(CASE WHEN status < 400 THEN seconds END), MAX(ts) "
            f"FROM requests {where} GROUP BY client ORDER BY MAX(ts) DESC", args)]
        last = [dict(zip(("ts", "client", "endpoint", "bytes", "seconds", "status", "docs", "pages", "error"), r))
                for r in db.execute(f"SELECT ts, client, endpoint, bytes, seconds, status, docs, pages, error "
                                    f"FROM requests {where} ORDER BY id DESC LIMIT ?", args + (recent,))]
    for c in by_client:
        c["errors"] = int(c["errors"] or 0)
        c["avgSeconds"] = round(c["avgSeconds"] or 0.0, 1)
    return {"uptimeSeconds": int(time.time() - _started), "total": total or 0, "ok": int(ok or 0),
            "errors": (total or 0) - int(ok or 0), "last24h": day, "avgSeconds": round(avg or 0.0, 1),
            "lastTs": last_ts, "clients": by_client, "recent": last}
