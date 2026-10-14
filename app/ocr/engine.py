"""Motores de OCR (RapidOCR) y su llamada protegida.

Hay dos modelos de reconocimiento de texto, y cada uso elige el que le conviene:
- "default" (chino+inglés, el que se validó con los formularios de tránsito): lee mejor la letra a mano y los
  formularios impresos de tránsito, pero escribe "Senor" y "Ano" (pierde acentos y eñes).
- "latin" (PP-OCRv5 latino, opcional: `python tools/download_models.py`): conserva acentos, eñes y espacios entre
  palabras, ideal para documentos generales; con los formularios manuscritos de tránsito leyó peor.
"""
import logging
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger("document-api.ocr")

LATIN_MODEL = Path(os.getenv("OCR_LATIN_MODEL") or Path(__file__).resolve().parent.parent.parent
                   / "models_extra" / "latin_PP-OCRv5_rec_mobile_infer.onnx")

_engines: Dict[str, RapidOCR] = {"default": RapidOCR()}
if LATIN_MODEL.is_file():
    try:
        _engines["latin"] = RapidOCR(rec_model_path=str(LATIN_MODEL))
        logger.info("OCR: modelo latino disponible (%s) para documentos generales", LATIN_MODEL.name)
    except Exception:
        logger.exception("No se pudo cargar el modelo latino; los documentos generales usarán el modelo por defecto")
else:
    logger.info("OCR: sin modelo latino (acentos y eñes se pierden en documentos generales): tools/download_models.py")

# Motor del hilo/tarea actual. Por defecto el validado con los documentos de tránsito.
_current: ContextVar[str] = ContextVar("ocr_engine", default="default")


@contextmanager
def use_engine(name: str):
    """Usa el motor `name` ("default" o "latin") mientras dure el bloque; si no existe, se queda el actual."""
    token = _current.set(name if name in _engines else _current.get())
    try:
        yield
    finally:
        _current.reset(token)


# El motor comparte estado interno entre llamadas (umbrales del detector): se serializa con un lock
_ocr_lock = threading.Lock()
OCR_DEFAULT: Dict[str, float] = {"box_thresh": 0.5, "unclip_ratio": 1.6, "text_score": 0.5}
OCR_SENSITIVE: Dict[str, float] = {"box_thresh": 0.3, "unclip_ratio": 2.0, "text_score": 0.3}  # imágenes borrosas

# Resultado crudo del OCR: lista de (puntos 4x2, texto, score)
OcrResult = List[Tuple[np.ndarray, str, float]]


def run_ocr(img: np.ndarray, cls: bool = True, params: Dict[str, float] = OCR_DEFAULT) -> OcrResult:
    """Ejecuta el OCR. `cls=False` desactiva el clasificador de 180° (útil para detectar páginas al revés)."""
    try:
        with _ocr_lock:
            res = _engines.get(_current.get(), _engines["default"])(img, use_cls=cls, **params)
    except Exception:
        logger.exception("Falló el OCR")
        return []
    items = res[0] if isinstance(res, (tuple, list)) and res else None
    out: OcrResult = []
    for it in items or []:
        try:
            text = str(it[1]).strip()
            if text:
                out.append((np.array(it[0], dtype=np.float32), text, float(it[2])))
        except (TypeError, ValueError, IndexError):
            continue
    return out
