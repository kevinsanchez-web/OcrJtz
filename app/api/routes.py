"""Endpoints HTTP."""
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, Response

from app import cache, history, jobs, live, received
from app.config import ENABLE_PANEL, KEEP_RECEIVED, LLM_MODE, LLM_URL, LOG_FILENAMES, MAX_CONCURRENT_JOBS, MAX_UPLOAD_BYTES, TRANSIT_CLIENT, VLM_MODEL
from app.schemas import AnalyzeDocumentResponse, JobStatus, OcrResponse
from app.security import acquire_job, client_ip, local_panel_only, release_job, require_api_key
from app.services.analyzer import LEGACY_MESSAGE, AnalysisError, analyze_file, detect_kind
from app.services.generic import ocr_file, pages_from_reads

logger = logging.getLogger("document-api")
router = APIRouter()


@router.get("/health", include_in_schema=False)
def health():
    """Chequeo de vida para el balanceador/Docker. No expone datos."""
    return {"status": "ok"}


_PANEL = Path(__file__).resolve().parent.parent / "web" / "panel.html"


@router.get("/panel", include_in_schema=False)
def web_panel():
    """Panel web para probar documentos y ver estadísticas desde cualquier dispositivo (pide la clave de la app)."""
    if not ENABLE_PANEL or not _PANEL.exists():
        raise HTTPException(status_code=404, detail="No encontrado")
    return HTMLResponse(_PANEL.read_text(encoding="utf-8"), headers={
        "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; style-src 'unsafe-inline'; "
                                   "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"})


@router.get("/api/v1/stats", include_in_schema=False, dependencies=[Depends(require_api_key)])
def stats(request: Request, recent: int = Query(15, ge=0, le=200)):
    """Estadísticas del historial. La app de tránsito ve todas las apps; las demás, solo las suyas."""
    client = getattr(request.state, "client", None)
    out = history.stats(None if client == TRANSIT_CLIENT else client, recent)
    out["vlm"] = {"enabled": bool(LLM_URL), "mode": LLM_MODE, "model": VLM_MODEL if LLM_MODE == "vision" else None}
    return out


_LOCAL = [Depends(require_api_key), Depends(local_panel_only)]


@router.get("/api/v1/live", include_in_schema=False, dependencies=_LOCAL)
def live_list():
    """Documentos en proceso y recién terminados, con sus etapas y el texto del VLM en vivo, para el panel de escritorio."""
    return {"items": live.snapshot()}


@router.get("/api/v1/received", include_in_schema=False, dependencies=_LOCAL)
def received_list():
    """Documentos recibidos recientes (metadatos), para el panel de escritorio. Requiere KEEP_RECEIVED=1."""
    return {"items": received.listing()}


@router.get("/api/v1/received/{rid}", include_in_schema=False, dependencies=_LOCAL)
def received_detail(rid: str):
    item = received.get(rid)
    if not item:
        raise HTTPException(status_code=404, detail="No encontrado o ya expiró")
    res = item["result"]
    return {"meta": received.meta(item), "result": res.model_dump() if res is not None else None, "error": item["error"]}


@router.get("/api/v1/received/{rid}/file", include_in_schema=False, dependencies=_LOCAL)
def received_file(rid: str):
    item = received.get(rid)
    if not item:
        raise HTTPException(status_code=404, detail="No encontrado o ya expiró")
    return Response(content=item["data"], media_type="application/octet-stream")


@router.delete("/api/v1/received", status_code=204, include_in_schema=False, dependencies=_LOCAL)
def received_clear():
    received.clear()
    live.clear()


def parse_fields(raw: Optional[str]) -> Tuple[str, ...]:
    """"total, fecha ,correo" -> ("total", "fecha", "correo") (máximo 20, sin repetidos)."""
    seen = []
    for f in (raw or "").split(","):
        f = f.strip()[:60]
        if f and f not in seen:
            seen.append(f)
    return tuple(seen[:20])


def _count(result) -> tuple:
    """(documentos, páginas) de una respuesta, para el historial."""
    docs = len(getattr(result, "documents", []) or [])
    pages = len(getattr(result, "pages", []) or []) or len({d.page for d in getattr(result, "documents", []) if d.page})
    return docs, pages


def _run_job(request: Request, file: UploadFile, endpoint: str, work: Callable[[bytes, str, bool], object],
             describe: Callable[[object], str], cache_parts: tuple = ()):
    """Recibe el archivo, aplica límites (tamaño, trabajos simultáneos) y ejecuta `work(contenido, id, es_transito)`.
    `es_transito`: la clave usada es la de la app de extracción de documentos de tránsito.
    Un archivo defectuoso nunca produce un 500: los errores atribuibles al archivo salen como 4xx."""
    rid = uuid.uuid4().hex[:6]
    ip = client_ip(request)
    client = getattr(request.state, "client", None) or TRANSIT_CLIENT
    is_transit = client == TRANSIT_CLIENT
    ext = os.path.splitext(file.filename or "")[1].lower() or "sin extensión"  # solo informativo: el tipo se detecta por contenido
    t0 = time.monotonic()

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    logger.info("[%s] PETICION de la app '%s' (%s): %s, %.0f KB%s", rid, client, ip, ext, len(contents) / 1024,
                f", archivo={file.filename!r}" if LOG_FILENAMES else "")
    if len(contents) > MAX_UPLOAD_BYTES:
        history.record(rid, client, endpoint, len(contents), 0, 413, error="archivo demasiado grande")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    cache_key = cache.make_key(contents, client, endpoint, *cache_parts)
    hit = cache.get(cache_key)
    if hit is not None:  # el mismo archivo ya se analizó hace poco: respuesta inmediata, sin gastar un cupo
        docs, pages = _count(hit)
        logger.info("[%s] RESULTADO desde caché (%.2fs)", rid, time.monotonic() - t0)
        history.record(rid, client, endpoint, len(contents), time.monotonic() - t0, 200, docs, pages, "desde caché")
        received.add(rid, client, endpoint, file.filename, contents, result=hit, cached=True)
        return hit

    if not acquire_job(client):
        logger.warning("[%s] Servidor ocupado (%d trabajos en curso), rechazada con 503", rid, MAX_CONCURRENT_JOBS)
        history.record(rid, client, endpoint, len(contents), 0, 503, error="servidor ocupado")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor ocupado, reintenta en unos segundos",
            headers={"Retry-After": "5"},
        )
    logger.info("[%s] PROCESANDO...", rid)
    live.begin(rid, client, endpoint, file.filename, len(contents))
    live.attach(rid)
    live.start(rid)
    try:
        result = work(contents, rid, is_transit)
        logger.info("[%s] TERMINADO en %.1fs -> %s", rid, time.monotonic() - t0, describe(result))
        for w in getattr(result, "warnings", []):
            logger.warning("[%s] AVISO: %s", rid, w)
        docs, pages = _count(result)
        history.record(rid, client, endpoint, len(contents), time.monotonic() - t0, 200, docs, pages)
        received.add(rid, client, endpoint, file.filename, contents, result=result, seconds=time.monotonic() - t0)
        live.end(rid, "done")
        cache.put(cache_key, result)
        return result
    except AnalysisError as e:
        logger.warning("[%s] ERROR %s: %s", rid, e.status_code, e.detail)
        history.record(rid, client, endpoint, len(contents), time.monotonic() - t0, e.status_code, error=e.detail)
        received.add(rid, client, endpoint, file.filename, contents, error={"status": e.status_code, "detail": e.detail},
                     seconds=time.monotonic() - t0)
        live.end(rid, "error", e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception:
        # No se registra el nombre del archivo: puede contener datos personales
        logger.exception("[%s] ERROR interno procesando documento", rid)
        history.record(rid, client, endpoint, len(contents), time.monotonic() - t0, 500, error="error interno")
        received.add(rid, client, endpoint, file.filename, contents,
                     error={"status": 500, "detail": "Error interno procesando el documento"}, seconds=time.monotonic() - t0)
        live.end(rid, "error", "Error interno")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno procesando el documento",
        )
    finally:
        live.detach()
        release_job(client)


def _job_view(job: dict) -> JobStatus:
    result = job["result"]
    start = job["started"] or job["created"]
    dump = result.model_dump() if result is not None else None
    if dump is not None and not job.get("lines"):  # las posiciones solo se calcularon para el panel local; no se pidieron
        if job["mode"] == "ocr":
            for page in dump.get("pages") or []:
                page["lines"] = None
        else:
            dump["pages"] = None
    return JobStatus(
        jobId=job["id"], status=job["status"], mode=job["mode"],
        progress={"page": job["done"], "total": job["total"]} if job["total"] else None,
        elapsedSeconds=round((job["finished"] or time.time()) - start, 1), cached=bool(job.get("cached")),
        result=dump, error=job["error"])


@router.post("/api/v1/jobs", response_model=JobStatus, status_code=202, dependencies=[Depends(require_api_key)])
def create_job(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Query("transit", pattern="^(transit|ocr)$",
                      description="transit = campos de documentos de tránsito; ocr = OCR general de cualquier documento"),
    structured: bool = Query(True, description="(solo ocr) añade campos de documentos conocidos"),
    lines: bool = Query(False, description="Incluye la posición de cada línea leída (`pages[].lines`)"),
    fields: Optional[str] = Query(None, description="(solo ocr) campos que quieres obtener, separados por comas"),
    deep: bool = Query(False, description="(solo ocr con fields) usa el modelo de visión para lo que no esté en el texto"),
):
    """Trabajo asíncrono: devuelve de inmediato un `jobId`; consulta `GET /api/v1/jobs/{jobId}` hasta que termine.
    Ideal para PDF de varias páginas y para redes que cortan las peticiones largas."""
    client = getattr(request.state, "client", None) or TRANSIT_CLIENT
    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"El archivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    kind = detect_kind(contents)
    if kind == "legacy":  # se rechaza ya, no tras esperar en la cola
        raise HTTPException(status_code=415, detail=LEGACY_MESSAGE)
    if kind is None:
        raise HTTPException(status_code=400, detail="El archivo no es un PDF, una imagen ni un documento de Office válido.")
    try:
        jid = jobs.submit(contents, client, mode, structured, lines, parse_fields(fields), deep, file.filename or "")
    except jobs.TooManyJobs:
        raise HTTPException(status_code=429, detail="Demasiados trabajos pendientes; espera a que terminen algunos",
                            headers={"Retry-After": "30"})
    logger.info("[%s] TRABAJO creado por la app '%s' (%s, %.0f KB)", jid[:6], client, mode, len(contents) / 1024)
    return _job_view(jobs.get(jid, client))


@router.get("/api/v1/jobs/{job_id}", response_model=JobStatus, dependencies=[Depends(require_api_key)])
def job_status(request: Request, job_id: str):
    """Estado de un trabajo: `queued`, `processing` (con `progress`), `done` (con `result`) o `error`."""
    job = jobs.get(job_id, getattr(request.state, "client", None) or TRANSIT_CLIENT)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado o ya expiró")
    return _job_view(job)


@router.delete("/api/v1/jobs/{job_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_job(request: Request, job_id: str):
    """Borra el resultado (o cancela el trabajo si aún está en cola)."""
    if not jobs.cancel(job_id, getattr(request.state, "client", None) or TRANSIT_CLIENT):
        raise HTTPException(status_code=404, detail="Trabajo no encontrado o ya expiró")


# Ruta canónica + alias (ocultos del esquema OpenAPI) para compatibilidad con clientes existentes
_ROUTE = dict(response_model=AnalyzeDocumentResponse, dependencies=[Depends(require_api_key)])


@router.post("/api/v1/analyze-document", **_ROUTE)
@router.post("/api/v1/analyze-document/", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze-vision", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze-vision/", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze", include_in_schema=False, **_ROUTE)
@router.post("/analyze", include_in_schema=False, **_ROUTE)
def analyze_document(request: Request, file: UploadFile = File(...),
                     lines: bool = Query(False, description="Incluye `pages` con la posición de cada línea leída")):
    """Documentos de tránsito (cédula, factura, RUNT, gases, poder): devuelve sus campos estructurados."""
    # def síncrono: FastAPI lo ejecuta en un threadpool y el OCR no bloquea el event loop
    want = lines or KEEP_RECEIVED  # el panel necesita las posiciones; al cliente se le quitan si no las pidió

    def work(contents: bytes, rid: str, is_transit: bool):
        reads: dict = {}
        detected, warnings = analyze_file(contents, rid, reads if want else None)  # formato de la app de tránsito, con cualquier clave
        return AnalyzeDocumentResponse(documents=detected, warnings=warnings,
                                       pages=pages_from_reads(reads, detected) if want else None)

    result = _run_job(request, file, "analyze-document", work,
                      lambda r: ", ".join(f"{d.documentType} ({d.confidence:.2f})"
                                          for d in r.documents) or "ningún documento reconocido",
                      cache_parts=(want,))
    return result if lines else result.model_copy(update={"pages": None})


@router.post("/api/v1/ocr", response_model=OcrResponse, dependencies=[Depends(require_api_key)])
@router.post("/api/v1/ocr/", include_in_schema=False, response_model=OcrResponse,
             dependencies=[Depends(require_api_key)])
def ocr_any_document(
    request: Request,
    file: UploadFile = File(...),
    structured: bool = Query(True, description="Añade los campos estructurados si el documento es de un tipo conocido"),
    lines: bool = Query(True, description="Incluye cada línea de texto con su posición (false = respuesta más liviana)"),
    fields: Optional[str] = Query(None, description="Campos que quieres obtener, separados por comas (ej. total,fecha,correo)"),
    deep: bool = Query(False, description="Con `fields`: usa el modelo de visión para los campos que no aparezcan en el texto (más lento, ~1 min por página)"),
):
    """OCR general: texto de CUALQUIER PDF o imagen, página por página, con la posición de cada línea.
    La app de tránsito recibe además sus campos con la forma que su interfaz espera (contacto anidado);
    las demás apps reciben los campos planos."""
    wanted = parse_fields(fields)
    return _run_job(request, file, "ocr", lambda c, rid, transit: ocr_file(c, rid, structured, lines, transit, list(wanted), deep),
                    lambda r: f"{len(r.pages)} página(s), {len(r.text)} caracteres, {len(r.documents)} documento(s) conocido(s)",
                    cache_parts=(structured, lines, wanted, deep))
