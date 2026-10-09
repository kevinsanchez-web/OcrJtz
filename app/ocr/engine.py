"""Motor de OCR (RapidOCR) y su llamada protegida."""
import logging
import threading
from typing import Dict, List, Tuple

import numpy as np
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger("document-api.ocr")

ocr_engine = RapidOCR()

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
            res = ocr_engine(img, use_cls=cls, **params)
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
