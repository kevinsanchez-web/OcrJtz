"""Poder especial para trámites de tránsito."""
import re
from typing import List, Optional

from app.extraction.base import build_item
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import has_kw

KEYWORDS = ["poder especial", "otorgante", "apoderado", "confiero poder", "otorgo poder"]
CORE_FIELDS = ["otorganteNombre", "otorganteIdentificacion", "apoderadoNombre"]


def parse_poder(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    if not has_kw(text, KEYWORDS):
        return None

    # El nombre va tras "Yo"; el número suele quedar en otra línea y "identificado" puede no leerse
    otorgante = re.search(r'\bYo[ \t]+([a-zA-ZÁÉÍÓÚÑáéíóúñ]+(?:[ \t]+[a-zA-ZÁÉÍÓÚÑáéíóúñ]+){1,4}?)[ \t]*(?:identificado|\d|\n|$)',
                          text, re.IGNORECASE)
    doc_otorgante = re.search(r'C\.?C\.?\s*\(?[xX]?\)?\s*(\d+)', text, re.IGNORECASE)
    apoderado = re.search(r'\b(?:Sra|Sr@|Sr)\.?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)\s*identificado', text, re.IGNORECASE)
    placa = re.search(r'PLACAS?\s*[:#]?\s*([A-Z]{3}\s?\d{2}[A-Z0-9])\b', text, re.IGNORECASE)  # carro ABC123 / moto ABC12D

    tramites = []
    if placa:
        placa_clean = re.sub(r'([A-Z]{3})[oO0]{2,3}(\d|\b)', r'\g<1>00\2', placa.group(1).strip())
        tramites.append(f"Matrícula inicial y trámites de tránsito para vehículo de placas {placa_clean}")
    else:
        tramites.append("Gestiones y trámites de tránsito encomendados")

    extracted = {
        "otorganteNombre": otorgante.group(1).strip() if otorgante else None,
        "otorganteIdentificacion": doc_otorgante.group(1).strip() if doc_otorgante else None,
        "tramitesAutorizados": tramites,
        "apoderadoNombre": (apoderado.group(1).strip() or None) if apoderado else None,
    }
    return build_item("poder", "Poder especial de tránsito", extracted, CORE_FIELDS, ocr_score)
