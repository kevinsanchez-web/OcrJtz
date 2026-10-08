import io
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
import pymupdf  # PyMuPDF
import numpy as np
from PIL import Image
from typing import Any, Dict, List, Optional, Tuple
from fastapi import Depends, FastAPI, File, HTTPException, Request, Security, UploadFile, status
from fastapi.security import APIKeyHeader
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from rapidocr_onnxruntime import RapidOCR
from rapid_layout import RapidLayout

logger = logging.getLogger("document-api")

# ------------------------------------------------------------------------------
# CONFIGURACIÓN DE SEGURIDAD (API servicio-a-servicio, fail-closed)
# ------------------------------------------------------------------------------

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "20"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
MIN_CONFIDENCE = 0.3  # por debajo de esto el documento se descarta

# Protección contra "decompression bombs": imágenes con demasiados píxeles
Image.MAX_IMAGE_PIXELS = 50_000_000

# Claves válidas, separadas por comas (permite rotación sin downtime). Obligatorias.
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", os.getenv("API_KEY", "")).split(",") if k.strip()]
if not API_KEYS:
    raise RuntimeError("Define la variable de entorno API_KEYS (o API_KEY); la API no arranca sin autenticación.")
if any(len(k) < 32 for k in API_KEYS):
    raise RuntimeError("Cada API key debe tener al menos 32 caracteres (usa: python -c \"import secrets;print(secrets.token_urlsafe(32))\").")

# Hosts permitidos en el header Host (ej. "api.interno.local,10.0.0.5"). "*" = sin restricción.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h.strip()]
if ALLOWED_HOSTS != ["*"]:
    # Siempre permitir acceso local (healthcheck de Docker y apps en la misma máquina)
    ALLOWED_HOSTS += [h for h in ("127.0.0.1", "localhost") if h not in ALLOWED_HOSTS]
# Swagger/ReDoc desactivados por defecto
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0") == "1"

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)


RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))   # peticiones por IP
MAX_AUTH_FAILURES = int(os.getenv("MAX_AUTH_FAILURES", "10"))      # fallos de clave por IP cada 5 min

_rate_lock = threading.Lock()
_requests_by_ip: Dict[str, deque] = defaultdict(deque)
_failures_by_ip: Dict[str, deque] = defaultdict(deque)


def _hit(bucket: Dict[str, deque], ip: str, window: float, limit: int, record: bool = True) -> bool:
    """Ventana deslizante en memoria. Devuelve True si NO se ha superado el límite."""
    now = time.monotonic()
    with _rate_lock:
        q = bucket[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        if record:
            q.append(now)
        elif not q:
            bucket.pop(ip, None)
        return True


def require_api_key(request: Request, key: Optional[str] = Security(api_key_header)) -> None:
    ip = request.client.host if request.client else "unknown"
    too_many = HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Demasiadas solicitudes",
        headers={"Retry-After": "60"},
    )
    # Bloqueo temporal tras muchos intentos fallidos (anti fuerza bruta)
    if not _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES, record=False):
        raise too_many

    # Se compara contra todas las claves sin cortocircuitar, en tiempo constante
    valid = False
    if key:
        for k in API_KEYS:
            valid |= secrets.compare_digest(key.encode(), k.encode())
    if not valid:
        _hit(_failures_by_ip, ip, 300, MAX_AUTH_FAILURES)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autorizado",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not _hit(_requests_by_ip, ip, 60, RATE_LIMIT_PER_MIN):
        raise too_many


app = FastAPI(
    title="Document Analysis & Extraction API",
    version="1.0.0",
    description="API de análisis e identificación de documentos vehiculares y personales.",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# Sin CORS a propósito: la consumen servidores, no navegadores.
if ALLOWED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Las respuestas contienen datos personales: nunca deben cachearse
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


ocr_engine = RapidOCR()
layout_engine = RapidLayout(conf_threshold=0.5)

# ------------------------------------------------------------------------------
# MODELOS PYDANTIC
# ------------------------------------------------------------------------------

class DocumentItem(BaseModel):
    found: bool
    documentType: str  # "cedula" | "factura" | "runt" | "gases" | "poder"
    extractedData: Dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str

class AnalyzeDocumentResponse(BaseModel):
    documents: List[DocumentItem]

# ------------------------------------------------------------------------------
# FUNCIONES AUXILIARES DE OCR
# ------------------------------------------------------------------------------

def extract_text_from_ocr(ocr_output) -> Tuple[str, List[float]]:
    """Devuelve el texto (una línea OCR por renglón) y los scores de cada línea."""
    if not ocr_output or not isinstance(ocr_output, (tuple, list)):
        return "", []
    ocr_res = ocr_output[0]
    if not ocr_res or not isinstance(ocr_res, list):
        return "", []

    lines, scores = [], []
    for item in ocr_res:
        if isinstance(item, (list, tuple)) and len(item) > 1 and item[1]:
            lines.append(str(item[1]))
            if len(item) > 2:
                try:
                    scores.append(float(item[2]))
                except (TypeError, ValueError):
                    pass
    return "\n".join(lines).strip(), scores


def process_image_bytes(img_bytes: bytes) -> Tuple[str, float]:
    """OCR de una imagen. Devuelve (texto, score medio del OCR en 0..1)."""
    try:
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        logger.exception("No se pudo abrir la imagen")
        return "", 0.0

    img_np = np.array(pil_img)
    extracted_texts: List[str] = []
    all_scores: List[float] = []

    try:
        layout_output = layout_engine(img_np)
        boxes = getattr(layout_output, "boxes", None)
        if boxes is not None and len(boxes) > 0:
            # Orden de lectura: de arriba a abajo, luego izquierda a derecha
            valid = [b for b in boxes if b is not None]
            for bbox in sorted(valid, key=lambda b: (int(b[1]), int(b[0]))):
                x1 = max(0, int(bbox[0]))
                y1 = max(0, int(bbox[1]))
                x2 = min(pil_img.width, int(bbox[2]))
                y2 = min(pil_img.height, int(bbox[3]))
                if x2 > x1 and y2 > y1:
                    txt, scores = extract_text_from_ocr(ocr_engine(img_np[y1:y2, x1:x2]))
                    if txt:
                        extracted_texts.append(txt)
                        all_scores.extend(scores)
    except Exception:
        logger.exception("Falló el análisis de layout; se usa OCR de página completa")
        extracted_texts, all_scores = [], []

    if not extracted_texts:
        try:
            txt, scores = extract_text_from_ocr(ocr_engine(img_np))
        except Exception:
            logger.exception("Falló el OCR")
            return "", 0.0
        if txt:
            extracted_texts.append(txt)
            all_scores.extend(scores)

    mean_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    return "\n".join(extracted_texts), mean_score


def build_item(doc_type: str, label: str, extracted: Dict[str, Any],
               core_fields: List[str], ocr_score: float) -> DocumentItem:
    """Confianza calculada: proporción de campos clave extraídos, ponderada por el score del OCR."""
    filled = sum(1 for k in core_fields if extracted.get(k))
    ratio = filled / len(core_fields) if core_fields else 0.0
    confidence = round(min(1.0, ratio * ocr_score), 2)
    return DocumentItem(
        found=confidence >= MIN_CONFIDENCE,
        documentType=doc_type,
        extractedData=extracted,
        confidence=confidence,
        summary=f"{label}: {filled}/{len(core_fields)} campos clave extraídos.",
    )


# ------------------------------------------------------------------------------
# EXTRACTION DINÁMICA DE DATOS SEGÚN TEXTO REAL
# ------------------------------------------------------------------------------

def parse_factura(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    text_lower = text.lower()
    if not any(k in text_lower for k in ["factura", "exito", "almacenes exito", "valor total", "factura electronica"]):
        return None

    # Búsqueda precisa para evitar tomar la palabra "DE" de "FACTURA ELECTRONICA DE VENTA"
    num_fac = re.search(r'\b(?:NUMERO|NÚMERO|NO)\b\.?\s*[:#]?\s*([A-Z0-9-]*\d[A-Z0-9-]*)', text, re.IGNORECASE)
    if not num_fac:
        num_fac = re.search(r'FACTURA\s+(?:ELECTRONICA\s+)?(?:DE\s+VENTA\s+)?(?:N[Oº°\.]?|NUMERO)\s*[:#]?\s*([A-Z0-9-]*\d[A-Z0-9-]*)', text, re.IGNORECASE)

    fecha = re.search(r'\b(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-[A-Z]{3}-\d{4})\b', text, re.IGNORECASE)

    nombre = re.search(r'ESTIMADO\(A\)\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s+NUT|\s+IDENTIFICACION|\n|$)', text, re.IGNORECASE)
    if not nombre:
        nombre = re.search(r'(?:RESPONSABLE|COMPRADOR|CLIENTE)\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s+IDENTIFICACION|\s+NIT|\d|\n|$)', text, re.IGNORECASE)
    if not nombre:
        nombre = re.search(r'([a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,}\s+[a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,}(?:\s+[a-zA-ZÁÉÍÓÚÑáéíóúñ]{3,})?)\s*\n?\s*IDENTIFICACION', text, re.IGNORECASE)

    ident = re.search(r'(?:IDENTIFICACION|NIT|CC)\s*[:#]?\s*(\d+)', text, re.IGNORECASE)
    marca = re.search(r'MARCA(?:\s*Y\s*LINEA)?\s*[:#]?\s*([a-zA-Z0-9 -]+?)(?=\s+MODELO|\s+LINEA|\n|$)', text, re.IGNORECASE)
    linea = re.search(r'(?:REFERENCIA|LINEA)\s*[:#]?\s*([a-zA-Z0-9-]+)', text, re.IGNORECASE)
    modelo = re.search(r'MODELO\s*[:#]?\s*(\d{4})', text, re.IGNORECASE)
    motor = re.search(r'MOTOR\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    chasis = re.search(r'CHASIS\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    total = re.search(r'(?:VALOR\s+TOTAL|(?<!SUB)\bTOTAL)\s*[:$]?\s*([\d\.,]+)', text, re.IGNORECASE)
    cilin = re.search(r'CILINDRAJE\s*[:#]?\s*(\d+)', text, re.IGNORECASE)
    color = re.search(r'COLOR\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)

    extracted = {
        "numeroFactura": num_fac.group(1).strip() if num_fac else None,
        "fecha": fecha.group(1).strip() if fecha else None,
        "compradorNombre": nombre.group(1).strip() if nombre else None,
        "compradorIdentificacion": ident.group(1).strip() if ident else None,
        "vehiculoMarca": marca.group(1).strip() if marca else None,
        "vehiculoLinea": linea.group(1).strip() if linea else None,
        "vehiculoModelo": modelo.group(1).strip() if modelo else None,
        "vehiculoMotor": motor.group(1).strip() if motor else None,
        "vehiculoChasis": chasis.group(1).strip() if chasis else None,
        "valorTotal": total.group(1).strip() if total else None,
        "vehiculoCilindrada": cilin.group(1).strip() if cilin else None,
        "vehiculoColor": color.group(1).strip() if color else None
    }
    return build_item("factura", "Factura de venta electrónica", extracted, ["numeroFactura", "fecha", "compradorNombre", "compradorIdentificacion", "valorTotal"], ocr_score)

def parse_runt(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    text_lower = text.lower()
    if not any(k in text_lower for k in ["runt", "registro nacional automotor", "solicitud de tramites", "formulario de solicitud"]):
        return None

    tramite = re.search(r'TRAMITE\s*SOLICITADO\s*[:#]?\s*(?:\[X\]\s*)?([A-Z\/ ]+?)(?=\n|$|\d)', text, re.IGNORECASE)
    doc = re.search(r'\b(?:CC|C\.C\.?|IDENTIFICACION|DOCUMENTO)\s*[:#]?\s*(\d+)', text, re.IGNORECASE)

    primer_ap = re.search(r'PRIMER\s+APELLIDO\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)
    segundo_ap = re.search(r'SEGUNDO\s+APELLIDO\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)
    nombres = re.search(r'NOMBRES?\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*(?:CC|C\.C|IDENTIFICACION|DIRECCION|TELEFONO|\d{2}\.|\n|$))', text, re.IGNORECASE)

    nombre_completo = None
    if nombres and primer_ap:
        nombre_completo = f"{nombres.group(1).strip()} {primer_ap.group(1).strip()} {segundo_ap.group(1).strip() if segundo_ap else ''}".strip()
    elif nombres:
        nombre_completo = nombres.group(1).strip()

    marca = re.search(r'MARCA\s*[:#]?\s*([a-zA-Z0-9 -]+?)(?=\s+LINEA|\n|$)', text, re.IGNORECASE)
    linea = re.search(r'LINEA\s*[:#]?\s*([a-zA-Z0-9-]+)', text, re.IGNORECASE)
    motor = re.search(r'(?:NO\.?\s*)?MOTOR\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    chasis = re.search(r'(?:NO\.?\s*)?CHASIS\s*[:#]?\s*([A-Z0-9]+)', text, re.IGNORECASE)
    modelo = re.search(r'MODELO\s*[:#]?\s*(\d{4})', text, re.IGNORECASE)
    cilin = re.search(r'CILINDRAJE\s*[:#]?\s*(\d+)', text, re.IGNORECASE)
    color = re.search(r'COLOR(?:ES)?\s*[:#]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ]+)', text, re.IGNORECASE)

    extracted = {
        "tramiteSolicitado": tramite.group(1).strip() if tramite else None,
        "propietarioNombre": nombre_completo,
        "propietarioIdentificacion": doc.group(1).strip() if doc else None,
        "vehiculoMarca": marca.group(1).strip() if marca else None,
        "vehiculoLinea": linea.group(1).strip() if linea else None,
        "vehiculoMotor": motor.group(1).strip() if motor else None,
        "vehiculoChasis": chasis.group(1).strip() if chasis else None,
        "vehiculoModelo": modelo.group(1).strip() if modelo else None,
        "vehiculoCilindrada": cilin.group(1).strip() if cilin else None,
        "vehiculoColor": color.group(1).strip() if color else None
    }
    return build_item("runt", "Formulario de Solicitud RUNT", extracted, ["tramiteSolicitado", "propietarioNombre", "propietarioIdentificacion", "vehiculoMarca", "vehiculoMotor", "vehiculoChasis"], ocr_score)

def parse_gases(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    text_lower = text.lower()
    if not any(k in text_lower for k in ["empadronamiento", "emisiones de gases", "gases por prueba"]):
        return None

    fecha = re.search(r'FECHA\s*[:#]?\s*([0-9]{1,2}-[A-Z]{3}-\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
    marca = re.search(r'MARCA\s*[\n,"]*\s*([a-zA-Z0-9 -]+?)(?=\s+NO|\s+MODELO|\n|$)', text, re.IGNORECASE)
    chasis = re.search(r'(?:NO\.?\s*)?CHASIS\s+([A-Z0-9]+)', text, re.IGNORECASE)
    motor = re.search(r'(?:NO\.?\s*)?MOTOR\s+([A-Z0-9]+)', text, re.IGNORECASE)
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
        "vehiculoModelo": modelo.group(1).strip() if modelo else None
    }
    return build_item("gases", "Certificado de gases y empadronamiento", extracted, ["fechaCertificado", "vehiculoMarca", "vehiculoChasis", "vehiculoMotor", "vehiculoModelo"], ocr_score)

def parse_cedula(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    text_lower = text.lower()
    if not any(k in text_lower for k in ["cedula", "cédula", "republica de colombia", "ciudadania", "nuip"]):
        return None

    doc = (re.search(r'(?:NUMERO|NÚMERO|NUIP)\.?\s*[:#]?\s*(\d{1,2}[\.,]?\d{3}[\.,]?\d{3})\b', text, re.IGNORECASE)
           or re.search(r'\b(\d{1,2}[\.,]?\d{3}[\.,]?\d{3})\b', text))
    num_doc = doc.group(1).replace(".", "").replace(",", "") if doc else None

    apellidos = re.search(r'APELLIDOS\s*[:\n]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*NOMBRES|\n[A-Z]+|$)', text, re.IGNORECASE)
    nombres = re.search(r'NOMBRES\s*[:\n]?\s*([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)(?=\s*FECHA|\s*NUMERO|\n[A-Z]+|$)', text, re.IGNORECASE)

    nombre_comp = None
    if nombres and apellidos:
        nombre_comp = f"{nombres.group(1).strip()} {apellidos.group(1).strip()}"
    elif nombres:
        nombre_comp = nombres.group(1).strip()

    fecha_pat = r'(\d{2}-?[A-Z]{3}-?\d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})'
    fecha_nac = (re.search(r'NACIMIENTO\s*[:\n]?\s*' + fecha_pat, text, re.IGNORECASE)
                 or re.search(fecha_pat, text, re.IGNORECASE))
    sexo = re.search(r'SEXO\s*[:\n"]*\s*([MF])', text, re.IGNORECASE)
    rh = re.search(r'(?:G\.?S\.?\s*)?RH\s*[:\n"]*\s*([0OAB][+-])', text, re.IGNORECASE)
    estatura = re.search(r'ESTATURA\s*[:\n"]*\s*(\d[\.,]\d{2})', text, re.IGNORECASE)

    extracted = {
        "nombreCompleto": nombre_comp,
        "numeroDocumento": num_doc,
        "fechaNacimiento": fecha_nac.group(1).strip() if fecha_nac else None,
        "sexo": sexo.group(1).upper() if sexo else None,
        "rh": rh.group(1).upper().replace("0", "O") if rh else None,
        "estatura": estatura.group(1).strip() if estatura else None,
        "celular": None,
        "correo": None,
        "direccion": None
    }
    return build_item("cedula", "Cédula de ciudadanía", extracted, ["nombreCompleto", "numeroDocumento", "fechaNacimiento", "sexo", "rh"], ocr_score)

def parse_poder(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    text_lower = text.lower()
    if not any(k in text_lower for k in ["poder especial", "otorgante", "apoderado", "confiero poder", "otorgo poder"]):
        return None

    otorgante = re.search(r'Yo\s+([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)\s+identificado', text, re.IGNORECASE)
    doc_otorgante = re.search(r'C\.?C\.?\s*\(?[xX]?\)?\s*(\d+)', text, re.IGNORECASE)
    apoderado = re.search(r'(?:Sr|Sra|Sr@)\s+([a-zA-ZÁÉÍÓÚÑáéíóúñ\s]+?)\s+identificado', text, re.IGNORECASE)
    placa = re.search(r'PLACAS?\s*([A-Z0-9]+)', text, re.IGNORECASE)

    tramites = []
    if placa:
        placa_clean = re.sub(r'([A-Z]{3})[oO0]{2,3}(\d|\b)', r'\g<1>00\2', placa.group(1).strip())
        tramites.append(f"Matrícula inicial y trámites de tránsito para vehículo de placas {placa_clean}")
    else:
        tramites.append("Gestiones y trámites de tránsito encomendados")

    extracted = {
        "otorganteNombre": otorgante.group(1).strip() if otorgante else None,
        "otorganteIdentificacion": doc_otorgante.group(1).strip() if doc_otorgante else None,
        "tramitesAutorizados": tramites,
        "apoderadoNombre": apoderado.group(1).strip() if apoderado else None
    }
    return build_item("poder", "Poder especial de tránsito", extracted, ["otorganteNombre", "otorganteIdentificacion", "apoderadoNombre"], ocr_score)

def parse_page_text(text: str, ocr_score: float = 1.0) -> Optional[DocumentItem]:
    """Ejecuta todos los extractores cuyo disparador coincide y devuelve el de mayor confianza."""
    candidates = []
    for parser in (parse_factura, parse_runt, parse_gases, parse_cedula, parse_poder):
        item = parser(text, ocr_score)
        if item and item.found:
            candidates.append(item)
    return max(candidates, key=lambda i: i.confidence) if candidates else None


# ------------------------------------------------------------------------------
# ENDPOINTS PRINCIPALES (Multiruta para compatibilidad y evitar 404)
# ------------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
def health():
    """Chequeo de vida para el balanceador/Docker. No expone datos."""
    return {"status": "ok"}


def _analyze_pdf(contents: bytes) -> List[DocumentItem]:
    detected: List[DocumentItem] = []
    try:
        doc = pymupdf.open(stream=contents, filetype="pdf")
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PDF inválido o corrupto")
    try:
        if doc.needs_pass:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="PDF protegido con contraseña")
        if doc.page_count > MAX_PDF_PAGES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"El PDF excede el máximo de {MAX_PDF_PAGES} páginas",
            )
        for page in doc:
            # 1. Texto nativo (PDFs digitales)
            raw_text = page.get_text()
            if raw_text and len(raw_text.strip()) > 30:
                item = parse_page_text(raw_text)
                if item:
                    detected.append(item)
                    continue

            # 2. Fallback a OCR (páginas escaneadas)
            img_bytes = page.get_pixmap(dpi=200).tobytes("png")
            page_text, score = process_image_bytes(img_bytes)
            if page_text:
                item = parse_page_text(page_text, score)
                if item:
                    detected.append(item)
    finally:
        doc.close()
    return detected


# Ruta canónica + alias (ocultos del esquema OpenAPI) para compatibilidad con clientes existentes
_ROUTE = dict(response_model=AnalyzeDocumentResponse, dependencies=[Depends(require_api_key)])


@app.post("/api/v1/analyze-document", **_ROUTE)
@app.post("/api/v1/analyze-document/", include_in_schema=False, **_ROUTE)
@app.post("/api/v1/analyze-vision", include_in_schema=False, **_ROUTE)
@app.post("/api/v1/analyze-vision/", include_in_schema=False, **_ROUTE)
@app.post("/api/v1/analyze", include_in_schema=False, **_ROUTE)
@app.post("/analyze", include_in_schema=False, **_ROUTE)
def analyze_document(file: UploadFile = File(...)):
    # def síncrono: FastAPI lo ejecuta en un threadpool y el OCR no bloquea el event loop
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
    if ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato no permitido ({ext or 'sin extensión'}). Tipos aceptados: {', '.join(sorted(allowed))}",
        )

    contents = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    if not _job_slots.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor ocupado, reintenta en unos segundos",
            headers={"Retry-After": "5"},
        )
    try:
        if ext == ".pdf":
            if not contents.startswith(b"%PDF"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo no es un PDF válido")
            detected = _analyze_pdf(contents)
        else:
            detected = []
            image_text, score = process_image_bytes(contents)
            if image_text:
                item = parse_page_text(image_text, score)
                if item:
                    detected.append(item)
        return AnalyzeDocumentResponse(documents=detected)
    except HTTPException:
        raise
    except Exception:
        # No se registra el nombre del archivo: puede contener datos personales
        logger.exception("Error procesando documento")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno procesando el documento",
        )
    finally:
        _job_slots.release()
