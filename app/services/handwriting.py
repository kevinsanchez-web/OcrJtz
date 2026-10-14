"""Recupera el contacto escrito a mano (celular, correo, dirección) en las páginas donde el OCR de página
completa se saltó alguna línea.

Se ubica el bloque por las líneas que sí se leyeron (el celular, la dirección), se recorta esa franja y se
lee de nuevo ampliada; el OCR rinde mucho mejor sobre un recorte que sobre la página entera. Si hay modelo de
visión (VLM) se le pide también la transcripción de la franja, que acierta más con letra manuscrita.
"""
import logging
import re
from collections import Counter
from typing import List, Optional

import cv2
import numpy as np

from app.config import VLM_MODEL
from app.extraction.contact import (CONTACT_FIELDS, find_address, find_email, find_phone, v_address, v_email, v_phone)
from app.llm import client
from app.llm.refine import _encode_image
from app.ocr.boxes import OcrBox, boxes_to_text, to_boxes
from app.ocr.engine import OCR_DEFAULT, OCR_SENSITIVE, run_ocr
from app.schemas import DocumentItem

logger = logging.getLogger("document-api.extraction")

_SCHEMA = {"type": "object",
           "properties": {f: {"type": "string"} for f in CONTACT_FIELDS},
           "required": list(CONTACT_FIELDS)}
_PROMPT = ("La imagen muestra líneas escritas a mano con datos de contacto: Celular, Correo y Dirección. "
           "Transcribe exactamente lo escrito en cada una. Si una línea no existe o no se lee, deja la cadena vacía.")


def _anchors(boxes: List[OcrBox]) -> List[OcrBox]:
    """Líneas ya leídas que pertenecen al bloque de contacto: celular, dirección, correo o sus etiquetas."""
    out = []
    for b in boxes:
        t = b.text
        if (find_phone(t) or "#" in t and re.search(r"\d", t) or "@" in t
                or re.match(r"^\W*(?:correo|e-?mail|direcci|celular|cel\b|dvecn|drecon)", t, re.IGNORECASE)):
            out.append(b)
    return out


def _band(img: np.ndarray, anchors: List[OcrBox]) -> Optional[np.ndarray]:
    h = max(b.h for b in anchors)
    x0 = max(0, int(min(b.x0 for b in anchors) - 3 * h))
    x1 = min(img.shape[1], int(max(b.x1 for b in anchors) + 3 * h))
    y0 = max(0, int(min(b.y0 for b in anchors) - 3 * h))
    y1 = min(img.shape[0], int(max(b.y1 for b in anchors) + 3 * h))
    if x1 - x0 < 40 or y1 - y0 < 20:
        return None
    return img[y0:y1, x0:x1]


def recover_contact(item: DocumentItem, boxes: List[OcrBox], img: np.ndarray, use_vlm: bool = True) -> None:
    """Completa en el sitio `celular`, `correo` y `direccion` de `item` si faltan. Nunca lanza excepciones."""
    data = item.extractedData
    wanted = [f for f in CONTACT_FIELDS if f in data and not data.get(f)]
    check_phone = "celular" in data and bool(data.get("celular"))  # un celular leído de la página se verifica en la franja
    if not wanted and not check_phone:
        return
    try:
        anchors = _anchors(boxes)
        if not anchors:
            return  # no hay bloque de contacto manuscrito en esta página
        crop = _band(img, anchors)
        if crop is None:
            return
        # 1. OCR del recorte ampliado
        big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        sub = to_boxes(run_ocr(big, cls=False, params=OCR_SENSITIVE))
        text = boxes_to_text(sub)
        exclude = data.get("numeroDocumento")
        found = {"celular": find_phone(text, exclude), "correo": find_email(text), "direccion": find_address(sub)}
        # Un dígito manuscrito mal leído invalida el celular: se lee la franja a varias escalas y gana la lectura más repetida
        votes = Counter(v for v in [found["celular"]] if v)
        for scale, params in ((1.5, OCR_SENSITIVE), (2.5, OCR_SENSITIVE), (1.0, OCR_DEFAULT), (2.0, OCR_DEFAULT)):
            try:
                resized = crop if scale == 1.0 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                extra = to_boxes(run_ocr(resized, cls=False, params=params))
                v = find_phone(boxes_to_text(extra), exclude)
                if v:
                    votes[v] += 1
            except Exception:
                logger.exception("Falló una lectura adicional de la franja")
        if votes:
            found["celular"] = votes.most_common(1)[0][0]
            if check_phone and found["celular"] != data.get("celular") and votes.most_common(1)[0][1] >= 2:
                logger.info("Celular corregido con la lectura de la franja (%d de %d lecturas coinciden)",
                            votes.most_common(1)[0][1], sum(votes.values()))
                data["celular"] = found["celular"]
        # 2. Modelo de visión sobre la franja: transcribe mejor la letra manuscrita
        vlm = _ask_vlm(crop) if use_vlm and "correo" in wanted else {}
        if vlm.get("correo"):
            found["correo"] = vlm["correo"]  # el OCR acierta el dominio pero falla en el nombre de usuario
        for f in ("celular", "direccion"):
            found[f] = found[f] or vlm.get(f)
        for f in wanted:
            if found.get(f):
                data[f] = found[f]
                logger.info("Contacto manuscrito recuperado: %s", f)
    except Exception:
        logger.exception("Falló la recuperación del contacto manuscrito")


def _ask_vlm(crop: np.ndarray) -> dict:
    if not client.enabled():
        return {}
    out = client.chat_json(_PROMPT, _SCHEMA, model=VLM_MODEL, images=[_encode_image(crop)]) or {}
    clean = {"celular": v_phone(str(out.get("celular", ""))), "correo": v_email(str(out.get("correo", ""))),
             "direccion": v_address(str(out.get("direccion", "")))}
    return {k: v for k, v in clean.items() if v}
