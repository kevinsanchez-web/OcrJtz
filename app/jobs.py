"""Trabajos asíncronos: se sube el archivo, se recibe un identificador y el resultado se consulta después.

Sirve para PDF largos (minutos) y para redes que cortan las peticiones largas: ninguna llamada dura más de unos
segundos. Todo vive en memoria (los resultados tienen datos personales) y se borra a los 30 minutos de terminar.
"""
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from app import cache, history, live, progress, received
from app.config import KEEP_RECEIVED, MAX_CONCURRENT_JOBS, TRANSIT_CLIENT
from app.schemas import AnalyzeDocumentResponse
from app.security import acquire_job, release_job
from app.services.analyzer import AnalysisError, analyze_file
from app.services.generic import ocr_file, pages_from_reads

logger = logging.getLogger("document-api")

RESULT_TTL = 1800          # segundos que se conserva un resultado terminado
MAX_ACTIVE_PER_CLIENT = 10  # en cola o procesándose, por app
MAX_STORED = 200
_pool = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT_JOBS), thread_name_prefix="job")
_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


class TooManyJobs(Exception):
    pass


def endpoint_for(mode: str) -> str:
    return "ocr" if mode == "ocr" else "analyze-document"


def _purge() -> None:
    now = time.time()
    for jid in [j for j, v in _jobs.items() if v["finished"] and now - v["finished"] > RESULT_TTL]:
        del _jobs[jid]
    if len(_jobs) > MAX_STORED:  # demasiados: se descartan los terminados más antiguos
        done = sorted((v["finished"], j) for j, v in _jobs.items() if v["finished"])
        for _, j in done[: len(_jobs) - MAX_STORED]:
            del _jobs[j]


def submit(contents: bytes, client: str, mode: str, structured: bool, lines: bool, fields: tuple = (), deep: bool = False,
           filename: str = "") -> str:
    """Crea el trabajo y devuelve su identificador. Lanza TooManyJobs si la app tiene demasiados pendientes."""
    endpoint = endpoint_for(mode)
    requested_lines = lines
    lines = lines or KEEP_RECEIVED  # el panel necesita las posiciones; `_job_view` las quita si la app no las pidió
    parts = (structured, lines, fields, deep) if mode == "ocr" else (lines,)
    job = {"id": uuid.uuid4().hex[:12], "client": client, "mode": mode, "status": "queued", "done": 0, "total": 0,
           "created": time.time(), "started": None, "finished": None, "result": None, "error": None, "size": len(contents),
           "filename": filename, "lines": requested_lines}
    with _lock:
        _purge()
        if sum(1 for v in _jobs.values() if v["client"] == client and not v["finished"]) >= MAX_ACTIVE_PER_CLIENT:
            raise TooManyJobs()
        _jobs[job["id"]] = job
    hit = cache.get(cache.make_key(contents, client, endpoint, *parts))
    if hit is not None:  # mismo archivo ya analizado: terminado al instante
        job.update(status="done", result=hit, started=time.time(), finished=time.time(), cached=True)
        history.record(job["id"], client, "job:" + endpoint, len(contents), 0, 200, *_count(hit), "desde caché")
        received.add(job["id"], client, "job:" + endpoint, filename, contents, result=hit, cached=True)
        return job["id"]
    live.begin(job["id"], client, "job:" + endpoint, filename, len(contents))
    _pool.submit(_run, job, contents, endpoint, parts, structured, lines, fields, deep)
    return job["id"]


def _count(result) -> tuple:
    docs = len(getattr(result, "documents", []) or [])
    pages = len(getattr(result, "pages", []) or []) or len({d.page for d in getattr(result, "documents", []) if d.page})
    return docs, pages


def _run(job: dict, contents: bytes, endpoint: str, parts: tuple, structured: bool, lines: bool, fields: tuple, deep: bool) -> None:
    client, rid = job["client"], job["id"][:6]
    while not acquire_job(client):  # espera su turno (la cola es justamente el objetivo: nada se rechaza con 503)
        if job.get("cancelled"):
            job.update(status="error", finished=time.time(), error={"status": 499, "detail": "Cancelado"})
            live.end(job["id"], "error", "Cancelado")
            return
        time.sleep(0.5)
    t0 = time.monotonic()
    job.update(status="processing", started=time.time())
    live.attach(job["id"])
    live.start(job["id"])
    progress.set_callback(lambda d, t: job.update(done=d, total=t))
    logger.info("[%s] TRABAJO asíncrono de la app '%s' PROCESANDO (%.0f KB)", rid, client, len(contents) / 1024)
    try:
        if job["mode"] == "ocr":
            result = ocr_file(contents, rid, structured, lines, client == TRANSIT_CLIENT, list(fields), deep)
        else:
            reads: dict = {}
            detected, warnings = analyze_file(contents, rid, reads if lines else None)
            result = AnalyzeDocumentResponse(documents=detected, warnings=warnings,
                                             pages=pages_from_reads(reads, detected) if lines else None)
        job.update(status="done", result=result, finished=time.time(), done=job["total"])
        history.record(rid, client, "job:" + endpoint, len(contents), time.monotonic() - t0, 200, *_count(result))
        received.add(job["id"], client, "job:" + endpoint, job.get("filename"), contents, result=result,
                     seconds=time.monotonic() - t0)
        live.end(job["id"], "done")
        cache.put(cache.make_key(contents, client, endpoint, *parts), result)
        logger.info("[%s] TRABAJO TERMINADO en %.1fs", rid, time.monotonic() - t0)
    except AnalysisError as e:
        job.update(status="error", finished=time.time(), error={"status": e.status_code, "detail": e.detail})
        history.record(rid, client, "job:" + endpoint, len(contents), time.monotonic() - t0, e.status_code, error=e.detail)
        received.add(job["id"], client, "job:" + endpoint, job.get("filename"), contents,
                     error={"status": e.status_code, "detail": e.detail}, seconds=time.monotonic() - t0)
        live.end(job["id"], "error", e.detail)
    except Exception:
        logger.exception("[%s] ERROR interno en trabajo asíncrono", rid)
        job.update(status="error", finished=time.time(), error={"status": 500, "detail": "Error interno procesando el documento"})
        history.record(rid, client, "job:" + endpoint, len(contents), time.monotonic() - t0, 500, error="error interno")
        received.add(job["id"], client, "job:" + endpoint, job.get("filename"), contents,
                     error={"status": 500, "detail": "Error interno procesando el documento"}, seconds=time.monotonic() - t0)
        live.end(job["id"], "error", "Error interno")
    finally:
        progress.set_callback(None)
        live.detach()
        release_job(client)


def get(job_id: str, client: str) -> Optional[Dict[str, Any]]:
    """El trabajo si existe y pertenece a esa app (la app de tránsito puede ver todos)."""
    with _lock:
        job = _jobs.get(job_id)
    if not job or (job["client"] != client and client != TRANSIT_CLIENT):
        return None
    return job


def cancel(job_id: str, client: str) -> bool:
    job = get(job_id, client)
    if not job:
        return False
    if job["status"] == "queued":
        job["cancelled"] = True
    with _lock:
        if job["finished"] or job["status"] == "queued":
            _jobs.pop(job_id, None)
    return True
