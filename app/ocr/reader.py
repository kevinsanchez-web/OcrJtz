"""Lectura de imágenes: genera lecturas cada vez más costosas hasta obtener una buena."""
import logging
from typing import Iterator, List, Tuple

import numpy as np

from app.ocr.boxes import OcrBox, boxes_to_text, to_boxes
from app.ocr.engine import OCR_DEFAULT, OCR_SENSITIVE, OcrResult, run_ocr
from app.ocr.orientation import orient
from app.ocr.preprocess import enhance, enhance_clahe, enhance_unsharp, load_image
from app.ocr.quality import is_good, quality, stats

logger = logging.getLogger("document-api.ocr")

# Mejoras a probar, en orden, cuando la lectura directa no es buena
_FALLBACKS = (
    (enhance, OCR_DEFAULT),
    (enhance, OCR_SENSITIVE),
    (enhance_clahe, OCR_SENSITIVE),
    (enhance_unsharp, OCR_DEFAULT),
)


def candidates(base: np.ndarray) -> Iterator[Tuple[OcrResult, np.ndarray, np.ndarray]]:
    """Genera (lectura, imagen enderezada, imagen leída: las cajas están en sus coordenadas) de la más barata a la más costosa (el consumidor decide cuándo parar)."""
    oriented, probe, moved = orient(base)
    # Página derecha y bien leída: el sondeo ya es la lectura final (evita repetir un OCR completo)
    if not moved and is_good(probe):
        yield probe, oriented, oriented
    else:
        yield run_ocr(oriented, cls=True), oriented, oriented
    for improve, params in _FALLBACKS:
        improved = improve(oriented)
        res = run_ocr(improved, cls=True, params=params)
        if stats(res)[0] < 20:  # casi nada leído: la orientación pudo fallar por la borrosidad
            improved, _, _ = orient(improve(base))
            res = run_ocr(improved, cls=True, params=params)
        yield res, oriented, improved


def read_image(base: np.ndarray) -> OcrResult:
    """Mejor lectura por calidad de OCR (se detiene en cuanto una es buena)."""
    best_res: OcrResult = []
    best_q = -1.0
    for res, _, _ in candidates(base):
        q = quality(res)
        if q > best_q:
            best_res, best_q = res, q
        if is_good(res):
            break
    return best_res


def process_image_bytes(img_bytes: bytes) -> Tuple[str, float, List[OcrBox]]:
    """OCR robusto (orientación, inclinación, borrosidad). Devuelve (texto, score medio 0..1, cajas)."""
    base = load_image(img_bytes)
    if base is None:
        return "", 0.0, []
    try:
        res = read_image(base)
    except Exception:
        logger.exception("Falló la lectura de la imagen")
        return "", 0.0, []
    boxes = to_boxes(res)
    return boxes_to_text(boxes), stats(res)[1], boxes
