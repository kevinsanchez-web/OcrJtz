"""Datos de contacto (celular, correo, dirección), normalmente escritos a mano junto a un documento.

El OCR los lee con errores, así que cada valor se valida por su forma: un celular colombiano son 10 dígitos
y empieza por 3; un correo tiene @ y un dominio; una dirección tiene un tipo de vía o un '#' con números.
Lo que no cumple se deja vacío (ver `reconcile.py`, que además lo completa con el de otras páginas).
"""
import re
from typing import Dict, List, Optional

from app.extraction.lexicon import canonical
from app.ocr.boxes import OcrBox

CONTACT_FIELDS = ("celular", "correo", "direccion")

_PHONE_LABEL = re.compile(r"(?:T?ELEFONO|CELULAR|CELUL|CEL|TEL)\W{0,3}([\d\s().-]{9,20})", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+(?:\s[\w.+-]+)?\s?@\s?[\w-]+(?:\s?\.\s?[\w-]+)+")  # a mano, el OCR separa con espacios
EMAIL_DOMAINS = ["GMAIL", "HOTMAIL", "OUTLOOK", "YAHOO", "ICLOUD", "LIVE"]
_STREET = re.compile(r"\b(?:CALLE|CLL?|CARRERA|CRA?|KR|KRA|CARR?|AVENIDA|AV|AVDA|DIAGONAL|DG|DIAG|TRANSVERSAL|TV|"
                     r"TRANS|MANZANA|MZ|BARRIO|BR|VEREDA|KM)\b\.?\s*\d", re.IGNORECASE)
_ADDRESS_LABEL = re.compile(r"^\W*(?:\d{1,2}\W{0,2})?(?:DIRECCION|DIR|RESIDENCIA)\W*(?P<rest>.*)$", re.IGNORECASE)


def v_phone(s: str) -> Optional[str]:
    """Celular colombiano: 10 dígitos que empiezan por 3. Tolera un dígito de más pegado (casilla, ruido)."""
    digits = re.sub(r"\D", "", s)
    m = re.search(r"3\d{9}", digits) if len(digits) <= 12 else None
    return m.group(0) if m else None


def v_email(s: str) -> Optional[str]:
    m = _EMAIL.search(s)
    if not m:
        return None
    mail = re.sub(r"\s", "", m.group(0)).lower().strip(".-")
    local, _, domain = mail.partition("@")
    label, dot, rest = domain.partition(".")  # el OCR escribe a mano 'gmail' como '6mall', 'g.mail', 'Email'...
    fixed = canonical(label, EMAIL_DOMAINS, 0.6) if len(label) >= 5 else None
    mail = f"{local}@{(fixed or label).lower()}{dot}{rest}"
    return mail if re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[a-z]{2,6}){1,3}", mail) else None


def v_address(s: str) -> Optional[str]:
    s = s.strip(" :=-.,")
    if len(s) < 6 or not re.search(r"\d", s):
        return None
    return s if _STREET.search(s) or re.search(r"#\s*\w", s) else None


def find_phone(text: str, exclude: Optional[str] = None) -> Optional[str]:
    """Celular tras una etiqueta (Teléfono, Cel...) o, si no, el primer número con forma de celular."""
    for m in _PHONE_LABEL.finditer(text):
        v = v_phone(m.group(1))
        if v:
            return v
    # admite un dígito de más pegado delante (el OCR lee la casilla o el borde como un 1)
    for m in re.finditer(r"(?<!\d)\d?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)", text):
        v = v_phone(m.group(0))
        if v and v != exclude:
            return v
    return None


def find_email(text: str) -> Optional[str]:
    return v_email(text)


def find_address(boxes: Optional[List[OcrBox]]) -> Optional[str]:
    """Línea de la dirección: la que sigue a la etiqueta 'Dirección' o, si no, la que tiene forma de dirección
    (tipo de vía con número, o '#'). Se quita una etiqueta mal leída ('Dvecn=') al principio."""
    for b in boxes or []:
        m = _ADDRESS_LABEL.match(b.text)
        if m and v_address(m.group("rest")):
            return v_address(m.group("rest"))
    for b in boxes or []:
        text = b.text
        if "#" in text and re.search(r"\d", text) and not text.lstrip().startswith(("A-", "No")):
            head, sep, tail = text.partition("=")  # "Dvecn= C 4E # 1,madi" -> lo que sigue al '='
            v = v_address(tail if sep and len(head) <= 10 else text)
            if v:
                return v
    for b in boxes or []:
        if _STREET.search(b.text):
            v = v_address(b.text)
            if v:
                return v
    return None


def extract_contact(text: str, boxes: Optional[List[OcrBox]], exclude_number: Optional[str] = None) -> Dict[str, Optional[str]]:
    return {"celular": find_phone(text, exclude_number), "correo": find_email(text), "direccion": find_address(boxes)}
