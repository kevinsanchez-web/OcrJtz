"""Formulario de Solicitud de Trámites RUNT."""
import re
from typing import List, Optional

from app.extraction.base import build_item
from app.extraction.contact import extract_contact
from app.extraction.forms import FormSpec, extract_form, merge_found
from app.extraction.validators import (v_brand, v_chassis, v_date, v_displacement, v_id, v_invoice_no, v_motor,
                                           v_name, v_vehicle_text, v_year)
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import has_kw

KEYWORDS = ["runt", "registro nacional automotor", "solicitud de tramites", "formulario de solicitud"]
CORE_FIELDS = ["tramiteSolicitado", "propietarioNombre", "propietarioIdentificacion",
               "vehiculoMarca", "vehiculoMotor", "vehiculoChasis"]

# En el formulario RUNT el valor va DEBAJO de su etiqueta numerada (ej. "5.MARCA")
SPEC: FormSpec = {
    "vehiculoMarca": (["marca"], v_brand),
    "vehiculoLinea": (["linea"], v_vehicle_text),
    "vehiculoModelo": (["modelo"], v_year),
    "vehiculoCilindrada": (["cilindrada"], v_displacement),
    "vehiculoMotor": (["nodemotor", "motor"], v_motor),
    "vehiculoChasis": (["nodechasis", "chasis"], v_chassis),
    "propietarioIdentificacion": (["nodocumento"], v_id),
}
OTHER_LABELS = ["nodeserie", "capacidad", "combustible", "carroceria", "clasedevehiculo", "tipodeservicio",
                "primerapellido", "segundoapellido", "nombres", "nombre", "ciudad", "direccion", "telefono",
                "potencia", "blindaje", "placa", "tramitesolicitado"]


def parse_runt(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    if not has_kw(text, KEYWORDS):
        return None

    tramite = re.search(r'TRAMITE\s*SOLICITADO\s*[:#]?\s*(?:\[X\]\s*)?([A-Z\/ ]+?)(?=\n|$|\d)', text, re.IGNORECASE)
    doc = re.search(r'\b(?:CC|C\.C\.?|IDENTIFICACION|DOCUMENTO)\s*[:#]?\s*(\d+)', text, re.IGNORECASE)

    primer_ap = re.search(r'PRIMER\s*APELLIDO\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)
    segundo_ap = re.search(r'SEGUNDO\s*APELLIDO\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)
    nombres = re.search(r'NOMBRES?\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*(?:CC|C\.C|IDENTIFICACION|DIRECCION|TELEFONO|\d{2}\.|\n|$))', text, re.IGNORECASE)

    nombre_completo = None
    if nombres and primer_ap:
        nombre_completo = f"{nombres.group(1).strip()} {primer_ap.group(1).strip()} {segundo_ap.group(1).strip() if segundo_ap else ''}".strip()
    elif nombres:
        nombre_completo = nombres.group(1).strip()

    marca = re.search(r'MARCA\s*[:#]?\s*([a-zA-Z0-9 -]+?)(?=\s+LINEA|\n|$)', text, re.IGNORECASE)
    linea = re.search(r'LINEA\s*[:#]?\s*([a-zA-Z0-9-]+)', text, re.IGNORECASE)
    motor = re.search(r'(?:NO\.?\s*)?MOTOR\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    chasis = re.search(r'(?:NO\.?\s*)?CHASIS\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    modelo = re.search(r'MODELO\s*[:#]?\s*(\d{4})', text, re.IGNORECASE)
    cilin = re.search(r'CILINDRAJE\s*[:#]?\s*(\d+)', text, re.IGNORECASE)
    color = re.search(r'COLOR(?:ES)?\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)

    extracted = {
        "tramiteSolicitado": tramite.group(1).strip() if tramite else None,
        "propietarioNombre": nombre_completo,
        "propietarioIdentificacion": doc.group(1).strip() if doc else None,
        "vehiculoMarca": marca.group(1).strip() if marca else None,
        "vehiculoLinea": linea.group(1).strip() if linea else None,
        "vehiculoMotor": motor.group(1).strip() if motor else None,
        "vehiculoChasis": chasis.group(1).strip() if chasis else None,
        "vehiculoModelo": modelo.group(1).strip() if modelo else None,
        "vehiculoCilindrada": cilin.group(1).strip() if cilin else None,
        "vehiculoColor": color.group(1).strip() if color else None,
    }
    if boxes:
        merge_found(extracted, extract_form(boxes, SPEC, "below", OTHER_LABELS))
    extracted.update(extract_contact(text, boxes, extracted.get("propietarioIdentificacion")))
    return build_item("runt", "Formulario de Solicitud RUNT", extracted, CORE_FIELDS, ocr_score)
