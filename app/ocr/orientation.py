"""Corrección de orientación: sentido (0°/180°) e inclinación de cualquier ángulo, incluido texto vertical."""
import math
from typing import Tuple

import cv2
import numpy as np

from app.ocr.engine import OcrResult, run_ocr
from app.ocr.preprocess import rotate
from app.ocr.quality import is_good, quality


def text_angle(res: OcrResult) -> float:
    """Inclinación dominante de las líneas de texto, en grados (-90, 90]. Media circular ponderada."""
    zs = []
    for pts, _, _ in res:
        e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
        l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
        e, long_, short = (e1, l1, l2) if l1 >= l2 else (e2, l2, l1)
        if short <= 0 or long_ / short < 1.8:  # cajas casi cuadradas no definen dirección
            continue
        a = math.atan2(float(e[1]), float(e[0]))
        zs.append(long_ * complex(math.cos(2 * a), math.sin(2 * a)))
    if not zs:
        return 0.0
    z = sum(zs)
    return math.degrees(math.atan2(z.imag, z.real) / 2)


def orient(img: np.ndarray) -> Tuple[np.ndarray, OcrResult, bool]:
    """Elige el sentido correcto (0° o 180°) y endereza cualquier inclinación.
    Se evalúa SIN el clasificador de 180°: una página al revés se lee como basura y se descarta."""
    moved = False  # ¿se giró la imagen? Si no, la lectura de sondeo sirve como lectura final
    best_img, best_res = img, run_ocr(img, cls=False)
    best_q = quality(best_res)
    if not is_good(best_res):
        flipped = cv2.rotate(img, cv2.ROTATE_180)
        res = run_ocr(flipped, cls=False)
        if quality(res) > best_q:
            best_img, best_res, best_q, moved = flipped, res, quality(res), True

    angle = text_angle(best_res) if best_res else 0.0
    if abs(angle) > 45 and angle < 0:  # texto vertical: el detector lo lee bien de arriba hacia abajo
        angle += 180
    # Una inclinación pequeña no afecta al OCR si la lectura ya es buena: enderezar costaría otro OCR completo
    if abs(angle) >= (4.0 if is_good(best_res) else 0.7):
        lev = rotate(best_img, angle)
        res = run_ocr(lev, cls=False)
        resid = text_angle(res) if res else 0.0
        if 1.5 <= abs(resid) <= 45:  # ajuste fino
            lev = rotate(lev, resid)
            res = run_ocr(lev, cls=False)
        if quality(res) > 0.5 * best_q:
            best_img, best_res, best_q, moved = lev, res, quality(res), True

    if not is_good(best_res):  # tras enderezar pudo quedar al revés
        flipped = cv2.rotate(best_img, cv2.ROTATE_180)
        res = run_ocr(flipped, cls=False)
        if quality(res) > best_q * 1.15:
            best_img, best_res, moved = flipped, res, True
    return best_img, best_res, moved
