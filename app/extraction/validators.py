"""Validación de campos por su forma esperada.

Un dato equivocado es peor que un dato vacío: aquí se descartan los valores que no tienen la forma del campo
(un chasis sin dígitos, una identificación con letras, un nombre que es una etiqueta del formulario...).
"""
import datetime
import re
from typing import Any, Callable, Dict, Optional

from app.extraction.contact import v_address, v_email, v_phone
from app.extraction.lexicon import BRANDS, COLORS, canonical
from app.text_utils import squash

# Palabras que delatan que se capturó una etiqueta del formulario en lugar de un valor
_FORM_WORDS = ("nombre", "apellido", "letras", "numeros", "direccion", "telefono", "firma", "identifi",
               "documento", "tramite", "observacion", "clasedevehiculo", "servicio")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-[A-Za-z]{3}-\d{4}")


def v_id(s: str) -> Optional[str]:
    d = re.sub(r"[.,\s]", "", s)
    return d if re.fullmatch(r"\d{5,12}", d) else None


def v_name(s: str) -> Optional[str]:
    s = s.strip(" :.-,)(")  # restos de la etiqueta ("ESTIMADO(A):")
    letters = sum(c.isalpha() for c in s)
    if letters < 5 or letters < 0.7 * len(s.replace(" ", "")):
        return None
    sq = squash(s)
    if any(w in sq for w in _FORM_WORDS):
        return None
    words = [t for t in re.findall(r"[^\W\d_]+", s) if len(t) >= 3]
    return s if len(words) >= 2 or (len(words) == 1 and len(words[0]) >= 8) else None  # 'DE IVA' no es un nombre


_MONTHS = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10,
           "NOV": 11, "DIC": 12, "JAN": 1, "APR": 4, "AUG": 8, "DEC": 12}


def _real_date(s: str) -> bool:
    """¿Es una fecha posible del calendario? Descarta lecturas como 31/02/2020 o 2024-13-01."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{2})[/-]([A-Za-z]{3}|\d{2})[/-](\d{4})", s)
        if not m:
            return False
        d, y = int(m[1]), int(m[3])
        mo = int(m[2]) if m[2].isdigit() else _MONTHS.get(m[2].upper(), 0)
    try:
        datetime.date(y, mo, d)
    except ValueError:
        return False
    return 1900 <= y <= datetime.date.today().year + 1


def v_date(s: str) -> Optional[str]:
    m = _DATE_RE.search(s)
    return m.group(0) if m and _real_date(m.group(0)) else None


def v_money(s: str) -> Optional[str]:
    return s if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{4,}", s) else None


def v_chassis(s: str) -> Optional[str]:
    t = s.replace(" ", "").upper()
    if len(t) == 17:  # VIN: nunca lleva I, O ni Q (el OCR las confunde con 1 y 0)
        t = t.translate(str.maketrans("IOQ", "100"))
    ok = re.fullmatch(r"[A-Z0-9]{8,20}", t) and re.search(r"\d", t) and re.search(r"[A-Z]", t)
    return t if ok else None


def v_motor(s: str) -> Optional[str]:
    t = s.replace(" ", "").upper()
    return t if re.fullmatch(r"[A-Z0-9-]{5,20}", t) and re.search(r"\d", t) else None


def v_year(s: str) -> Optional[str]:
    return s if re.fullmatch(r"(?:19|20)\d{2}", s) else None


def v_displacement(s: str) -> Optional[str]:
    return s if re.fullmatch(r"\d{2,4}", s) else None


def v_invoice_no(s: str) -> Optional[str]:
    return s if re.fullmatch(r"[A-Za-z]{0,5}-?\d{3,}[A-Za-z0-9-]*", s) else None


def v_vehicle_text(s: str) -> Optional[str]:
    """Marca, línea y color: texto corto que no sea solo un número ni una etiqueta."""
    if not re.fullmatch(r"[A-Za-z0-9 \-/]{2,30}", s) or s.isdigit():
        return None
    stop = ("linea", "marca", "modelo", "motor", "clase", "servicio", "prenda", "registro", "matricula",
            "traspaso", "cambio", "levanta", "carroceria")
    return None if any(w in squash(s) for w in stop) else s


def v_brand(s: str) -> Optional[str]:
    """Marca: si se parece mucho a una conocida (CHEVR0LET, TOYOTTA) se corrige; si no, se deja."""
    v = v_vehicle_text(s)
    return (canonical(v, BRANDS) or v) if v else None


def v_color(s: str) -> Optional[str]:
    v = v_vehicle_text(s)
    return (canonical(v, COLORS, 0.8) or v) if v else None


VALIDATORS: Dict[str, Callable[[str], Optional[str]]] = {
    "numeroDocumento": v_id, "compradorIdentificacion": v_id,
    "propietarioIdentificacion": v_id, "otorganteIdentificacion": v_id,
    "nombreCompleto": v_name, "compradorNombre": v_name, "propietarioNombre": v_name,
    "otorganteNombre": v_name, "apoderadoNombre": v_name,
    "fecha": v_date, "fechaNacimiento": v_date, "fechaCertificado": v_date,
    "valorTotal": v_money, "totalTransaccion": v_money,
    "celular": v_phone, "correo": v_email, "direccion": v_address,
    "vehiculoChasis": v_chassis, "vehiculoMotor": v_motor,
    "vehiculoModelo": v_year, "vehiculoCilindrada": v_displacement,
    "numeroFactura": v_invoice_no,
    "vehiculoMarca": v_brand, "vehiculoLinea": v_vehicle_text, "vehiculoColor": v_color,
}

# Campos de texto libre (nombres): la comprobación "aparece en el texto del OCR" admite el orden cambiado
NAME_FIELDS = {"nombreCompleto", "compradorNombre", "propietarioNombre", "otorganteNombre", "apoderadoNombre"}


def validate_field(field: str, value: Any) -> Optional[str]:
    """Valor limpio, o None si está vacío o no tiene la forma del campo."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    fn = VALIDATORS.get(field)
    return fn(value) if fn else value


def sanitize(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Aplica `validate_field` a todos los campos de texto (las listas se dejan como están)."""
    return {k: (validate_field(k, v) if isinstance(v, str) or v is None else v) for k, v in extracted.items()}
