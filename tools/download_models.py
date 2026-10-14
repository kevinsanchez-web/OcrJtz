"""Descarga el modelo de reconocimiento latino (PP-OCRv5) que lee acentos, eñes y espacios entre palabras.

    python tools/download_models.py

Se guarda en models_extra/ (~8 MB). Sin este archivo la API usa el modelo por defecto, que escribe "Senor" y "Ano".
Es un único archivo ONNX del proyecto RapidOCR (ModelScope); no instala nada más.
"""
import sys
import urllib.request
from pathlib import Path

URL = ("https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.4.0/onnx/PP-OCRv5/rec/"
       "latin_PP-OCRv5_rec_mobile_infer.onnx")
DEST = Path(__file__).resolve().parent.parent / "models_extra" / "latin_PP-OCRv5_rec_mobile_infer.onnx"
EXPECTED_SIZE = 7_904_513


def main() -> int:
    if DEST.is_file() and DEST.stat().st_size == EXPECTED_SIZE:
        print(f"Ya está descargado: {DEST}")
        return 0
    DEST.parent.mkdir(exist_ok=True)
    print("Descargando el modelo (~8 MB)...")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    if len(data) != EXPECTED_SIZE or data[:15].lower().startswith((b"<!doctype", b"<html")):
        print(f"La descarga no es el modelo esperado ({len(data)} bytes). No se guardó.", file=sys.stderr)
        return 1
    DEST.write_bytes(data)
    print(f"Guardado en {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
