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
ENABLE_PANEL = os.getenv("ENABLE_PANEL", "1") != "0"   # página web /panel para probar documentos (pide la clave)
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0") == "1"      # Swagger/ReDoc desactivados por defecto
# Ver en el panel de escritorio los documentos que llegan (archivo + resultado, SOLO en memoria). Apagado por defecto.
KEEP_RECEIVED = os.getenv("KEEP_RECEIVED", "0") == "1"
RECEIVED_TTL = int(os.getenv("RECEIVED_TTL", "1800"))                      # segundos que se conserva cada uno
RECEIVED_MAX_ITEMS = int(os.getenv("RECEIVED_MAX_ITEMS", "20"))
RECEIVED_MAX_BYTES = int(os.getenv("RECEIVED_MAX_MB", "150")) * 1024 * 1024  # tope de memoria de todos juntos

# --- Límites de uso ---------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "20"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "0"))  # subidas (POST) por minuto por app e IP; 0 = sin límite
RATE_LIMIT_READS_PER_MIN = int(os.getenv("RATE_LIMIT_READS_PER_MIN", "0"))  # consultas (GET) por minuto; 0 = sin límite
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
# Si al archivo le quedan menos segundos que esto, no se consulta al VLM (una consulta en frío tarda ~60 s; con el modelo
# cargado, ~10 s). Con un tope por archivo justo, las últimas páginas se quedaban sin VLM y el contacto manuscrito salía peor.
VLM_MIN_SECONDS = int(os.getenv("VLM_MIN_SECONDS", "90"))
# Preparación del recorte del contacto manuscrito (quitar sombras y subir el contraste): "off", "ocr" (solo las lecturas
# del OCR) o "all" (también la imagen que ve el VLM).
CONTACT_ENHANCE = os.getenv("CONTACT_ENHANCE", "ocr").strip().lower()
MAX_RENDER_PIXELS = 3200  # lado mayor al convertir una página de PDF a imagen (evita agotar la memoria)
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

# Protección contra "decompression bombs": imágenes con demasiados píxeles
Image.MAX_IMAGE_PIXELS = 50_000_000

# --- Calidad de la extracción -----------------------------------------------------
MIN_CONFIDENCE = 0.3       # por debajo de esto el documento se descarta
ENOUGH_CONFIDENCE = 0.8    # con esta confianza no se prueban más mejoras de imagen
EXCELLENT_OCR_SCORE = 0.92  # con una lectura así de buena, mejorar la imagen no ayuda: se deja de reintentar
MAX_SECONDS_PER_IMAGE = int(os.getenv("MAX_SECONDS_PER_IMAGE", "60"))  # tope de reintentos por imagen/página
# Caché en memoria: el mismo archivo con los mismos parámetros se responde al instante (nunca se guarda a disco)
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") != "0"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
CACHE_MAX_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "32"))
# Relectura ampliada de los datos dudosos (cédulas, fechas, chasis). En CPU suma ~7 s por página sin mejora
# medible en las pruebas; actívala (REFOCUS=1) en la Jetson con GPU, donde es casi gratis.
REFOCUS = os.getenv("REFOCUS", "0") == "1"

# --- Seguridad (API servicio-a-servicio, fail-closed) -------------------------------
# Claves válidas, separadas por comas (permite rotación sin downtime). Obligatorias.
# Cada entrada es "clave" o "nombre:clave". Una clave sin nombre es de la app de extracción de documentos de
# tránsito (TRANSIT_CLIENT), así su configuración actual sigue funcionando; otras apps usan su propia clave
# con nombre (ej. API_KEYS=<clave_transito>,contabilidad:<otra_clave>) y reciben el formato general.
TRANSIT_CLIENT = os.getenv("TRANSIT_CLIENT", "transito").strip() or "transito"
API_CLIENTS = []  # [(nombre, clave)]
for _entry in os.getenv("API_KEYS", os.getenv("API_KEY", "")).split(","):
    _entry = _entry.strip()
    if _entry:
        _name, _sep, _key = _entry.rpartition(":")
        API_CLIENTS.append((_name.strip() if _sep and _name.strip() else TRANSIT_CLIENT, _key.strip()))
API_KEYS = [k for _, k in API_CLIENTS]
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
