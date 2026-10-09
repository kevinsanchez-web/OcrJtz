"""Clasificación y extracción: prueba cada tipo de documento y devuelve el de mayor confianza.

Para añadir un tipo nuevo: crea su módulo en esta carpeta (ver factura.py) y agrégalo a PARSERS.
"""
import logging
from typing import List, Optional

from app.config import LOG_OCR_TEXT
from app.extraction import cedula, factura, gases, poder, runt
from app.extraction.cedula import parse_cedula
from app.extraction.factura import parse_factura
from app.extraction.gases import parse_gases
from app.extraction.poder import parse_poder
from app.extraction.runt import parse_runt
from app.llm.refine import CORE_FIELDS as LLM_CORE_FIELDS
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import fix_ocr_zeros

logger = logging.getLogger("document-api.extraction")

PARSERS = (parse_factura, parse_runt, parse_gases, parse_cedula, parse_poder)
LLM_CORE_FIELDS.update({"cedula": cedula.CORE_FIELDS, "factura": factura.CORE_FIELDS, "gases": gases.CORE_FIELDS,
                        "poder": poder.CORE_FIELDS, "runt": runt.CORE_FIELDS})


def parse_page_text(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    """Ejecuta todos los extractores cuyo disparador coincide y devuelve el de mayor confianza."""
    candidates = []
    text = fix_ocr_zeros(text)
    if LOG_OCR_TEXT:
        logger.info("TEXTO OCR (score %.2f): %r", ocr_score, text[:3000])
    for parser in PARSERS:
        item = parser(text, ocr_score, boxes)
        if item and item.found:
            candidates.append(item)
    if not candidates:
        logger.warning("Ningun tipo de documento reconocido (%d caracteres de texto, OCR score %.2f)",
                       len(text), ocr_score)
    return max(candidates, key=lambda i: i.confidence) if candidates else None
