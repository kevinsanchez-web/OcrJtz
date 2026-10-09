"""Vocabularios del dominio (vehículos en Colombia) para corregir errores de OCR por similitud.

Solo se corrige cuando la lectura es MUY parecida a una palabra conocida; si no se parece a ninguna se deja
tal cual (una marca poco común no debe ser "corregida" a otra).
"""
from difflib import SequenceMatcher
from typing import Iterable, Optional

from app.text_utils import norm

BRANDS = [
    "AKT", "AUDI", "AUTECO", "BAJAJ", "BENELLI", "BMW", "BYD", "CHANGAN", "CHERY", "CHEVROLET", "CITROEN",
    "DAEWOO", "DFSK", "DODGE", "DUCATI", "FIAT", "FORD", "FOTON", "GAC", "GREAT WALL", "HERO", "HINO", "HONDA",
    "HYUNDAI", "ISUZU", "JAC", "JEEP", "JMC", "KAWASAKI", "KIA", "KTM", "KYMCO", "LAND ROVER", "LIFAN",
    "MAHINDRA", "MAZDA", "MERCEDES BENZ", "MG", "MINI", "MITSUBISHI", "NISSAN", "PEUGEOT", "RENAULT", "ROYAL ENFIELD",
    "SEAT", "SKODA", "SSANGYONG", "SUBARU", "SUZUKI", "TOYOTA", "TVS", "VICTORY", "VOLKSWAGEN", "VOLVO",
    "YAMAHA", "ZONTES",
]
COLORS = [
    "BLANCO", "NEGRO", "GRIS", "PLATA", "PLATEADO", "ROJO", "AZUL", "VERDE", "AMARILLO", "NARANJA", "BEIGE",
    "DORADO", "CAFE", "MARRON", "VINOTINTO", "MORADO", "ROSADO", "BRONCE", "TURQUESA",
]


def canonical(value: str, vocabulary: Iterable[str], cutoff: float = 0.84) -> Optional[str]:
    """La palabra del vocabulario a la que se parece `value`, o None si no se parece a ninguna."""
    key = norm(value).strip().upper()
    best, best_r = None, 0.0
    for word in vocabulary:
        if key == word:
            return word
        if abs(len(word) - len(key)) > 2:
            continue
        r = SequenceMatcher(None, key, word).ratio()
        if r > best_r:
            best, best_r = word, r
    return best if best_r >= cutoff else None
