"""Líneas de texto detectadas con su posición, y reconstrucción del texto por renglones."""
from dataclasses import dataclass
from typing import List

import numpy as np

from app.ocr.engine import OcrResult
from app.text_utils import fix_ocr_zeros


@dataclass
class OcrBox:
    """Una línea de texto detectada, con su posición en la imagen."""
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def to_boxes(res: OcrResult) -> List[OcrBox]:
    return [OcrBox(fix_ocr_zeros(t), sc, float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max()))
            for p, t, sc in res]


def boxes_to_text(boxes: List[OcrBox]) -> str:
    """Reconstruye el texto por renglones usando la posición de cada línea."""
    if not boxes:
        return ""
    med_h = float(np.median([b.h for b in boxes])) or 1.0
    rows: List[List[OcrBox]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if rows and abs(b.cy - float(np.mean([r.cy for r in rows[-1]]))) < 0.55 * med_h:
            rows[-1].append(b)
        else:
            rows.append([b])
    return "\n".join(" ".join(b.text for b in sorted(r, key=lambda b: b.x0)) for r in rows)
