"""Medida del contacto manuscrito (celular, correo, dirección): corre el análisis completo de una página y compara cada
campo con el valor correcto, con la tasa de error de caracteres (CER; 0 = idéntico).

Uso:
    python tests/eval_contact.py                  # todos los casos de tests/samples/contact_cases.json
    CONTACT_ENHANCE=all python tests/eval_contact.py   # probar otra configuración (off | ocr | all)

tests/samples/contact_cases.json (ignorado por git: son datos personales reales):
    [{"name": "tramite p5", "file": "tramite_real.pdf", "page": 5,
      "expected": {"celular": "3102389606", "correo": "ana.lopez@gmail.com", "direccion": "Cl 24E # 1-147, Madrid Cundinamarca"}}]
`page` solo aplica a PDF (empieza en 1). Se comparan sin mayúsculas, tildes ni puntuación; en el correo tampoco cuentan los puntos.
"""
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("API_KEYS", "k" * 40)
os.environ.setdefault("LOG_LEVEL", "WARNING")
for line in (ROOT / ".env.local").read_text(encoding="utf-8-sig").splitlines() if (ROOT / ".env.local").exists() else []:
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ["KEEP_RECEIVED"] = "0"

import pymupdf  # noqa: E402

from app.config import CONTACT_ENHANCE  # noqa: E402
from app.services.analyzer import _render_dpi, analyze_image  # noqa: E402


def norm(field: str, s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    if field == "celular":
        return re.sub(r"\D", "", s)
    if field == "correo":
        return re.sub(r"[\s._-]", "", s)
    return re.sub(r"[^a-z0-9#-]+", " ", s).strip()


def cer(expected: str, got: str) -> float:
    a, b = expected, got
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(a))


def page_png(path: Path, page: int) -> bytes:
    if path.suffix.lower() == ".pdf":
        doc = pymupdf.open(path)
        p = doc[page - 1]
        return p.get_pixmap(dpi=_render_dpi(p)).tobytes("png")
    return path.read_bytes()


def main() -> None:
    cases = json.loads((ROOT / "tests" / "samples" / "contact_cases.json").read_text(encoding="utf-8"))
    print(f"CONTACT_ENHANCE={CONTACT_ENHANCE} | {len(cases)} caso(s)")
    all_cer, t0 = [], time.time()
    for case in cases:
        item = analyze_image(page_png(ROOT / "tests" / "samples" / case["file"], case.get("page", 1)))
        data = (item.extractedData if item else {}) or {}
        contact = data.get("contacto") or {}
        print(f"\n[{case['name']}]")
        for field, expected in case["expected"].items():
            got = data.get(field) or contact.get(field) or ""
            e = cer(norm(field, expected), norm(field, got))
            all_cer.append(e)
            mark = "✔" if e == 0 else "·" if e <= 0.15 else "✘"
            print(f"  {mark} {field:9} CER {e:4.2f} | leyó: {got!r:48} | correcto: {expected!r}")
    print(f"\nCER medio: {sum(all_cer) / max(1, len(all_cer)):.3f}  ({len(all_cer)} campos, {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
