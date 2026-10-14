"""Casillas marcadas del formulario RUNT: detecta qué trámite tiene una X o un trazo sobre su celda.

El OCR no lee marcas, y un modelo de visión pequeño elige una opción de la lista impresa aunque no haya
ninguna marcada. Aquí se mide la imagen: se ubica la rejilla "3. TRÁMITE SOLICITADO" (18 celdas en 3 filas
y 6 columnas), se borran los textos impresos y las líneas de la tabla, y una celda cuenta como marcada si
conserva un trazo notablemente más grande que los números impresos de las demás.
"""
import re
from typing import List, Optional

import cv2
import numpy as np

from app.ocr.boxes import OcrBox

# Opciones del formulario, en el orden de sus celdas (fila a fila)
TRAMITES = ["MATRICULA/REGISTRO", "TRASPASO", "TRASLADO MATRICULA/REGISTRO", "RADICADO MATRICULA/REGISTRO",
            "CAMBIO DE COLOR", "CAMBIO DE SERVICIO", "REGRABAR MOTOR", "REGRABAR CHASIS", "TRANSFORMACION",
            "DUPLICADO LICENCIA TRANSITO", "INSCRIPCION PRENDA", "LEVANTAMIENTO PRENDA",
            "CANCELACION MATRICULA/REGISTRO", "CAMBIO DE PLACAS", "DUPLICADO DE PLACAS", "REMATRICULA",
            "CAMBIO DE CARROCERIA", "OTROS"]
COLUMNS, ROWS = 6, 3
MARK_FACTOR = 1.1  # un trazo cuenta como marca si su lado mayor supera 1.1 veces el alto del texto impreso


def _sq(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def detect_tramite(boxes: List[OcrBox], img: np.ndarray) -> Optional[str]:
    """Trámite(s) marcado(s) en la rejilla, o None si no hay marca o la rejilla no se puede ubicar con certeza."""
    head = next((b for b in boxes if "tramitesolicitado" in _sq(b.text)), None)
    foot = next((b for b in boxes if "clasedevehiculo" in _sq(b.text)), None)
    if not head or not foot or foot.y0 <= head.y1:
        return None
    y0, y1 = head.y1, foot.y0
    inside = [b for b in boxes if y0 - 5 <= b.cy <= y1 and not re.fullmatch(r"\W*\d{1,2}\W*", b.text)]
    if not inside:
        return None
    # Columnas: agrupar los centros de los textos por cercanía horizontal
    xs = sorted(b.cx for b in inside if b.x0 < head.x0 + 0.45 * img.shape[1])
    if not xs:
        return None
    groups = [[xs[0]]]
    for v in xs[1:]:
        (groups[-1].append(v) if v - groups[-1][-1] < 0.03 * img.shape[1] else groups.append([v]))
    centers = [float(np.mean(g)) for g in groups][:COLUMNS]  # más allá de la 6.ª columna hay otros recuadros
    if len(centers) != COLUMNS:
        return None  # no es la rejilla de 6 columnas: mejor no adivinar
    mids = [(centers[i] + centers[i + 1]) / 2 for i in range(COLUMNS - 1)]
    edges = [head.x0 - 10] + mids + [centers[-1] + (centers[-1] - mids[-1])]

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    long_h = max(30, int(0.02 * img.shape[1]))
    lines = cv2.bitwise_or(
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (long_h, 1))),
        cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, long_h))))
    ink = cv2.bitwise_and(bw, cv2.bitwise_not(cv2.dilate(lines, np.ones((5, 5), np.uint8))))
    for b in boxes:  # el texto impreso no es una marca (se borra solo el interior: un trazo que lo cruza sobrevive)
        ink[max(0, int(b.y0) + 3):int(b.y1) - 3, max(0, int(b.x0) + 3):int(b.x1) - 3] = 0

    text_h = float(np.median([b.h for b in inside]))
    row_h = (y1 - y0) / ROWS
    marked = []
    for r in range(ROWS):
        for c in range(COLUMNS):
            ya, yb, xa, xb = y0 + r * row_h, y0 + (r + 1) * row_h, edges[c], edges[c + 1]
            iy, ix = 0.06 * (yb - ya), 0.06 * (xb - xa)
            cell = ink[int(ya + iy):int(yb - iy), int(xa + ix):int(xb - ix)]
            if cell.size == 0:
                continue
            n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(cell, np.ones((5, 5), np.uint8)))
            biggest = max((max(stats[i, 2], stats[i, 3]) for i in range(1, n)), default=0)
            if biggest >= MARK_FACTOR * text_h:
                marked.append((biggest, TRAMITES[r * COLUMNS + c]))
    if not marked or len(marked) > 3:  # demasiadas "marcas" = suciedad o la rejilla mal ubicada
        return None
    return " / ".join(name for _, name in sorted(marked, reverse=True))
