"""Factura de venta electrónica de vehículo (extracción por regex, tolerante a palabras pegadas)."""
import re
from typing import List, Optional

from app.extraction.base import build_item
from app.extraction.forms import FormSpec, extract_form, merge_found
from app.extraction.validators import (v_chassis, v_date, v_displacement, v_id, v_invoice_no, v_motor,
                                           v_name, v_vehicle_text, v_year)
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import has_kw

KEYWORDS = ["factura", "exito", "almacenes exito", "valor total", "factura electronica"]
CORE_FIELDS = ["numeroFactura", "fecha", "compradorNombre", "compradorIdentificacion", "valorTotal"]

SPEC: FormSpec = {
    "numeroFactura": (["facturaelectronicadeventan", "facturadeventan", "numerofactura"], v_invoice_no),
    "fecha": (["fechadeexpedicion", "fecha"], v_date),
    "compradorNombre": (["estimadoa", "estimado", "cliente", "comprador"], v_name),
    "compradorIdentificacion": (["numdocumento", "identificacion"], v_id),
}
OTHER_LABELS = ["direccion", "telefono", "nit", "subtotal", "descuento", "total", "iva", "base"]


def parse_factura(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    if not has_kw(text, KEYWORDS):
        return None

    # Búsqueda precisa para evitar tomar la palabra "DE" de "FACTURA ELECTRONICA DE VENTA"
    _id = r'([A-Z]{0,5}-?\d[A-Z0-9-]*)'
    num_fac = (re.search(r'FACTURA[A-Z\s]*?N[Oº°]\.?\s*[:#.]?\s*' + _id, text, re.IGNORECASE)
               or re.search(r'(?:\bN[Oº°]\.?|N[UÚ]MERO)\s*[:#.]?\s*' + _id, text, re.IGNORECASE))

    fecha = re.search(r'\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-[A-Z]{3}-\d{4})\b', text, re.IGNORECASE)

    nombre = re.search(r'ESTIMAD[O0]\s*(?:\([A-Z]?\)|[^:\n]{0,5}:)?\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s+NUT|\s+IDENTIFICACION|\n|$)', text, re.IGNORECASE)
    if not nombre:
        nombre = re.search(r'(?:RESPONSABLE|COMPRADOR|CLIENTE)\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s+IDENTIFICACION|\s+NIT|\d|\n|$)', text, re.IGNORECASE)
    if not nombre:
        nombre = re.search(r'([a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,}\s+[a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,}(?:\s+[a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,})?)\s*\n?\s*IDENTIFICACION', text, re.IGNORECASE)

    # Comprador: IDENTIFICACION/CC primero; el NIT que aparece suele ser el del emisor (ej. Almacenes Éxito)
    ident = (re.search(r'(?:IDENTIFICACION|C\.?C\.?|CEDULA)\s*[:#=]?\s*(\d{5,})', text, re.IGNORECASE)
             or next((m for m in re.finditer(r'NIT\s*[:#]?\s*(\d{6,})', text, re.IGNORECASE)
                      if not m.group(1).startswith("890900608")), None))
    marca = re.search(r'MARCA(?:\s*Y\s*LINEA)?\s*[:#]?\s*([a-zA-Z0-9 -]+?)(?=\s+MODELO|\s+LINEA|\n|$)', text, re.IGNORECASE)
    linea = re.search(r'(?<![A-Za-z])(?:REFERENCIA|LINEA)\s*[:#]?\s*([a-zA-Z0-9-]+)', text, re.IGNORECASE)
    modelo = re.search(r'MODELO\s*[:#]?\s*(\d{4})', text, re.IGNORECASE)
    motor = re.search(r'MOTOR\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    chasis = re.search(r'CHASIS\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    total = (re.search(r'VALOR\s*TOTAL\s*[:$]?\s*([\d\.,]+)', text, re.IGNORECASE)
             or re.search(r'VALOR\s*TOTAL[^0-9\n]{0,40}(\d{1,3}(?:[.,]\d{3})+)', text, re.IGNORECASE))
    if not total:  # el mayor importe con formato de dinero precedido de TOTAL (sin contar SUBTOTAL)
        money = list(re.finditer(r'(?<!SUB)TOTAL\W{0,3}(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})?)', text, re.IGNORECASE))
        total = max(money, key=lambda m: int(re.sub(r"\D", "", re.sub(r"[.,]\d{2}$", "", m.group(1))))) if money else None
    if not total:
        total = re.search(r'(?<!SUB)\bTOTAL\s*[:$]?\s*([\d\.,]+)', text, re.IGNORECASE)
    transaccion = re.search(r'TOTAL\s*TRANSACCI[O0]N\s*[:#=]?\s*\$?\s*(\d[\d.,]*)', text, re.IGNORECASE)
    cilin = re.search(r'CILINDRAJE\s*[:#]?\s*(\d+)', text, re.IGNORECASE)
    color = re.search(r'COLOR\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)

    extracted = {
        "numeroFactura": num_fac.group(1).strip() if num_fac else None,
        "fecha": fecha.group(1).strip() if fecha else None,
        "compradorNombre": nombre.group(1).strip() if nombre else None,
        "compradorIdentificacion": ident.group(1).strip() if ident else None,
        "vehiculoMarca": marca.group(1).strip() if marca else None,
        "vehiculoLinea": linea.group(1).strip() if linea else None,
        "vehiculoModelo": modelo.group(1).strip() if modelo else None,
        "vehiculoMotor": motor.group(1).strip() if motor else None,
        "vehiculoChasis": chasis.group(1).strip() if chasis else None,
        "valorTotal": total.group(1).strip() if total else None,
        "totalTransaccion": transaccion.group(1).strip(".,") if transaccion else None,
        "vehiculoCilindrada": cilin.group(1).strip() if cilin else None,
        "vehiculoColor": color.group(1).strip() if color else None,
    }
    if boxes:
        merge_found(extracted, extract_form(boxes, SPEC, "both", OTHER_LABELS))
    return build_item("factura", "Factura de venta electrónica", extracted, CORE_FIELDS, ocr_score)
