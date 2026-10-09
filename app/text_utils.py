"""Utilidades de texto: normalización y búsqueda de palabras clave tolerante a errores de OCR."""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import List


def norm(t: str) -> str:
    """Minúsculas y sin acentos."""
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def squash(t: str) -> str:
    """Solo letras y dígitos, sin acentos ni espacios (el OCR suele pegar las palabras)."""
    return re.sub(r"[^a-z0-9]", "", norm(t))


def fix_ocr_zeros(text: str) -> str:
    """El OCR lee a veces la O de una palabra como cero (NUMER0, MODEL0, EXIT0). Solo se corrigen
    palabras casi totalmente alfabéticas; los códigos mixtos (chasis, motor) no se tocan."""
    def repl(m: "re.Match") -> str:
        tok = m.group(0)
        letters, zeros = sum(c.isalpha() for c in tok), tok.count("0")
        if zeros and letters >= 3 and zeros * 3 <= letters and all(c.isalpha() or c == "0" for c in tok):
            return tok.replace("0", "O")
        return tok
    return re.sub(r"[A-Za-z0-9]+", repl, text)


def has_kw(text: str, keywords: List[str], threshold: float = 0.84) -> bool:
    """Busca palabras clave tolerando errores de OCR (acentos, espacios pegados, letras mal leídas)."""
    t = norm(text)
    tsq = squash(text)
    words = re.findall(r"\w+", t)
    for kw in keywords:
        k = norm(kw)
        if k in t:
            return True
        ksq = squash(kw)
        if len(ksq) >= 8 and ksq in tsq:  # frase con espacios pegados por el OCR
            return True
        if len(k) < 6:  # palabras cortas: solo coincidencia exacta
            continue
        n = len(k.split())
        for i in range(len(words) - n + 1):
            cand = " ".join(words[i:i + n])
            if abs(len(cand) - len(k)) <= 2:
                m = SequenceMatcher(None, cand, k)
                if m.quick_ratio() >= threshold and m.ratio() >= threshold:
                    return True
    return False
