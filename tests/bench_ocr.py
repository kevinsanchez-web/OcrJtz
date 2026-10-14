"""Banco de pruebas sintetico: documentos con degradaciones realistas (borroso, rotado, tablas, ruido)."""
import io, os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # raiz del proyecto
os.environ["API_KEYS"] = "k" * 40
os.environ["CACHE_ENABLED"] = "0"
os.environ["HISTORY_DB"] = os.path.join(os.environ.get("TEMP", "."), "test_history.db")
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
from app.services.analyzer import analyze_image

F = r"C:\Windows\Fonts"
def font(name, size): return ImageFont.truetype(os.path.join(F, name), size)

# ---------------- documentos base ----------------
def doc_cedula():
    """Cedula con etiqueta arriba y valor abajo (+ reverso con RH/estatura)."""
    im = Image.new("RGB", (1100, 760), (236, 238, 226)); d = ImageDraw.Draw(im)
    lab, val = font("arial.ttf", 20), font("arialbd.ttf", 34)
    d.text((40, 20), "REPUBLICA DE COLOMBIA", fill="black", font=font("arialbd.ttf", 30))
    d.text((40, 60), "IDENTIFICACION PERSONAL  CEDULA DE CIUDADANIA", fill="black", font=lab)
    rows = [("NUMERO", "1.234.567.890"), ("APELLIDOS", "GOMEZ PEREZ"), ("NOMBRES", "JUAN CARLOS")]
    y = 110
    for l, v in rows:
        d.text((40, y), l, fill=(60, 60, 60), font=lab); d.text((40, y + 26), v, fill="black", font=val); y += 95
    d.line((40, 410, 1060, 410), fill="gray", width=2)
    d.text((40, 430), "FECHA DE NACIMIENTO", fill=(60, 60, 60), font=lab); d.text((40, 456), "15-MAR-1990", fill="black", font=val)
    d.text((420, 430), "ESTATURA", fill=(60, 60, 60), font=lab); d.text((420, 456), "1.75", fill="black", font=val)
    d.text((40, 540), "G.S. RH", fill=(60, 60, 60), font=lab); d.text((40, 566), "O+", fill="black", font=val)
    d.text((420, 540), "SEXO", fill=(60, 60, 60), font=lab); d.text((420, 566), "M", fill="black", font=val)
    return im
CEDULA = {"numeroDocumento": "1234567890", "sexo": "M", "rh": "O+", "estatura": "1.75"}
def check_cedula(ed):
    r = {k: (ed.get(k) == v) for k, v in CEDULA.items()}
    nm = (ed.get("nombreCompleto") or "").upper().replace(" ", "")
    r["nombre"] = "JUAN" in nm and "GOMEZ" in nm
    r["fechaNac"] = (ed.get("fechaNacimiento") or "").upper().replace("-", "") == "15MAR1990"
    return r

def doc_factura():
    """Factura con tabla de lineas (rejilla) y datos de comprador."""
    im = Image.new("RGB", (1200, 900), "white"); d = ImageDraw.Draw(im)
    b, n = font("arialbd.ttf", 30), font("arial.ttf", 26)
    d.text((40, 20), "ALMACENES EXITO S.A.", fill="black", font=b)
    d.text((40, 70), "FACTURA ELECTRONICA DE VENTA No. FE-12345", fill="black", font=b)
    d.text((40, 120), "Fecha: 2024-03-15", fill="black", font=n)
    d.text((40, 160), "ESTIMADO(A): ANA MARIA LOPEZ", fill="black", font=n)
    d.text((40, 200), "IDENTIFICACION 900123456", fill="black", font=n)
    x0, y0, cw, rh = 40, 270, [520, 180, 220, 240], 54
    hdr = ["DESCRIPCION", "CANT", "VR UNIT", "VR TOTAL"]
    data = [["Vehiculo MARCA RENAULT", "1", "1.250.000", "1.250.000"], ["Seguro obligatorio", "1", "0", "0"], ["Matricula", "1", "0", "0"]]
    for r, row in enumerate([hdr] + data):
        x = x0
        for c, t in enumerate(row):
            d.rectangle((x, y0 + r * rh, x + cw[c], y0 + (r + 1) * rh), outline="black", width=2)
            d.text((x + 10, y0 + r * rh + 12), t, fill="black", font=b if r == 0 else n); x += cw[c]
    d.text((640, y0 + 5 * rh), "VALOR TOTAL: 1.250.000", fill="black", font=b)
    return im
FACT = {"numeroFactura": "FE-12345", "fecha": "2024-03-15", "compradorIdentificacion": "900123456", "valorTotal": "1.250.000"}
def check_factura(ed):
    r = {k: (ed.get(k) == v) for k, v in FACT.items()}
    r["comprador"] = "ANA" in (ed.get("compradorNombre") or "").upper()
    return r

# ---------------- degradaciones ----------------
def rot(im, a): return im.rotate(a, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
def blur(im, s): return Image.fromarray(cv2.GaussianBlur(np.array(im), (0, 0), s))
def lowres(im, f):
    w, h = im.size; return im.resize((int(w * f), int(h * f)), Image.BILINEAR)
def jpeg(im, q):
    b = io.BytesIO(); im.save(b, "JPEG", quality=q); return Image.open(io.BytesIO(b.getvalue())).convert("RGB")
def noise(im, s):
    a = np.array(im).astype(np.float32); rng = np.random.default_rng(1)
    return Image.fromarray(np.clip(a + rng.normal(0, s, a.shape), 0, 255).astype(np.uint8))
def shadow(im):
    a = np.array(im).astype(np.float32); h, w = a.shape[:2]
    g = np.linspace(0.45, 1.0, w)[None, :, None]; return Image.fromarray((a * g).astype(np.uint8))
def lowcontrast(im):
    a = np.array(im).astype(np.float32); return Image.fromarray((128 + (a - 128) * 0.45).astype(np.uint8))
def perspective(im, k=0.08):
    a = np.array(im); h, w = a.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[w * k, h * k], [w, 0], [w * (1 - k), h * (1 - k)], [0, h]])
    m = cv2.getPerspectiveTransform(src, dst)
    return Image.fromarray(cv2.warpPerspective(a, m, (w, h), borderValue=(200, 200, 200)))
def chain(im, *fs):
    for f in fs: im = f(im)
    return im

CASES = [
    ("limpia", lambda i: i),
    ("rot 90", lambda i: rot(i, 90)),
    ("rot 180", lambda i: rot(i, 180)),
    ("rot 270", lambda i: rot(i, 270)),
    ("rot 15", lambda i: rot(i, 15)),
    ("rot -30", lambda i: rot(i, -30)),
    ("rot 40", lambda i: rot(i, 40)),
    ("rot 90+20", lambda i: rot(i, 110)),
    ("borrosa 2", lambda i: blur(i, 2.0)),
    ("borrosa 3.5", lambda i: blur(i, 3.5)),
    ("baja res 0.4", lambda i: lowres(i, 0.4)),
    ("baja res .3+jpeg", lambda i: jpeg(lowres(i, 0.3), 30)),
    ("ruido+jpeg", lambda i: jpeg(noise(i, 25), 35)),
    ("sombra", shadow),
    ("poco contraste", lowcontrast),
    ("perspectiva", perspective),
    ("rot 90+borrosa", lambda i: blur(rot(i, 90), 2.5)),
    ("rot 25+borrosa+jpeg", lambda i: jpeg(blur(rot(i, 25), 2.0), 40)),
    ("foto: persp+sombra+blur", lambda i: blur(shadow(perspective(rot(i, 8))), 1.6)),
]

def png(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

def run(only=None, docs=("cedula", "factura")):
    base = {"cedula": (doc_cedula(), "cedula", check_cedula), "factura": (doc_factura(), "factura", check_factura)}
    tot = ok = 0; fails = []
    for dn in docs:
        img, typ, chk = base[dn]
        print(f"\n=== {dn} ===")
        for name, f in CASES:
            if only and not any(o in name for o in only): continue
            t0 = time.time(); item = analyze_image(png(f(img))); sc = item.confidence if item else 0.0
            if not item or item.documentType != typ:
                res = {"TIPO": False}; got = item.documentType if item else None
            else:
                res = chk(item.extractedData); got = typ
            good = sum(res.values()); n = len(res); tot += n if got == typ else len(chk(base[dn][0] and {})); ok += good
            bad = [k for k, v in res.items() if not v]
            print(f"{name:26} {time.time()-t0:5.1f}s ocr={sc:.2f} tipo={got!s:8} {good}/{n} {'' if not bad else 'FALLA: ' + ','.join(bad)}")
            if bad: fails.append((dn, name, bad))
    print(f"\nTOTAL campos correctos: {ok}/{tot}   casos con fallos: {len(fails)}")

if __name__ == "__main__":
    only = sys.argv[1].split("|") if len(sys.argv) > 1 and sys.argv[1] != "all" else None
    run(only)



