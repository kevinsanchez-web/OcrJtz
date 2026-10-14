"""Forma final de los datos que consume el frontend (ver `src/types.ts` y `ActiveDocumentDetail.tsx` de la app).

La extracción trabaja con textos planos; aquí, al final, se entregan con la forma que la interfaz espera:
los importes como NÚMERO (el campo "Total Transacción" es un input numérico) y el contacto de la cédula
anidado en `contacto: {celular, correo, direccion}`.
"""
import re
from typing import List, Optional, Union

from app.schemas import DocumentItem

MONEY_FIELDS = ("valorTotal", "totalTransaccion")
CONTACT_FIELDS = ("celular", "correo", "direccion")


def parse_money(s: str) -> Optional[Union[int, float]]:
    """'17.590.000' / '17.590.000,00' / '17,590,000.50' / '1306714' -> número. None si no es un importe."""
    s = s.strip().lstrip("$").strip()
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    last = max(s.rfind("."), s.rfind(","))
    if last != -1 and len(s) - last - 1 in (1, 2) and not (len(s) - last - 1 == 3):
        whole, frac = s[:last], s[last + 1:]  # el último separador son decimales (1 o 2 cifras)
        value = float(re.sub(r"\D", "", whole) + "." + frac)
        return int(value) if value == int(value) else value
    return int(re.sub(r"\D", "", s))


def finalize(items: List[DocumentItem], nest_contact: bool = True) -> None:
    """Ajusta `extractedData` de cada documento a la forma que espera el frontend (en el sitio).
    `nest_contact`: anidar el contacto de la cédula en `contacto` (la forma de la app de tránsito)."""
    for item in items:
        data = item.extractedData
        for f in MONEY_FIELDS:
            if isinstance(data.get(f), str):
                data[f] = parse_money(data[f])
        if nest_contact and item.documentType == "cedula":
            data["contacto"] = {f: data.pop(f, None) or "" for f in CONTACT_FIELDS}
