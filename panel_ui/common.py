"""Piezas compartidas por las pestañas del panel: colores, lectura de .env.local, llamadas a la API."""
import io
import json
import mimetypes
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
URL = f"http://127.0.0.1:{PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BG, PANEL, FG, MUTED = "#0f172a", "#020617", "#e2e8f0", "#64748b"
GREEN, RED, AMBER, BLUE, PURPLE, SLATE = "#22c55e", "#f87171", "#fbbf24", "#2563eb", "#7c3aed", "#475569"

MODES = {  # modalidad -> (ruta, descripción)
    "transito": ("/api/v1/analyze-document", "Documentos de tránsito (campos de la app)"),
    "general": ("/api/v1/ocr", "OCR general (cualquier documento)"),
}
FILETYPES = [("Imágenes y PDF", "*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif"), ("Todos", "*.*")]


def load_env() -> dict:
    """Variables de .env.local (sin la marca BOM que a veces dejan los editores)."""
    env = dict(os.environ)
    f = ROOT / ".env.local"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def key_entries() -> list:
    """Entradas de API_KEYS en .env.local: [(nombre, clave)]; la clave sin nombre es la de tránsito."""
    raw = (load_env().get("API_KEYS") or load_env().get("API_KEY") or "")
    out = []
    for e in (x.strip() for x in raw.split(",")):
        if e:
            name, sep, key = e.rpartition(":")
            out.append((name.strip() if sep and name.strip() else "transito", key.strip()))
    return out


def api_key() -> str:
    """Clave para llamar a la API: la de tránsito o, si no hay, la primera."""
    entries = key_entries()
    for name, key in entries:
        if name == "transito":
            return key
    return entries[0][1] if entries else ""


def _error(e: Exception, t0: float) -> dict:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = json.loads(e.read()).get("detail", "")
        except Exception:
            detail = ""
        hint = {401: "La clave de la API no coincide con .env.local", 413: "El archivo es demasiado grande",
                503: "La API está ocupada: reintenta en unos segundos"}.get(e.code, "")
        return {"ok": False, "status": e.code, "secs": time.time() - t0,
                "error": f"{detail or e.reason} {('- ' + hint) if hint else ''}".strip()}
    return {"ok": False, "status": 0, "secs": time.time() - t0,
            "error": f"No se pudo conectar con la API ({e}). ¿Está encendida?"}


def api_get(path: str, timeout: float = 5) -> dict:
    """GET autenticado: {ok, status, secs, json|error}."""
    t0 = time.time()
    try:
        req = urllib.request.Request(URL + path, headers={"X-API-Key": api_key()})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.status, "secs": time.time() - t0, "json": json.loads(r.read())}
    except Exception as e:
        return _error(e, t0)


def post_file(path: str, name: str, data: bytes) -> dict:
    """Sube un archivo a la API (multipart): {ok, status, secs, json|error}."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(URL + path, data=body, method="POST", headers={
        "X-API-Key": api_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return {"ok": True, "status": r.status, "secs": time.time() - t0, "json": json.loads(r.read())}
    except Exception as e:
        return _error(e, t0)


def run_job(mode: str, name: str, data: bytes, params: str = "", on_progress=None, timeout: float = 1200) -> dict:
    """Sube el archivo como trabajo asíncrono y espera el resultado mostrando el avance (página N de M).
    Devuelve lo mismo que `post_file`: {ok, status, secs, json|error}."""
    t0 = time.time()
    created = post_file(f"/api/v1/jobs?mode={mode}{params}", name, data)
    if not created["ok"]:
        return created
    jid = created["json"]["jobId"]
    while time.time() - t0 < timeout:
        st = api_get(f"/api/v1/jobs/{jid}", timeout=10)
        if not st["ok"]:
            return {**st, "secs": time.time() - t0}
        j = st["json"]
        if on_progress:
            on_progress(j["status"], j.get("progress"))
        if j["status"] == "done":
            return {"ok": True, "status": 200, "secs": time.time() - t0, "json": j["result"], "cached": j.get("cached")}
        if j["status"] == "error":
            e = j.get("error") or {}
            return {"ok": False, "status": e.get("status", 500), "secs": time.time() - t0, "error": e.get("detail", "Error")}
        time.sleep(1.0)
    return {"ok": False, "status": 0, "secs": time.time() - t0, "error": "El trabajo tardó demasiado"}


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def describe(res: dict) -> tuple:
    """(resumen, texto legible) de una respuesta de la API."""
    j = res.get("json") or {}
    if not res["ok"]:
        return f"✘ Error {res['status']}: {res['error']}", ""
    head = f"✔ Funciona · HTTP {res['status']} · {res['secs']:.1f} s"
    lines, readable = [head], []
    if "pages" in j:  # OCR general
        lines.append(f"{len(j['pages'])} página(s) · {len(j.get('text', ''))} caracteres leídos")
        for p in j["pages"]:
            tipo = f" · tipo: {p['documentType']}" if p.get("documentType") else ""
            lines.append(f"  p.{p['page']} ({p['source']}) confianza {p['confidence']:.2f}{tipo}")
            for t in p.get("tables") or []:
                lines.append(f"    tabla de {len(t['rows'])} filas × {max(len(r) for r in t['rows'])} columnas")
        readable.append(j.get("text", ""))
        for p in j["pages"]:
            for n, t in enumerate(p.get("tables") or [], 1):
                readable.append(f"\n--- Tabla {n} (página {p['page']}) ---")
                readable.extend("  " + " | ".join(row) for row in t["rows"])
        if j.get("fields"):
            readable.append("\n--- Campos pedidos ---")
            for k, v in j["fields"].items():
                readable.append(f"  {k}: {v['value'] if v else '(no encontrado)'}")
    for d in j.get("documents", []):
        rev = "  ⚠ revisar" if d.get("needsReview") else ""
        lines.append(f"  Documento p.{d.get('page')}: {d['documentType']} · confianza {d['confidence']:.2f}{rev}")
        readable.append(f"\n[p.{d.get('page')}] {d['documentType']} (confianza {d['confidence']:.2f})")
        for k, v in d["extractedData"].items():
            if isinstance(v, dict):
                v = ", ".join(f"{a}: {b or '-'}" for a, b in v.items())
            readable.append(f"  {k}: {v if v not in (None, '', []) else '-'}")
        if d.get("inheritedFields"):
            readable.append(f"  (completados desde otra página: {', '.join(d['inheritedFields'])})")
    if not j.get("documents") and "pages" not in j:
        lines.append("No se reconoció ningún documento.")
    for w in j.get("warnings", []):
        lines.append(f"  ⚠ {w}")
    return "\n".join(lines), "\n".join(readable)


def selftest_image() -> bytes:
    """Imagen con un texto conocido para comprobar que la lectura funciona de punta a punta."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 260), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 54)
    except Exception:
        f = ImageFont.load_default()
    d.text((40, 40), "PRUEBA OCR 12345", fill="black", font=f)
    d.text((40, 130), "Factura No. 98765", fill="black", font=f)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def ago(ts: Optional[float]) -> str:
    if not ts:
        return "nunca"
    s = int(time.time() - ts)
    return f"hace {s} s" if s < 90 else f"hace {s // 60} min" if s < 5400 else f"hace {s // 3600} h"
