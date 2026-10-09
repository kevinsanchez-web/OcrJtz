"""Extracción por posición: encuentra el valor de un campo junto a su etiqueta.

Funciona aunque el OCR pegue las palabras o lea mal la etiqueta. El valor puede estar a la derecha de la
etiqueta, debajo de ella (formularios) o encima (cédula nueva). Cuando hay varias etiquetas cerca, cada valor
se asigna a la etiqueta más cercana, así no se confunden dos campos contiguos.
"""
import re
from difflib import SequenceMatcher
from typing import Callable, List, Optional

from app.ocr.boxes import OcrBox
from app.text_utils import norm, squash


def match_label(box: OcrBox, alias: str) -> Optional[str]:
    """Si la caja empieza con la etiqueta `alias` (en minúsculas, sin espacios) devuelve el texto que
    sigue ('' si es solo la etiqueta). Devuelve None si la caja no es esa etiqueta.
    Admite un número de casilla delante (ej. '5.MARCA')."""
    n = norm(box.text)
    pat = r"^\W*(?:\d{1,2}\W{0,2})?" + r"[^a-z0-9]*".join(re.escape(c) for c in alias) + r"(?P<rest>.*)$"
    m = re.match(pat, n, re.S)
    if m:
        rest = box.text[m.start("rest"):]
        if len(alias) >= 4 or not rest[:1].isalpha():
            return rest.strip(" :.-\t")
    sq = squash(box.text)  # etiqueta con errores de OCR (ej. APELL1DOS)
    sq = re.sub(r"^\d{1,2}", "", sq)
    if len(sq) >= 4 and abs(len(sq) - len(alias)) <= 2 and SequenceMatcher(None, sq, alias).ratio() >= 0.82:
        return ""
    return None


def _aligned(lab: OcrBox, b: OcrBox) -> bool:
    """¿Están en la misma columna? (se solapan horizontalmente o empiezan casi en el mismo punto)"""
    return min(b.x1, lab.x1) - max(b.x0, lab.x0) > 0 or abs(b.x0 - lab.x0) <= 2 * lab.h


def _vgap(a: OcrBox, b: OcrBox) -> float:
    """Distancia vertical entre dos cajas (0 si se solapan)."""
    return max(b.y0 - a.y1, a.y0 - b.y1, 0.0)


def spatial_value(boxes: List[OcrBox], aliases: List[str],
                  validate: Callable[[str], Optional[str]], all_labels: List[str],
                  vertical: str = "both") -> Optional[str]:
    """Busca el valor de un campo cerca de su etiqueta. `validate` devuelve el valor limpio o None si el
    texto no tiene la forma esperada; `all_labels` son todas las etiquetas del documento (no son valores).
    `vertical`: "above" (valor encima de la etiqueta), "below" (debajo) o "both" (el más cercano)."""
    label_boxes, label_ids = [], set()
    for b in boxes:
        rests = [match_label(b, a) for a in all_labels]
        rests = [r for r in rests if r is not None]
        if rests:
            label_ids.add(id(b))        # una etiqueta no es un valor
            if "" in rests:             # solo las etiquetas sin valor en su caja compiten por los valores vecinos
                label_boxes.append(b)

    for lab in boxes:
        rest = None
        for a in aliases:
            rest = match_label(lab, a)
            if rest is not None:
                break
        if rest is None:
            continue
        if rest:
            v = validate(rest)
            if v:
                return v
        cands = []
        for b in boxes:
            if b is lab or id(b) in label_ids:
                continue
            hh = max(lab.h, b.h)
            if abs(b.cy - lab.cy) <= 0.6 * hh and b.x0 - lab.x1 >= -0.5 * lab.h:   # a la derecha
                cands.append((max(b.x0 - lab.x1, 0), b))
                continue
            # Verticales: la altura de la etiqueta es más fiable (las cajas de valor pueden salir infladas)
            below = lab.cy + 0.6 * lab.h < b.cy <= lab.cy + 5 * hh
            above = b.cy < lab.cy - 0.6 * lab.h and lab.cy - b.cy <= 5 * hh
            if vertical == "above":
                below = False
            elif vertical == "below":
                above = False
            if (below or above) and _aligned(lab, b):
                gap = _vgap(lab, b)
                # Modo "both": ¿otra etiqueta alineada con este valor está más cerca? Entonces es de ella
                if vertical == "both" and any(other is not lab and _aligned(other, b) and _vgap(other, b) < gap - 1e-6
                                              for other in label_boxes):
                    continue
                cands.append((gap * 1.5 + abs(b.x0 - lab.x0) * 0.3, b))
        for _, b in sorted(cands, key=lambda c: c[0]):
            v = validate(b.text)
            if v:
                return v
    return None
