"""Formatos de imagen extra: HEIC/HEIF (las fotos de los iPhone). Importar este módulo basta para que Pillow los abra."""
import logging

logger = logging.getLogger("document-api")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # sin la librería la API sigue funcionando, solo que rechaza los HEIC
    HEIC_SUPPORTED = False
    logger.warning("pillow-heif no está instalado: las fotos HEIC/HEIF no se podrán leer")
