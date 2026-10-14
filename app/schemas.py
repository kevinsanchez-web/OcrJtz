"""Modelos de la respuesta de la API."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, PrivateAttr


class DocumentItem(BaseModel):
    found: bool
    documentType: str  # "cedula" | "factura" | "runt" | "gases" | "poder"
    extractedData: Dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    page: Optional[int] = None   # página del archivo (empieza en 1)
    needsReview: bool = False    # True si la confianza es baja: conviene que una persona lo revise
    inheritedFields: List[str] = Field(default_factory=list)  # campos vacíos completados con datos de otra página del archivo
    # Lectura de la que salió (cajas, tamaño, giros): no se serializa; sirve para devolver las posiciones si se piden
    _read: Optional[Dict[str, Any]] = PrivateAttr(default=None)


class OcrLine(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    box: List[float]  # [x0, y0, x1, y1] relativos a la página (0 a 1), sin importar su resolución


class KeyValue(BaseModel):
    key: str
    value: str


class Table(BaseModel):
    rows: List[List[str]]        # filas de la tabla, cada una con sus celdas (la primera suele ser el encabezado)
    source: str                  # "grid" (celdas reales del PDF) o "aligned" (deducida de la posición del texto)
    box: List[float]             # [x0, y0, x1, y1] relativo a la página (0 a 1)


class OcrPage(BaseModel):
    page: int
    source: str                  # "native" (texto digital del PDF) u "ocr" (leído de la imagen)
    text: str                    # texto de la página en orden de lectura, un renglón por línea
    confidence: float = Field(ge=0.0, le=1.0)
    lines: Optional[List[OcrLine]] = None   # cada línea con su posición (si se pidió `lines=true`)
    # Giros (grados antihorarios, en orden, cada uno ampliando el lienzo) que se aplicaron a la página original antes
    # de leerla: `lines[].box` está en ESA imagen ya enderezada. Para dibujar los recuadros sobre la página original,
    # aplícale estos mismos giros primero. Vacío = la página se leyó tal cual.
    rotations: List[float] = Field(default_factory=list)
    keyValues: List[KeyValue] = Field(default_factory=list)  # pares "etiqueta: valor" detectados en el texto
    tables: List[Table] = Field(default_factory=list)        # tablas con sus filas y columnas
    documentType: Optional[str] = None      # tipo conocido (cedula, factura...) si se reconoció, si no null


class FieldValue(BaseModel):
    value: str
    source: str                  # "keyvalue" (par etiqueta: valor), "text" (línea del texto) o "vlm" (modelo de visión)
    page: Optional[int] = None


class OcrResponse(BaseModel):
    text: str                    # texto de todas las páginas
    pages: List[OcrPage]
    fields: Optional[Dict[str, Optional[FieldValue]]] = None  # campos pedidos con `fields=`; null si no se encontró
    documents: List["DocumentItem"] = Field(default_factory=list)  # campos estructurados de documentos conocidos
    warnings: List[str] = Field(default_factory=list)


class JobStatus(BaseModel):
    jobId: str
    status: str                       # "queued" (en cola) | "processing" | "done" | "error"
    mode: str                         # "transit" (campos de documentos de tránsito) | "ocr" (OCR general)
    progress: Optional[Dict[str, int]] = None   # {"page": N, "total": M} mientras procesa un PDF
    elapsedSeconds: float = 0.0
    cached: bool = False
    result: Optional[Dict[str, Any]] = None     # el mismo cuerpo que devuelve la versión síncrona, al terminar
    error: Optional[Dict[str, Any]] = None      # {"status": 4xx/5xx, "detail": "..."} si falló


class AnalyzeDocumentResponse(BaseModel):
    documents: List[DocumentItem]
    warnings: List[str] = []     # páginas ilegibles, tiempo agotado, documentos no reconocidos...
    pages: Optional[List[OcrPage]] = None   # con `lines=true`: la posición de cada línea leída de cada página reconocida
