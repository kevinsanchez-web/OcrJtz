"""Extracción por posición para formularios con etiquetas (RUNT, certificados, facturas).

Cada documento declara qué etiquetas tiene cada campo y cómo se valida su valor (ver `FormSpec`); aquí se
busca el valor junto a la etiqueta (a la derecha, debajo o encima) con `spatial_value`.
"""
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from app.extraction.spatial import spatial_value
from app.ocr.boxes import OcrBox

# campo -> (etiquetas en minúsculas y sin espacios, validador del valor)
FormSpec = Dict[str, Tuple[List[str], Callable[[str], Optional[str]]]]


def extract_form(boxes: List[OcrBox], spec: FormSpec, vertical: str = "both",
                 extra_labels: Iterable[str] = ()) -> Dict[str, Optional[str]]:
    """Valor de cada campo de `spec` según su posición respecto a la etiqueta.
    `vertical`: "below" si el valor va debajo de la etiqueta, "above" si va encima, "both" si varía."""
    all_labels = [a for aliases, _ in spec.values() for a in aliases] + list(extra_labels)
    return {field: spatial_value(boxes, aliases, validate, all_labels, vertical)
            for field, (aliases, validate) in spec.items()}


def merge_found(extracted: Dict, found: Dict[str, Optional[str]]) -> None:
    """Los valores hallados por posición sustituyen a los del regex (son más fiables si hubo etiqueta)."""
    for k, v in found.items():
        if v:
            extracted[k] = v
