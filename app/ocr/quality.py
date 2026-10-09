"""Métricas de calidad de una lectura de OCR."""
import re
from typing import Tuple

from app.ocr.engine import OcrResult
from app.text_utils import squash

# Palabras típicas de los documentos: distinguen una página al derecho de una al revés
LEXICON = ["republica", "colombia", "cedula", "ciudadania", "numero", "apellidos", "nombres", "fecha",
           "nacimiento", "estatura", "sexo", "factura", "total", "identificacion", "vehiculo", "marca",
           "modelo", "motor", "chasis", "placa", "poder", "tramite", "runt", "valor", "cliente", "gases",
           "empadronamiento", "descripcion", "cantidad", "linea", "color", "cilindraje"]


def stats(res: OcrResult) -> Tuple[int, float]:
    """(caracteres leídos, score medio ponderado por longitud)."""
    chars = [len(re.findall(r"\w", t)) for _, t, _ in res]
    total = sum(chars)
    mean = sum(c * sc for c, (_, _, sc) in zip(chars, res)) / total if total else 0.0
    return total, mean


def quality(res: OcrResult) -> float:
    """Más caracteres, más confianza y más palabras de documento reconocidas = mejor lectura."""
    total, mean = stats(res)
    if not total:
        return 0.0
    squashed = squash(" ".join(t for _, t, _ in res))
    hits = sum(1 for w in LEXICON if w in squashed)
    return total * mean * (1 + 0.15 * hits)


def is_good(res: OcrResult) -> bool:
    total, mean = stats(res)
    return total >= 80 and mean >= 0.88
