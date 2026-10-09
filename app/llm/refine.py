"""Respaldo con un modelo local (Ollama): completa los campos que el regex y la posición no pudieron extraer.

- Modo "vision" (por defecto): un VLM ve la imagen de la página y lee los campos faltantes.
- Modo "text": un LLM de solo texto recibe el texto del OCR.

Reglas para que sea eficiente y seguro:
- Solo actúa si la confianza es baja y solo pide los campos que faltan (prompt y respuesta más cortos).
- Cada valor devuelto debe tener la forma del campo (validators) y PARECERSE a lo que el OCR leyó: el modelo
  puede corregir un dígito mal leído, pero no inventar un dato que no esté en el documento.
- Si el modelo falla o tarda demasiado, se devuelve el resultado original sin cambios.
"""
import base64
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import cv2
import numpy as np

from app.config import ENOUGH_CONFIDENCE, LLM_MODE, LLM_MODEL, VLM_MAX_SIDE, VLM_MODEL
from app.extraction.validators import NAME_FIELDS, validate_field
from app.llm import client
from app.schemas import DocumentItem
from app.text_utils import fix_ocr_zeros, squash

logger = logging.getLogger("document-api.llm")

# Campos a extraer por tipo de documento, con una pista para el modelo
FIELD_HINTS: Dict[str, Dict[str, str]] = {
    "cedula": {
        "nombreCompleto": "nombres y apellidos de la persona", "numeroDocumento": "número de cédula, solo dígitos",
        "fechaNacimiento": "fecha de nacimiento", "sexo": "M o F", "rh": "grupo sanguíneo, ej. O+",
        "estatura": "estatura en metros, ej. 1.75"},
    "factura": {
        "numeroFactura": "número de la factura", "fecha": "fecha de la factura",
        "compradorNombre": "nombre del comprador/cliente", "compradorIdentificacion": "cédula o NIT del comprador",
        "vehiculoMarca": "marca del vehículo", "vehiculoLinea": "línea o referencia del vehículo",
        "vehiculoModelo": "año del modelo", "vehiculoMotor": "número de motor",
        "vehiculoChasis": "número de chasis o VIN", "valorTotal": "valor total a pagar",
        "vehiculoCilindrada": "cilindraje en cc", "vehiculoColor": "color del vehículo"},
    "runt": {
        "tramiteSolicitado": "trámite marcado con una X (matrícula, traspaso...)",
        "propietarioNombre": "nombre completo del propietario",
        "propietarioIdentificacion": "cédula del propietario", "vehiculoMarca": "marca", "vehiculoLinea": "línea",
        "vehiculoMotor": "número de motor", "vehiculoChasis": "número de chasis", "vehiculoModelo": "año del modelo",
        "vehiculoCilindrada": "cilindrada en cc", "vehiculoColor": "color"},
    "gases": {
        "fechaCertificado": "fecha del certificado", "vehiculoMarca": "marca", "vehiculoChasis": "número de chasis o VIN",
        "vehiculoMotor": "número de motor", "vehiculoCilindrada": "cilindrada en cc", "vehiculoLinea": "línea o referencia",
        "vehiculoModelo": "año del modelo"},
    "poder": {
        "otorganteNombre": "nombre de quien otorga el poder (escrito a mano)",
        "otorganteIdentificacion": "cédula del otorgante",
        "apoderadoNombre": "nombre del apoderado (a quien se otorga el poder)"},
}
CORE_FIELDS: Dict[str, List[str]] = {}  # lo completa pipeline.py (evita importar los extractores aquí)


def _tokens(text: str) -> List[str]:
    return [t for t in (squash(w) for w in text.split()) if t]


def _supported(field: str, value: str, text: str, text_sq: str, tokens: List[str]) -> bool:
    """¿El valor aparece (o casi) en el texto del OCR? Admite una o dos letras/dígitos mal leídos."""
    v = squash(value)
    if not v:
        return False
    if v in text_sq:
        return True

    def close(piece: str) -> bool:
        return piece in text_sq or any(
            abs(len(t) - len(piece)) <= 2 and SequenceMatcher(None, piece, t).ratio() >= 0.75 for t in tokens)

    if field in NAME_FIELDS:
        words = [squash(w) for w in value.split() if len(w) >= 3]
        return bool(words) and all(close(w) for w in words)
    return len(v) >= 4 and close(v)


def _encode_image(img: np.ndarray) -> str:
    """JPEG en base64, con el lado mayor limitado (menos píxeles = respuesta más rápida del VLM)."""
    h, w = img.shape[:2]
    k = VLM_MAX_SIDE / max(h, w)
    if k < 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return base64.b64encode(buf.tobytes()).decode()


def refine_item(item: DocumentItem, text: str, ocr_score: float = 1.0,
                image: Optional[np.ndarray] = None) -> DocumentItem:
    """Completa con el modelo local los campos vacíos de `item` si su confianza es baja."""
    if not client.enabled() or item.confidence >= ENOUGH_CONFIDENCE:
        return item
    use_vision = LLM_MODE == "vision"
    if use_vision and image is None:
        return item  # PDF con texto nativo: no hay imagen que enseñar al VLM
    hints = FIELD_HINTS.get(item.documentType)
    missing = [f for f in (hints or {}) if not item.extractedData.get(f)]
    if not hints or not missing:
        return item

    text = fix_ocr_zeros(text)
    schema = {"type": "object",
              "properties": {f: {"type": ["string", "null"]} for f in missing}, "required": missing}
    pistas = "\n".join(f"- {f}: {hints[f]}" for f in missing)
    if use_vision:
        prompt = (f"Esta imagen es un documento colombiano de tipo '{item.documentType}'. "
                  f"Lee y extrae SOLO estos campos:\n{pistas}\n"
                  f"Copia los valores tal como están escritos. Usa null si el dato no está o no se puede leer. "
                  f"NO inventes ni completes datos.")
        model, images = VLM_MODEL, [_encode_image(image)]
    else:
        prompt = (f"Texto leído por OCR de un documento colombiano de tipo '{item.documentType}'. Puede tener errores: "
                  f"palabras pegadas, O/0 confundidos y columnas mezcladas.\nExtrae SOLO estos campos:\n{pistas}\n"
                  f"Usa null si el dato no aparece con claridad. NO inventes ni completes.\n\nTEXTO:\n{text[:6000]}")
        model, images = LLM_MODEL, None

    t0 = time.monotonic()
    out = client.chat_json(prompt, schema, model=model, images=images)
    if not out:
        return item

    text_sq, tokens = squash(text), _tokens(text)
    data = dict(item.extractedData)
    added = []
    for f in missing:
        v = validate_field(f, out.get(f))
        if v and _supported(f, v, text, text_sq, tokens):
            data[f] = v
            added.append(f)
    logger.info("Respaldo %s (%s, %.0fs): %d/%d campos aportados%s", "VLM" if use_vision else "LLM", model,
                time.monotonic() - t0, len(added), len(missing), f" -> {', '.join(added)}" if added else "")
    if not added:
        return item

    core = CORE_FIELDS.get(item.documentType, [])
    filled = sum(1 for k in core if data.get(k))
    confidence = round(min(1.0, (filled / len(core) if core else 0.0) * ocr_score), 2)
    label = item.summary.split(":")[0]
    return item.model_copy(update={
        "extractedData": data, "confidence": confidence,
        "summary": f"{label}: {filled}/{len(core)} campos clave extraídos (completado con modelo local).",
    })
