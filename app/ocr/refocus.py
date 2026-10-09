"""Segunda lectura de los fragmentos dudosos: recorta cada dato poco fiable (cédula, fecha, chasis...), lo
amplía y lo vuelve a leer. Un recorte pequeño y ampliado se lee mejor que la misma línea dentro de una página
entera. Solo se relee lo dudoso, para no alargar el tiempo de respuesta."""
import logging
import re
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.ocr.boxes import OcrBox, to_boxes
from app.ocr.engine import OCR_DEFAULT, OCR_SENSITIVE, run_ocr

logger = logging.getLogger("document-api.ocr")

DATA_SCORE = 0.95       # datos (números, códigos) con menos confianza que esto se releen; el texto corriente no
MAX_CROPS = 8
TARGET_HEIGHT = 90      # alto en píxeles al que se amplía el texto del recorte
MAX_SECONDS = 6.0


def _digits(text: str) -> int:
    return len(re.findall(r"\d", text))


def _looks_like_data(text: str) -> bool:
    """Identificaciones, fechas, chasis, placas: un solo carácter mal leído las invalida."""
    return _digits(text) >= 4 or bool(re.search(r"[A-Z]{3}\s?\d{2}[A-Z\d]\b", text))


def _keeps_digits(old: str, new: str) -> bool:
    """En un dato numérico, una relectura que pierde dígitos suele ser un recorte cortado: se descarta."""
    return not _looks_like_data(old) or _digits(new) >= _digits(old)


def _crop(img: np.ndarray, b: OcrBox) -> Optional[np.ndarray]:
    h_img, w_img = img.shape[:2]
    pad_y, pad_x = max(4, int(0.35 * b.h)), max(6, int(0.5 * b.h))
    x0, x1 = max(0, int(b.x0) - pad_x), min(w_img, int(b.x1) + pad_x)
    y0, y1 = max(0, int(b.y0) - pad_y), min(h_img, int(b.y1) + pad_y)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
    k = min(5.0, max(1.0, TARGET_HEIGHT / max(b.h, 1.0)))
    if k > 1.05:
        gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.copyMakeBorder(gray, 24, 24, 24, 24, cv2.BORDER_REPLICATE)  # el detector rinde mejor con margen
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)


def _reread(img: np.ndarray, b: OcrBox) -> Optional[Tuple[str, float]]:
    """Mejor lectura del recorte entre dos variantes: (texto, score)."""
    crop = _crop(img, b)
    if crop is None:
        return None
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY))
    best: Optional[Tuple[str, float]] = None
    for v, params in ((crop, OCR_DEFAULT), (cv2.cvtColor(clahe, cv2.COLOR_GRAY2RGB), OCR_SENSITIVE)):
        boxes = sorted(to_boxes(run_ocr(v, cls=False, params=params)), key=lambda x: x.x0)
        if not boxes:
            continue
        text = " ".join(x.text for x in boxes)
        score = sum(x.score * len(x.text) for x in boxes) / max(sum(len(x.text) for x in boxes), 1)
        if best is None or score > best[1]:
            best = (text, score)
        if best[1] >= 0.93:  # lectura buena: no hace falta probar otra variante
            break
    return best


def refocus(img: np.ndarray, boxes: List[OcrBox]) -> Tuple[List[OcrBox], int]:
    """Devuelve (cajas con los textos dudosos corregidos, cuántos cambiaron). No modifica la lista original."""
    todo = sorted((b for b in boxes if _looks_like_data(b.text) and b.score < DATA_SCORE),
                  key=lambda b: b.score)[:MAX_CROPS]
    t0 = time.monotonic()
    out = list(boxes)
    changed = 0
    for b in todo:
        if time.monotonic() - t0 > MAX_SECONDS:
            break
        try:
            r = _reread(img, b)
        except Exception:
            logger.exception("Falló la relectura de un recorte")
            continue
        if not r:
            continue
        text, score = r
        idx = out.index(b)
        if text == b.text:
            out[idx] = OcrBox(b.text, max(b.score, score), b.x0, b.y0, b.x1, b.y1)  # dos lecturas coinciden
        elif score > b.score + 0.02 and 0.5 <= len(text) / max(len(b.text), 1) <= 1.6 and _keeps_digits(b.text, text):
            out[idx] = OcrBox(text, score, b.x0, b.y0, b.x1, b.y1)
            changed += 1
            logger.info("Relectura: %r (%.2f) -> %r (%.2f)", b.text[:40], b.score, text[:40], score)
    return out, changed
