"""Configuración de la aplicación, leída de variables de entorno (ver .env.example)."""
import logging
import os

from PIL import Image

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

# --- Depuración -------------------------------------------------------------------
LOG_OCR_TEXT = os.getenv("LOG_OCR_TEXT", "0") == "1"    # SOLO depurar: imprime el texto leído (datos personales)
LOG_FILENAMES = os.getenv("LOG_FILENAMES", "0") == "1"  # el nombre del archivo puede contener datos personales
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0") == "1"      # Swagger/ReDoc desactivados por defecto

# --- Límites de uso ---------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "20"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))  # peticiones por IP
MAX_AUTH_FAILURES = int(os.getenv("MAX_AUTH_FAILURES", "10"))    # fallos de clave por IP cada 5 min
PDF_RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "200"))  # 150 es más rápido pero cambia la lectura de formularios densos
# --- Respaldo opcional con un modelo de lenguaje local (Ollama). Apagado si LLM_URL está vacío ------------
LLM_URL = os.getenv("LLM_URL", "").rstrip("/")             # ej. http://127.0.0.1:11434
LLM_MODE = os.getenv("LLM_MODE", "vision").lower()          # "vision": el modelo ve la imagen (VLM); "text": solo el texto del OCR
VLM_MODEL = os.getenv("VLM_MODEL", "qwen2.5vl:3b")          # modelo de visión-lenguaje
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:latest")        # modelo de solo texto (modo "text")
VLM_MAX_SIDE = int(os.getenv("VLM_MAX_SIDE", "1024"))       # lado mayor de la imagen enviada al VLM (menos = más rápido)
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))          # segundos por llamada; si se agota se ignora el modelo
MAX_SECONDS_PER_DOCUMENT = int(os.getenv("MAX_SECONDS_PER_DOCUMENT", "240"))  # tope por archivo: devuelve lo procesado
MAX_RENDER_PIXELS = 3200  # lado mayor al convertir una página de PDF a imagen (evita agotar la memoria)
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

# Protección contra "decompression bombs": imágenes con demasiados píxeles
Image.MAX_IMAGE_PIXELS = 50_000_000

# --- Calidad de la extracción -----------------------------------------------------
MIN_CONFIDENCE = 0.3       # por debajo de esto el documento se descarta
ENOUGH_CONFIDENCE = 0.8    # con esta confianza no se prueban más mejoras de imagen
EXCELLENT_OCR_SCORE = 0.92  # con una lectura así de buena, mejorar la imagen no ayuda: se deja de reintentar
MAX_SECONDS_PER_IMAGE = int(os.getenv("MAX_SECONDS_PER_IMAGE", "60"))  # tope de reintentos por imagen/página
# Relectura ampliada de los datos dudosos (cédulas, fechas, chasis). En CPU suma ~7 s por página sin mejora
# medible en las pruebas; actívala (REFOCUS=1) en la Jetson con GPU, donde es casi gratis.
REFOCUS = os.getenv("REFOCUS", "0") == "1"

# --- Seguridad (API servicio-a-servicio, fail-closed) -------------------------------
# Claves válidas, separadas por comas (permite rotación sin downtime). Obligatorias.
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", os.getenv("API_KEY", "")).split(",") if k.strip()]
if not API_KEYS:
    raise RuntimeError("Define la variable de entorno API_KEYS (o API_KEY); la API no arranca sin autenticación.")
if any(len(k) < 32 for k in API_KEYS):
    raise RuntimeError(
        "Cada API key debe tener al menos 32 caracteres "
        "(usa: python -c \"import secrets;print(secrets.token_urlsafe(32))\")."
    )

# Hosts permitidos en el header Host (ej. "api.interno.local,10.0.0.5"). "*" = sin restricción.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h.strip()]
if ALLOWED_HOSTS != ["*"]:
    # Siempre permitir acceso local (healthcheck de Docker y apps en la misma máquina)
    ALLOWED_HOSTS += [h for h in ("127.0.0.1", "localhost") if h not in ALLOWED_HOSTS]
