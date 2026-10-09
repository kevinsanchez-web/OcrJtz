"""Endpoints HTTP."""
import logging
import os
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.config import LOG_FILENAMES, MAX_CONCURRENT_JOBS, MAX_UPLOAD_BYTES
from app.schemas import AnalyzeDocumentResponse
from app.security import job_slots, require_api_key
from app.services.analyzer import AnalysisError, analyze_file

logger = logging.getLogger("document-api")
router = APIRouter()


@router.get("/health", include_in_schema=False)
def health():
    """Chequeo de vida para el balanceador/Docker. No expone datos."""
    return {"status": "ok"}


# Ruta canónica + alias (ocultos del esquema OpenAPI) para compatibilidad con clientes existentes
_ROUTE = dict(response_model=AnalyzeDocumentResponse, dependencies=[Depends(require_api_key)])


@router.post("/api/v1/analyze-document", **_ROUTE)
@router.post("/api/v1/analyze-document/", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze-vision", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze-vision/", include_in_schema=False, **_ROUTE)
@router.post("/api/v1/analyze", include_in_schema=False, **_ROUTE)
@router.post("/analyze", include_in_schema=False, **_ROUTE)
def analyze_document(request: Request, file: UploadFile = File(...)):
    # def síncrono: FastAPI lo ejecuta en un threadpool y el OCR no bloquea el event loop
    rid = uuid.uuid4().hex[:6]
    ip = request.client.host if request.client else "?"
    ext = os.path.splitext(file.filename or "")[1].lower() or "sin extensión"  # solo informativo: el tipo se detecta por contenido

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    logger.info("[%s] PETICION recibida de %s: %s, %.0f KB%s", rid, ip, ext, len(contents) / 1024,
                f", archivo={file.filename!r}" if LOG_FILENAMES else "")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    if not job_slots.acquire(blocking=False):
        logger.warning("[%s] Servidor ocupado (%d trabajos en curso), rechazada con 503", rid, MAX_CONCURRENT_JOBS)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor ocupado, reintenta en unos segundos",
            headers={"Retry-After": "5"},
        )
    t0 = time.monotonic()
    logger.info("[%s] PROCESANDO...", rid)
    try:
        detected, warnings = analyze_file(contents, rid)
        resumen = ", ".join(f"{d.documentType} ({d.confidence:.2f})" for d in detected) or "ninguno reconocido"
        logger.info("[%s] TERMINADO en %.1fs -> %d documento(s): %s", rid, time.monotonic() - t0, len(detected), resumen)
        for w in warnings:
            logger.warning("[%s] AVISO: %s", rid, w)
        return AnalyzeDocumentResponse(documents=detected, warnings=warnings)
    except AnalysisError as e:
        logger.warning("[%s] ERROR %s: %s", rid, e.status_code, e.detail)
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception:
        # No se registra el nombre del archivo: puede contener datos personales
        logger.exception("[%s] ERROR interno procesando documento", rid)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno procesando el documento",
        )
    finally:
        job_slots.release()
