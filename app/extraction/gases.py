"""Certificado de emisiones de gases y empadronamiento."""
import re
from typing import List, Optional

from app.extraction.base import build_item
from app.extraction.forms import FormSpec, extract_form, merge_found
from app.extraction.validators import (v_chassis, v_date, v_displacement, v_id, v_invoice_no, v_motor,
                                           v_name, v_vehicle_text, v_year)
from app.ocr.boxes import OcrBox
from app.schemas import DocumentItem
from app.text_utils import has_kw

KEYWORDS = ["empadronamiento", "emisiones de gases", "gases por prueba"]
CORE_FIELDS = ["fechaCertificado", "vehiculoMarca", "vehiculoChasis", "vehiculoMotor", "vehiculoModelo"]

# El certificado de empadronamiento pone el valor a la derecha y el de gases debajo de la etiqueta
SPEC: FormSpec = {
    "fechaCertificado": (["fechaprueba", "fecha"], v_date),
    "vehiculoMarca": (["marca"], v_vehicle_text),
    "vehiculoLinea": (["lineareferencia", "linearef", "linea"], v_vehicle_text),
    "vehiculoModelo": (["modeloano", "modelo"], v_year),
    "vehiculoCilindrada": (["cilindradacc", "cilindrada"], v_displacement),
    "vehiculoMotor": (["nomotor", "motorno", "motor"], v_motor),
    "vehiculoChasis": (["nochasis", "novin", "serialno", "chasis"], v_chassis),
}
OTHER_LABELS = ["clasedevehiculo", "color", "nopasajeros", "pesobruto", "noaceptacion", "fechaaceptacion",
                "nolevante", "fechalevante", "nit", "nombre", "ciudad", "dpto", "telefono", "direccion",
                "vehicular", "tiempos"]


def parse_gases(text: str, ocr_score: float = 1.0, boxes: Optional[List[OcrBox]] = None) -> Optional[DocumentItem]:
    if not has_kw(text, KEYWORDS):
        return None

    fecha = re.search(r'FECHA\s*[:#]?\s*([0-9]{1,2}-[A-Z]{3}-\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
    marca = re.search(r'MARCA\s*[\n,"]*\s*([a-zA-Z0-9 -]+?)(?=\s+NO|\s+MODELO|\n|$)', text, re.IGNORECASE)
    chasis = re.search(r'(?:NO\.?\s*)?CHASIS\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    motor = re.search(r'(?:NO\.?\s*)?MOTOR\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    cilin = re.search(r'CILINDRA(?:DA|JE)\s*\(?CC\)?\s*(\d+)', text, re.IGNORECASE)
    linea = re.search(r'LINEA(?:/REF)?\s*[:#\n,"]*\s*([a-zA-Z0-9-]+)', text, re.IGNORECASE)
    modelo = re.search(r'MODELO(?:\s*\(AÑO\))?\s*[:#\n,"]*\s*(\d{4})', text, re.IGNORECASE)

    extracted = {
        "tipoCertificado": "EMISIONES DE GASES / EMPADRONAMIENTO",
        "fechaCertificado": fecha.group(1).strip() if fecha else None,
        "vehiculoMarca": marca.group(1).strip() if marca else None,
        "vehiculoChasis": chasis.group(1).strip() if chasis else None,
        "vehiculoMotor": motor.group(1).strip() if motor else None,
        "vehiculoCilindrada": cilin.group(1).strip() if cilin else None,
        "vehiculoLinea": linea.group(1).strip() if linea else None,
        "vehiculoModelo": modelo.group(1).strip() if modelo else None,
    }
    if boxes:
        merge_found(extracted, extract_form(boxes, SPEC, "both", OTHER_LABELS))
    return build_item("gases", "Certificado de gases y empadronamiento", extracted, CORE_FIELDS, ocr_score)
