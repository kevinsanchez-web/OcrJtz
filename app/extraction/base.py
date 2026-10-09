"""Construcción del resultado de un documento, común a todos los extractores."""
import logging
from typing import Any, Dict, List

from app.config import MIN_CONFIDENCE
from app.extraction.validators import sanitize
from app.schemas import DocumentItem

logger = logging.getLogger("document-api.extraction")


def build_item(doc_type: str, label: str, extracted: Dict[str, Any],
               core_fields: List[str], ocr_score: float) -> DocumentItem:
    """Confianza calculada: proporción de campos clave extraídos, ponderada por el score del OCR."""
    extracted = sanitize(extracted)  # un dato con forma incorrecta es peor que un dato vacío
    filled = sum(1 for k in core_fields if extracted.get(k))
    ratio = filled / len(core_fields) if core_fields else 0.0
    confidence = round(min(1.0, ratio * ocr_score), 2)
    missing = [k for k in core_fields if not extracted.get(k)]
    if missing or ocr_score < 0.8:
        logger.warning("Posible fallo en %s: OCR score %.2f, campos vacios: %s",
                       doc_type, ocr_score, ", ".join(missing) or "ninguno")
    return DocumentItem(
        found=confidence >= MIN_CONFIDENCE,
        documentType=doc_type,
        extractedData=extracted,
        confidence=confidence,
        summary=f"{label}: {filled}/{len(core_fields)} campos clave extraídos.",
    )
