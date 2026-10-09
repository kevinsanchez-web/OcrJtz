"""Modelos de la respuesta de la API."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentItem(BaseModel):
    found: bool
    documentType: str  # "cedula" | "factura" | "runt" | "gases" | "poder"
    extractedData: Dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    page: Optional[int] = None   # página del archivo (empieza en 1)
    needsReview: bool = False    # True si la confianza es baja: conviene que una persona lo revise
    inheritedFields: List[str] = Field(default_factory=list)  # campos vacíos completados con datos de otra página del archivo


class AnalyzeDocumentResponse(BaseModel):
    documents: List[DocumentItem]
    warnings: List[str] = []     # páginas ilegibles, tiempo agotado, documentos no reconocidos...
