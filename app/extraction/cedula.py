"""Cédula de ciudadanía: extracción por posición (principal) y por regex (respaldo)."""
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.extraction.base import build_item
from app.extraction.contact import extract_contact
from app.extraction.spatial import spatial_value
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import has_kw, norm, squash

KEYWORDS = ["cedula", "cédula", "republica de colombia", "ciudadania", "nuip"]
CORE_FIELDS = ["nombreCompleto", "numeroDocumento", "fechaNacimiento", "sexo", "rh"]

# Etiquetas (en minúsculas y sin espacios) que aparecen en la cédula
LABELS = {
    "numero": ["numero", "nuip"],
    "apellidos": ["apellidos", "apellido"],
    "nombres": ["nombres", "nombre"],
    "fecha": ["fechadenacimiento", "fechanacimiento", "nacimiento"],
    "estatura": ["estatura"],
    "sexo": ["sexo"],
    "rh": ["gsrh", "grupo sanguineo", "rh"],
    "otros": ["lugardenacimiento", "fechaylugardeexpedicion", "expedicion", "indicederecho", "donante",
              "fechadeexpedicion"],
}
ALL_LABELS = [a for v in LABELS.values() for a in v]

_NAME_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñÜü][A-Za-zÁÉÍÓÚÑáéíóúñÜü\s.'-]+")
_DATE_RE = re.compile(r"\d{2}\s*-?\s*[A-Za-z]{3}\s*-?\s*\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}")


# --- Validadores: devuelven el valor limpio o None si el texto no tiene la forma esperada ---------------

def _is_label_word(t: str) -> bool:
    sq = squash(t)
    return any(sq == a or (len(a) >= 5 and SequenceMatcher(None, sq, a).ratio() >= 0.85) for a in ALL_LABELS)


def _v_number(t: str) -> Optional[str]:
    t = t.strip()
    d = re.sub(r"\D", "", t)
    return d if re.fullmatch(r"[\d.,\s]+", t) and 6 <= len(d) <= 10 else None


def _v_name(t: str) -> Optional[str]:
    t = t.strip(" :.-")
    return t if len(t) >= 2 and _NAME_RE.fullmatch(t) and not _is_label_word(t) else None


def _v_date(t: str) -> Optional[str]:
    m = _DATE_RE.search(t)
    return re.sub(r"\s+", "", m.group(0)) if m else None


def _v_height(t: str) -> Optional[str]:
    m = re.search(r"(?<![\d.,])([12])\s*[.,]\s*(\d{2})(?![\d]|[.,]\d)", t)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _v_sex(t: str) -> Optional[str]:
    for tok in norm(t).replace(".", " ").split():
        if tok in ("m", "f"):
            return tok.upper()
        if tok in ("masculino", "femenino"):
            return tok[0].upper()
    return None


def _v_rh(t: str) -> Optional[str]:
    for tok in t.upper().replace(".", " ").split():
        m = re.fullmatch(r"(AB|A|B|O|0)([+-])", tok) or re.fullmatch(r"([+-])(AB|A|B|O|0)", tok)  # "+0" = O+ mal leído
        if m:
            a, b = m.groups()
            grp, sign = (b, a) if a in "+-" else (a, b)
            return grp.replace("0", "O") + sign
    return None


# --- Extracción --------------------------------------------------------------------------------------

def _extract_spatial(boxes: List[OcrBox], vertical: str) -> Dict[str, Any]:
    def find(key: str, validate):
        return spatial_value(boxes, LABELS[key], validate, ALL_LABELS, vertical)

    nombres, apellidos = find("nombres", _v_name), find("apellidos", _v_name)
    return {
        "nombreCompleto": " ".join(x for x in (nombres, apellidos) if x) or None,
        "numeroDocumento": find("numero", _v_number),
        "fechaNacimiento": find("fecha", _v_date),
        "sexo": find("sexo", _v_sex),
        "rh": find("rh", _v_rh),
        "estatura": find("estatura", _v_height),
    }


def extract_spatial(boxes: List[OcrBox]) -> Dict[str, Any]:
    """Valor de cada campo según su posición respecto a la etiqueta. La cédula nueva pone el valor ENCIMA
    de la etiqueta y las antiguas DEBAJO: se prueban ambos sentidos y gana el que valida más campos."""
    above, below = _extract_spatial(boxes, "above"), _extract_spatial(boxes, "below")
    n_above, n_below = sum(1 for v in above.values() if v), sum(1 for v in below.values() if v)
    if n_above != n_below:
        return above if n_above > n_below else below
    return _extract_spatial(boxes, "both")


def parse_cedula(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    if not has_kw(text, KEYWORDS):
        return None

    doc = (re.search(r'(?:NUMERO|NÚMERO|NUIP)\.?\s*[:#]?\s*(\d{1,3}(?:[\.,\s]?\d{3}){1,3})(?!\d)', text, re.IGNORECASE)
           or re.search(r'(?<![\w/-])([1-9]\d{0,2}(?:[\.,]\d{3}){2,3}|[1-9]\d{7,9})(?![\w/-])', text))  # sin letras pegadas ni ceros a la izquierda (códigos del reverso)
    num_doc = re.sub(r"\D", "", doc.group(1)) if doc else None

    apellidos = re.search(r'APELLIDOS\s*[:\n]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*NOMBRES|\n[A-Z]+|$)', text, re.IGNORECASE)
    nombres = re.search(r'NOMBRES\s*[:\n]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*FECHA|\s*NUMERO|\n[A-Z]+|$)', text, re.IGNORECASE)

    nombre_comp = None
    if nombres and apellidos:
        nombre_comp = f"{nombres.group(1).strip()} {apellidos.group(1).strip()}"
    elif nombres:
        nombre_comp = nombres.group(1).strip()

    fecha_pat = r'(\d{2}-?[A-Z]{3}-?\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})'
    fecha_nac = (re.search(r'NACIMIENTO\s*[:\n]?\s*' + fecha_pat, text, re.IGNORECASE)
                 or re.search(fecha_pat, text, re.IGNORECASE))
    sexo = re.search(r'SEXO\s*[:\n"]*\s*([MF])', text, re.IGNORECASE)
    rh = re.search(r'(?:G\.?S\.?\s*)?RH\s*[:\n"]*\s*([0OAB][+-])', text, re.IGNORECASE)
    estatura = re.search(r'ESTATURA\s*[:\n"]*\s*(\d[\.,]\d{2})', text, re.IGNORECASE)

    extracted: Dict[str, Any] = {
        "nombreCompleto": nombre_comp,
        "numeroDocumento": num_doc,
        "fechaNacimiento": fecha_nac.group(1).strip() if fecha_nac else None,
        "sexo": sexo.group(1).upper() if sexo else None,
        "rh": rh.group(1).upper().replace("0", "O") if rh else None,
        "estatura": estatura.group(1).strip() if estatura else None,
        "celular": None,
        "correo": None,
        "direccion": None,
    }
    if boxes:  # por posición: más fiable que el regex si las etiquetas se detectan
        for k, v in extract_spatial(boxes).items():
            if v:
                extracted[k] = v
    # Respaldo cuando el OCR une columnas o separa el valor de su etiqueta
    upper = text.upper()
    if not extracted["rh"]:
        m = re.search(r"(?<![A-Z0-9])(AB|A|B|O|0)\s?([+-])(?![A-Za-z0-9])", upper)
        extracted["rh"] = m.group(1).replace("0", "O") + m.group(2) if m else None
    if not extracted["estatura"]:
        extracted["estatura"] = _v_height(text)
    if not extracted["sexo"]:
        m = re.search(r"-([MF])-\d{6,}", upper)  # línea de códigos al pie de la cédula
        extracted["sexo"] = m.group(1) if m else None
    extracted.update(extract_contact(text, boxes, num_doc))  # celular, correo, dirección (a mano)
    return build_item("cedula", "Cédula de ciudadanía", extracted, CORE_FIELDS, ocr_score)
