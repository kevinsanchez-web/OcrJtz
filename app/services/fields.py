"""Campos a pedido: el cliente dice qué datos quiere de un documento cualquiera (`fields=total,fecha,correo`).

1. Se buscan entre los pares "etiqueta: valor" y las líneas del texto, tolerando sinónimos (total ~ valor total ~
   monto) y escrituras distintas (numeroContrato ~ "No. de contrato").
2. Lo que no aparezca así se le pide al modelo de visión (si está encendido), y solo se acepta un valor que
   también se parezca a lo que leyó el OCR: nunca se inventa un dato.
"""
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import numpy as np

from app.config import LLM_MODE, VLM_MODEL
from app.extraction.contact import v_email, v_phone
from app.extraction.validators import _DATE_RE
from app.llm import client
from app.llm.refine import _encode_image, _supported, _tokens
from app.schemas import FieldValue, OcrPage
from app.text_utils import norm, squash

logger = logging.getLogger("document-api")

SYNONYMS = {
    "total": ["valor total", "monto", "importe", "total a pagar", "total neto"],
    "valor": ["total", "monto", "importe", "precio"],
    "fecha": ["fecha de expedicion", "fecha de emision", "date"],
    "cedula": ["documento", "identificacion", "cc", "nit", "c c"],
    "documento": ["cedula", "identificacion", "cc", "nit"],
    "telefono": ["celular", "tel", "movil"],
    "celular": ["telefono", "tel", "movil"],
    "correo": ["email", "e mail", "mail", "correo electronico"],
    "email": ["correo", "mail", "correo electronico"],
    "direccion": ["domicilio", "residencia"],
    "nombre": ["name", "cliente", "senor", "razon social"],
    "numero": ["no", "nro", "num"],
}
MIN_SECONDS_FOR_VLM = 70


def _words(name: str) -> List[str]:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.strip())          # numeroContrato -> numero Contrato
    return [w for w in re.split(r"[\s_\-./]+", norm(name)) if w]


def _variants(name: str) -> List[List[str]]:
    """El nombre pedido y sus variantes con sinónimos, como listas de palabras."""
    base = _words(name)
    out = [base]
    for i, w in enumerate(base):
        for syn in SYNONYMS.get(w, []):
            out.append(base[:i] + syn.split() + base[i + 1:])
    return out


def _typed(name: str, value: str) -> Optional[str]:
    """Si el nombre sugiere un tipo (fecha, correo, teléfono, importe), extrae solo esa parte del valor."""
    words = set(_words(name))
    if words & {"correo", "email", "mail"}:
        return v_email(value)
    if words & {"telefono", "celular", "tel", "movil"}:
        return v_phone(value) or (value if re.search(r"\d{7,}", value) else None)
    if "fecha" in words or "date" in words:
        m = _DATE_RE.search(value) or re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", value)
        return m.group(0) if m else None
    if words & {"total", "valor", "monto", "importe", "precio", "subtotal", "saldo", "canon", "renta", "cuota", "pago",
                  "costo", "tarifa", "salario", "sueldo"}:
        m = re.search(r"\$?\s*\d[\d.,]*", value)
        return m.group(0).replace("$", "").strip() if m else None
    return value.strip() or None


def _from_text(name: str, pages: List[OcrPage]) -> Optional[FieldValue]:
    variants = _variants(name)
    # 1) pares "etiqueta: valor" ya detectados
    best = (0.0, None, 0)
    target = squash(" ".join(variants[0]))
    for p in pages:
        for kv in p.keyValues:
            key = squash(kv.key)
            if not key:
                continue
            for v in variants:
                s = squash(" ".join(v))
                score = 1.0 if s == key else 0.9 if (len(s) >= 4 and (s in key or key in s)) else \
                    SequenceMatcher(None, s, key).ratio()
                if score > best[0]:
                    typed = _typed(name, kv.value)
                    if typed:
                        best = (score, typed, p.page)
    if best[0] >= 0.8 and best[1]:
        return FieldValue(value=best[1], source="keyvalue", page=best[2])
    # 2) línea que empieza con la etiqueta, aunque no lleve dos puntos ("Total neto 32.700")
    for v in variants:
        pat = re.compile(r"^\W*" + r"[\W_]*".join(map(re.escape, v)) + r"[\s:=\-]*(\S.*)$", re.IGNORECASE)
        for p in pages:
            for line in p.text.split("\n"):
                m = pat.match(_fold(line))  # _fold conserva la longitud: el valor se corta del texto original
                if m:
                    typed = _typed(name, line[m.start(1):])
                    if typed:
                        return FieldValue(value=typed, source="text", page=p.page)
    return None


_FOLD = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _fold(s: str) -> str:
    """Quita acentos sin cambiar la longitud del texto."""
    return s.translate(_FOLD)


def find_fields(requested: List[str], pages: List[OcrPage], images: Dict[int, np.ndarray],
                deadline: float) -> Dict[str, Optional[FieldValue]]:
    out: Dict[str, Optional[FieldValue]] = {}
    for name in requested:
        try:
            out[name] = _from_text(name, pages)
        except Exception:
            logger.exception("Falló la búsqueda del campo %r", name)
            out[name] = None
    missing = [n for n, v in out.items() if v is None]
    if missing and client.enabled() and LLM_MODE == "vision" and images:
        full_text = "\n".join(p.text for p in pages)
        text_sq, tokens = squash(full_text), _tokens(full_text)
        for page_no in sorted(images)[:4]:
            if not missing or time.monotonic() + MIN_SECONDS_FOR_VLM > deadline:
                break
            schema = {"type": "object", "properties": {f: {"type": ["string", "null"]} for f in missing},
                      "required": missing}
            names = "\n".join(f"- {f}: {' '.join(_words(f))}" for f in missing)
            prompt = ("Esta imagen es un documento. Lee y extrae SOLO estos campos:\n" + names +
                      "\nCopia los valores tal como están escritos. Usa null si el dato no está o no se puede leer. "
                      "NO inventes ni completes datos.")
            try:
                res = client.chat_json(prompt, schema, model=VLM_MODEL, images=[_encode_image(images[page_no])],
                                       label="campos pedidos") or {}
            except Exception:
                logger.exception("Falló la consulta al modelo de visión")
                break
            for f in list(missing):
                v = res.get(f)
                if isinstance(v, str) and v.strip() and _supported(f, v.strip(), full_text, text_sq, tokens):
                    out[f] = FieldValue(value=v.strip(), source="vlm", page=page_no)
                    missing.remove(f)
    return out
