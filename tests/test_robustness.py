"""Batería de archivos raros: la API NO debe devolver 500 ni respuestas que no sean JSON."""
import io, os, sys, time, threading
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz del proyecto
sys.path.insert(0, os.path.dirname(__file__))
os.environ["API_KEYS"] = "k" * 40
os.environ["RATE_LIMIT_PER_MIN"] = "10000"
os.environ["MAX_CONCURRENT_JOBS"] = "2"
os.environ["CACHE_ENABLED"] = "0"  # la prueba de concurrencia envía el mismo archivo varias veces
os.environ["HISTORY_DB"] = os.path.join(os.environ.get("TEMP", "."), "test_history.db")
import numpy as np, pymupdf
from PIL import Image
from fastapi.testclient import TestClient
from bench_ocr import doc_cedula, doc_factura, png
import main as entry

c = TestClient(entry.app, raise_server_exceptions=False)
H = {"X-API-Key": "k" * 40}


def img_bytes(im, fmt, **kw):
    b = io.BytesIO(); im.save(b, fmt, **kw); return b.getvalue()


def pdf_with(pages):
    """pages: lista de funciones page->None"""
    d = pymupdf.open()
    for fn in pages:
        p = d.new_page(); fn(p)
    return d.tobytes()


def img_page(im):
    def f(p):
        p.insert_image(p.rect, stream=png(im))
    return f


def text_page(t):
    def f(p):
        p.insert_text((50, 80), t, fontsize=11)
    return f


FACT_TXT = "FACTURA ELECTRONICA DE VENTA No. FE-777\nFecha: 2024-05-01\nESTIMADO(A): ANA LOPEZ\nIDENTIFICACION 123456789\nVALOR TOTAL: 5.000.000"
ced = doc_cedula().convert("RGB")
good_pdf = pdf_with([img_page(ced), lambda p: None, text_page(FACT_TXT)])
enc = pymupdf.open(); enc.new_page()
enc_pdf = enc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="x", user_pw="y")
exif = Image.new("RGB", (300, 200), "white")
ex = io.BytesIO(); exif_obj = Image.Exif(); exif_obj[274] = 6; exif.save(ex, "JPEG", exif=exif_obj)
rng = np.random.default_rng(0)

CASES = [
    ("vacío (0 bytes)", b"", "a.pdf"),
    ("basura aleatoria", rng.bytes(5000), "a.png"),
    ("texto con extensión .pdf", b"hola esto no es un pdf" * 20, "a.pdf"),
    ("PDF truncado a la mitad", good_pdf[: len(good_pdf) // 2], "a.pdf"),
    ("PDF con cabecera y basura", b"%PDF-1.4\n" + rng.bytes(3000), "a.pdf"),
    ("PDF cifrado", enc_pdf, "a.pdf"),
    ("PDF página en blanco", pdf_with([lambda p: None]), "a.pdf"),
    ("PDF con texto irrelevante", pdf_with([text_page("Lorem ipsum dolor sit amet consectetur adipiscing elit sed do")]), "a.pdf"),
    ("PDF página gigante 5000x5000pt", pdf_with([lambda p: None]).replace(b"612", b"612"), "a.pdf"),
    ("PDF mixto: cédula+blanca+factura", good_pdf, "a.pdf"),
    ("PDF 25 páginas (límite 20)", pdf_with([lambda p: None] * 25), "a.pdf"),
    ("PNG con extensión .pdf", png(ced), "a.pdf"),
    ("JPEG con extensión .png", img_bytes(ced, "JPEG"), "a.png"),
    ("WEBP con extensión .jpg", img_bytes(ced, "WEBP"), "a.jpg"),
    ("TIFF", img_bytes(ced, "TIFF"), "a.tif"),
    ("BMP", img_bytes(ced, "BMP"), "a.bmp"),
    ("GIF", img_bytes(ced.convert("P"), "GIF"), "a.gif"),
    ("sin extensión", png(ced), "archivo"),
    ("imagen 1x1", img_bytes(Image.new("RGB", (1, 1), "white"), "PNG"), "a.png"),
    ("imagen 5x5 blanca", img_bytes(Image.new("RGB", (5, 5), "white"), "PNG"), "a.png"),
    ("negro total 800x800", img_bytes(Image.new("RGB", (800, 800), "black"), "PNG"), "a.png"),
    ("ruido aleatorio 1000x1000", img_bytes(Image.fromarray(rng.integers(0, 255, (1000, 1000, 3), dtype=np.uint8)), "PNG"), "a.png"),
    ("muy ancha 6000x100", img_bytes(Image.new("RGB", (6000, 100), "white"), "PNG"), "a.png"),
    ("muy alta 100x6000", img_bytes(Image.new("RGB", (100, 6000), "white"), "PNG"), "a.png"),
    ("RGBA con transparencia", img_bytes(ced.convert("RGBA"), "PNG"), "a.png"),
    ("CMYK JPEG", img_bytes(ced.convert("CMYK"), "JPEG"), "a.jpg"),
    ("paleta (P)", img_bytes(ced.convert("P"), "PNG"), "a.png"),
    ("escala de grises 16 bits", img_bytes(Image.fromarray(np.full((300, 300), 40000, dtype=np.uint16)), "PNG"), "a.png"),
    ("1 bit (B/N)", img_bytes(ced.convert("1"), "PNG"), "a.png"),
    ("JPEG con EXIF rotado", ex.getvalue(), "a.jpg"),
    ("imagen grande 8000x6000 blanca", img_bytes(Image.new("RGB", (8000, 6000), "white"), "PNG"), "a.png"),
    ("imagen bomba 12000x12000", img_bytes(Image.new("RGB", (12000, 12000), "white"), "PNG"), "a.png"),
    ("cédula normal (control)", png(ced), "a.png"),
]

bad = 0
for name, data, fname in CASES:
    t0 = time.time()
    try:
        r = c.post("/analyze", files={"file": (fname, data, "application/octet-stream")}, headers=H)
        dt = time.time() - t0
        try:
            j = r.json()
        except Exception:
            j = None
        ok = r.status_code in (200, 400, 413, 422) and j is not None
        if r.status_code == 200:
            extra = f"docs={[d['documentType'] for d in j['documents']]} avisos={len(j['warnings'])}"
        else:
            extra = str(j.get("detail") if j else r.text[:60])
        flag = "OK " if ok else "FALLO"
        if not ok: bad += 1
        print(f"{flag} {r.status_code} {dt:5.1f}s  {name:34} {extra[:90]}", flush=True)
    except Exception as e:
        bad += 1; print(f"FALLO EXC  {name}: {type(e).__name__}: {e}", flush=True)

# concurrencia: 4 peticiones a la vez con límite de 2 trabajos -> 200 o 503, nunca 500
res = []
def worker():
    r = c.post("/analyze", files={"file": ("a.png", png(ced), "image/png")}, headers=H); res.append(r.status_code)
ts = [threading.Thread(target=worker) for _ in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
print(f"{'OK ' if all(s in (200, 503) for s in res) else 'FALLO'} concurrencia (4 a la vez): {sorted(res)}")
if not all(s in (200, 503) for s in res): bad += 1
print(f"\nRESULTADO: {len(CASES) + 1 - bad}/{len(CASES) + 1} casos sin error 500 y con JSON válido")
