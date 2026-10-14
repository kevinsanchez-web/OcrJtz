"""Evaluador de precisión: corre la API sobre documentos reales y los compara con los valores correctos.

Uso:
    python tests/evaluate.py                  # todos los documentos de tests/samples/
    python tests/evaluate.py cedula           # solo los que tengan "cedula" en el nombre
    python tests/evaluate.py --save-baseline  # guarda este resultado como referencia
    python tests/evaluate.py --vlm            # con el modelo de visión (más lento)

Por cada archivo tests/samples/<nombre>.<pdf|png|jpg...> debe haber un <nombre>.expected.json:

    {"documents": [
        {"type": "cedula", "page": 5, "fields": {"numeroDocumento": "1233498817", "contacto.celular": "3102389606"}},
        {"type": "factura", "fields": {"valorTotal": 17590000}}
    ]}

`page` es opcional (si falta se usa el primer documento de ese tipo). Los campos anidados se escriben con punto.
Los documentos reales llevan datos personales: tests/samples/ está en .gitignore y no se sube a GitHub.
"""
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("API_KEYS", "k" * 40)
os.environ.setdefault("CACHE_ENABLED", "0")
os.environ.setdefault("HISTORY_DB", os.path.join(os.environ.get("TEMP", "."), "eval_history.db"))
if "--vlm" not in sys.argv:
    os.environ["LLM_URL"] = ""  # sin modelo de visión: rápido y reproducible

SAMPLES = Path(__file__).resolve().parent / "samples"
REPORT = Path(__file__).resolve().parent / "last_report.json"
BASELINE = Path(__file__).resolve().parent / "baseline.json"
EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def norm(v) -> str:
    """Comparación tolerante: sin acentos, mayúsculas, espacios ni puntuación; los números por su valor."""
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def get_path(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def evaluate(sample: Path) -> dict:
    from app.services.analyzer import analyze_file
    expected = json.loads(sample.with_suffix("").with_suffix(".expected.json").read_text(encoding="utf-8")) \
        if sample.with_suffix("").with_suffix(".expected.json").exists() else None
    if expected is None:
        return {"file": sample.name, "error": "falta el archivo .expected.json"}
    t0 = time.time()
    items, warnings = analyze_file(sample.read_bytes(), "eval")
    secs = time.time() - t0
    got = [i.model_dump() for i in items]
    rows = []
    for exp in expected["documents"]:
        cands = [d for d in got if d["documentType"] == exp["type"] and (exp.get("page") in (None, d.get("page")))]
        doc = cands[0] if cands else None
        for field, want in exp["fields"].items():
            have = get_path(doc["extractedData"], field) if doc else None
            ok = have is not None and norm(have) == norm(want)
            rows.append({"type": exp["type"], "page": exp.get("page"), "field": field, "expected": want,
                         "got": have, "ok": ok, "docFound": doc is not None})
    return {"file": sample.name, "seconds": round(secs, 1), "rows": rows, "warnings": warnings}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    files = sorted(p for p in SAMPLES.glob("*") if p.suffix.lower() in EXTS and (not args or any(a in p.name for a in args)))
    if not files:
        print(f"No hay documentos en {SAMPLES}. Copia ahí tus archivos y un <nombre>.expected.json por cada uno "
              f"(ver el encabezado de este archivo).")
        return
    results = [evaluate(f) for f in files]
    total = ok = 0
    by_field: dict = {}
    for r in results:
        print(f"\n=== {r['file']} " + (f"({r['seconds']} s)" if "seconds" in r else ""))
        if "error" in r:
            print("  ✘", r["error"])
            continue
        for row in r["rows"]:
            total += 1
            ok += row["ok"]
            st = by_field.setdefault(row["field"], [0, 0])
            st[0] += row["ok"]
            st[1] += 1
            page = f" p.{row['page']}" if row["page"] else ""
            if row["ok"]:
                print(f"  ✔ {row['type']}{page} {row['field']}")
            elif not row["docFound"]:
                print(f"  ✘ {row['type']}{page} {row['field']}: no se reconoció el documento")
            else:
                print(f"  ✘ {row['type']}{page} {row['field']}: esperado {row['expected']!r} · obtenido {row['got']!r}")
    pct = 100 * ok / total if total else 0
    print(f"\nPRECISIÓN: {ok}/{total} campos correctos ({pct:.1f}%)")
    weak = sorted(((f, a, b) for f, (a, b) in by_field.items() if a < b), key=lambda x: x[1] / x[2])
    if weak:
        print("Campos con fallos:", ", ".join(f"{f} ({a}/{b})" for f, a, b in weak))
    report = {"accuracy": round(pct, 1), "correct": ok, "total": total, "results": results}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if "--save-baseline" in sys.argv:
        BASELINE.write_text(json.dumps({"accuracy": round(pct, 1), "correct": ok, "total": total}), encoding="utf-8")
        print("Referencia guardada.")
    elif BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        diff = pct - base["accuracy"]
        print(f"Frente a la referencia ({base['accuracy']}%): {'+' if diff >= 0 else ''}{diff:.1f} puntos "
              f"{'✔ mejora o igual' if diff >= 0 else '✘ EMPEORÓ'}")
    print(f"Reporte completo: {REPORT}")


if __name__ == "__main__":
    main()
