"""Tablas: devuelve filas y columnas en vez de líneas de texto sueltas.

- PDF digital: usa el detector de tablas de PyMuPDF (`find_tables`), que lee las celdas reales.
- Imagen o PDF escaneado: agrupa las líneas leídas por renglón y deduce las columnas por los huecos verticales
  que se repiten en tres o más renglones seguidos.
"""
import logging
from typing import List, Optional

import numpy as np

from app.ocr.boxes import OcrBox
from app.schemas import Table

logger = logging.getLogger("document-api")

MIN_ROWS = 3
MIN_COLS = 2


def _rows(boxes: List[OcrBox], med_h: float) -> List[List[OcrBox]]:
    rows: List[List[OcrBox]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if rows and abs(b.cy - float(np.mean([r.cy for r in rows[-1]]))) < 0.55 * med_h:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [sorted(r, key=lambda b: b.x0) for r in rows]


def _columns(run: List[List[OcrBox]], med_h: float) -> List[List[float]]:
    """Intervalos horizontales de las columnas: huecos verticales vacíos en todos los renglones del tramo."""
    spans = sorted((b.x0, b.x1) for row in run for b in row)
    cols: List[List[float]] = []
    for x0, x1 in spans:
        if cols and x0 <= cols[-1][1] + 0.8 * med_h:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    return cols


def tables_from_boxes(boxes: List[OcrBox], width: float, height: float) -> List[Table]:
    """Tablas deducidas de la posición de las líneas leídas por OCR."""
    if len(boxes) < MIN_ROWS * MIN_COLS:
        return []
    med_h = float(np.median([b.h for b in boxes])) or 1.0
    rows = _rows(boxes, med_h)
    out: List[Table] = []
    i = 0
    while i < len(rows):
        if len(rows[i]) < MIN_COLS:
            i += 1
            continue
        j = i
        while j + 1 < len(rows) and len(rows[j + 1]) >= MIN_COLS and \
                rows[j + 1][0].cy - rows[j][0].cy < 4.0 * med_h:  # renglones consecutivos, sin saltos grandes
            j += 1
        run = rows[i:j + 1]
        i = j + 1
        if len(run) < MIN_ROWS:
            continue
        cols = _columns(run, med_h)
        if not (MIN_COLS <= len(cols) <= 12):
            continue
        cells = []
        for row in run:
            line = [""] * len(cols)
            for b in row:
                k = next((n for n, (a, z) in enumerate(cols) if a - 1 <= b.cx <= z + 1), None)
                if k is not None:
                    line[k] = (line[k] + " " + b.text).strip()
            cells.append(line)
        filled = sum(1 for r in cells if sum(1 for c in r if c) >= 2)
        if filled < 0.7 * len(cells):
            continue  # texto en columnas de un periódico, no una tabla
        x0, y0 = min(b.x0 for r in run for b in r), min(b.y0 for r in run for b in r)
        x1, y1 = max(b.x1 for r in run for b in r), max(b.y1 for r in run for b in r)
        out.append(Table(rows=cells, source="aligned",
                         box=[round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4)]))
    return out


def tables_from_pdf_page(page) -> List[Table]:
    """Tablas de una página de PDF digital (celdas reales), o [] si PyMuPDF no las encuentra."""
    try:
        found = page.find_tables()
    except Exception:
        return []
    w, h = page.rect.width or 1.0, page.rect.height or 1.0
    out: List[Table] = []
    for t in getattr(found, "tables", []) or []:
        try:
            rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in t.extract()]
        except Exception:
            continue
        rows = [r for r in rows if any(r)]
        if len(rows) >= 2 and max(len(r) for r in rows) >= MIN_COLS:
            x0, y0, x1, y1 = t.bbox
            out.append(Table(rows=rows, source="grid", box=[round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]))
    return out
