"""Fábrica de la aplicación FastAPI."""
import logging

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.config import ALLOWED_HOSTS, ENABLE_DOCS, LLM_MODE, LLM_MODEL, LLM_URL, VLM_MODEL
from app.security import security_headers


logger = logging.getLogger("document-api")


def create_app() -> FastAPI:
    if LLM_URL:
        logger.info("Respaldo con modelo local: ACTIVADO (%s, modelo %s, servidor %s)",
                    "visión/VLM" if LLM_MODE == "vision" else "texto", VLM_MODEL if LLM_MODE == "vision" else LLM_MODEL, LLM_URL)
    else:
        logger.info("Respaldo con modelo local: DESACTIVADO (define LLM_URL para activarlo)")
    application = FastAPI(
        title="Document Analysis & Extraction API",
        version="1.0.0",
        description="API de análisis e identificación de documentos vehiculares y personales.",
        docs_url="/docs" if ENABLE_DOCS else None,
        redoc_url="/redoc" if ENABLE_DOCS else None,
        openapi_url="/openapi.json" if ENABLE_DOCS else None,
    )
    # Sin CORS a propósito: la consumen servidores, no navegadores.
    if ALLOWED_HOSTS != ["*"]:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    application.middleware("http")(security_headers)
    application.include_router(router)
    return application


app = create_app()
