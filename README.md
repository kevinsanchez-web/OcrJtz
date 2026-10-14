# Document Analysis API (Jetson Thor)

API FastAPI que analiza cédulas, facturas, RUNT, certificados de gases y poderes (PDF/imagen) con OCR.

## Despliegue en la Jetson

Requisitos: JetPack con Docker y `docker compose` instalados.

```bash
git clone <URL_DEL_REPOSITORIO> api && cd api
./deploy.sh
```

El script crea `.env` (pide el dominio y genera la clave de API), construye la imagen y levanta la API detrás de Caddy (HTTPS automático).
Para actualizar: `git pull && ./deploy.sh`.

### Para que sea accesible desde internet
- **Con IP pública / port-forward:** registro DNS A del dominio → IP pública; abre los puertos 80 y 443 hacia la Jetson.
- **Sin IP pública (CGNAT):** usa un túnel (Cloudflare Tunnel o Tailscale Funnel) apuntando a `http://localhost:80` y deja el dominio vacío en `deploy.sh`.
- No expongas el puerto 8000: no se publica.

## Estructura del código

```
main.py                    Punto de entrada (uvicorn main:app)
app/
  main.py                  Crea la aplicación FastAPI (middlewares y rutas)
  config.py                Variables de entorno y límites
  security.py              API key, límite de peticiones, bloqueo por intentos, cabeceras
  schemas.py               Formato de la respuesta (DocumentItem)
  text_utils.py            Normalización de texto y búsqueda de palabras clave tolerante a errores de OCR
  api/routes.py            Endpoints (/health y /api/v1/analyze-document + alias)
  services/analyzer.py     Orquesta imagen/PDF -> documentos (sin HTTP)
  llm/                     Respaldo opcional con modelo de lenguaje local (Ollama)
  ocr/
    engine.py              Motor RapidOCR y su llamada protegida
    boxes.py               Líneas de texto con posición y reconstrucción por renglones
    quality.py             Métricas de calidad de una lectura
    preprocess.py          Carga de imagen, giro y mejoras (borrosa, pequeña, sombras)
    orientation.py         Sentido (0/180) e inclinación de cualquier ángulo
    reader.py              Lecturas progresivas hasta obtener una buena
  extraction/
    pipeline.py            Prueba cada tipo y devuelve el de mayor confianza (lista PARSERS)
    base.py                Cálculo de confianza y construcción del resultado
    validators.py          Valida cada campo por su forma (un dato vacío es mejor que uno falso)
    spatial.py             Valor de un campo según su posición respecto a la etiqueta
    cedula.py  factura.py  runt.py  gases.py  poder.py     Un archivo por tipo de documento
```

**Añadir un tipo de documento:** crear `app/extraction/<tipo>.py` con una función `parse_<tipo>` (ver `factura.py`)
y agregarla a `PARSERS` en `pipeline.py`.

## Respaldo con modelo de lenguaje (opcional)

Si una página queda con confianza baja, un modelo local (Ollama) puede completar los campos vacíos. Está
**apagado por defecto**. Para activarlo: instala Ollama, `ollama pull qwen2.5`, y define en `.env`
`LLM_URL=http://127.0.0.1:11434` (y opcionalmente `LLM_MODEL`).

- Solo pide los campos que faltan, y cada valor debe tener la forma del campo y **aparecer en el texto del OCR**
  (el modelo no puede inventar dígitos). Si falla o tarda más de `LLM_TIMEOUT`, se ignora.
- En CPU un modelo de 7B tarda ~50-100 s por página; úsalo con GPU o con documentos de pocas páginas.
- El código está en `app/llm/`.

## Robustez y respuesta

La API **no devuelve 500 por un archivo raro**: el tipo se detecta por el contenido (no por la extensión), una página
dañada no tumba el resto del PDF, hay un tope de tiempo por archivo (`MAX_SECONDS_PER_DOCUMENT`, 240 s) que devuelve
lo ya procesado, y las imágenes en blanco o diminutas se descartan al instante.

```json
{
  "documents": [{"documentType": "cedula", "confidence": 0.94, "page": 5, "needsReview": false, "extractedData": {}}],
  "warnings": ["Página 3: no se reconoció ningún documento."]
}
```
- `needsReview: true` → confianza baja (menos de 0.8): conviene que una persona lo revise.
- `warnings` → páginas ilegibles, documentos no reconocidos, páginas omitidas por tiempo o por el límite de páginas.
- Un campo que no se pudo leer o no tiene la forma esperada viene `null`, nunca con un dato inventado.

**Pruebas:** `python tests/test_robustness.py` (34 archivos raros: vacíos, corruptos, cifrados, mal etiquetados, gigantes...)
y `python tests/bench_ocr.py all` (cédula y factura con 19 degradaciones). Requieren `API_KEYS` y fuentes de Windows.

## Uso
```bash
curl -X POST https://TU_DOMINIO/api/v1/analyze-document \
  -H "X-API-Key: TU_CLAVE" -F "file=@cedula.pdf"
```
Salud: `GET /health`. Respuestas: 200, 400, 401, 413, 422, 429, 503.

## Configuración (.env)
`API_KEYS` (obligatoria, separadas por coma para rotar), `ALLOWED_HOSTS`, `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `MAX_CONCURRENT_JOBS`, `RATE_LIMIT_PER_MIN`, `MAX_AUTH_FAILURES`, `ENABLE_DOCS`.

## Logs
`docker compose logs -f api`

## OCR general (cualquier documento)

Además del endpoint de documentos de tránsito, `POST /api/v1/ocr` lee **cualquier** PDF o imagen
(contratos, cartas, facturas de cualquier empresa, formularios, recibos...), sin asumir su contenido.

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/ocr" -H "X-API-Key: TU_CLAVE" -F "file=@documento.pdf"
```

Respuesta: `text` (todo el texto), y por cada página en `pages`: `text`, `confidence`, `source`
(`native` = texto digital del PDF, `ocr` = leído de la imagen), `lines` (cada línea con `box` = [x0,y0,x1,y1]
relativo a la página, 0 a 1) y `keyValues` (pares "Etiqueta: valor" detectados). Si el documento es de un tipo
conocido (cédula, factura, RUNT, gases, poder), además llegan sus campos estructurados en `documents`.

Parámetros opcionales: `?lines=false` (respuesta más liviana, sin posiciones) y `?structured=false`
(no buscar campos de tipos conocidos). Usa la misma lectura robusta (orientación, inclinación, borrosidad,
límites de tiempo y de tamaño) y la misma autenticación que el resto.

Límite conocido: el modelo de OCR actual no distingue bien los acentos ni la eñe ("Señor" → "Senor",
"Año" → "Ano") ni algunos caracteres de otros idiomas; para cambiarlo haría falta otro modelo de reconocimiento.

## Varias apps usando la misma API

Cada app tiene **su propia clave**, y la clave dice qué app llama. En `API_KEYS` (separadas por comas):

```
API_KEYS=CLAVE_DE_TRANSITO,contabilidad:OTRA_CLAVE_DE_32_O_MAS_CARACTERES
```

- Una clave **sin nombre** es la de la app de extracción de documentos de tránsito (`TRANSIT_CLIENT`, por defecto
  `transito`): su configuración actual no cambia.
- Una clave `nombre:clave` es de otra app. Genera la clave con
  `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
- El registro muestra de qué app viene cada petición: `PETICION de la app 'contabilidad' ...`.
- **La carga de otra app no interrumpe a tránsito:** el límite por minuto es por app, y las otras apps no pueden
  ocupar todos los trabajos simultáneos (siempre queda un cupo libre para tránsito).
- `POST /api/v1/analyze-document` conserva el formato de la app de tránsito. `POST /api/v1/ocr` (cualquier
  documento) entrega a tránsito sus campos con la forma que su interfaz espera (`contacto` anidado) y a las demás
  apps los campos planos.
- Para quitar el acceso a una app basta con borrar su clave de `API_KEYS` y reiniciar.

## Trabajos asíncronos (PDF largos y redes que cortan peticiones largas)

```bash
# 1. Subir: responde al instante con un jobId
curl -X POST "http://127.0.0.1:8000/api/v1/jobs?mode=transit" -H "X-API-Key: TU_CLAVE" -F "file=@documento.pdf"
# 2. Consultar hasta que status sea "done" (o "error"); mientras procesa trae progress {page,total}
curl "http://127.0.0.1:8000/api/v1/jobs/JOB_ID" -H "X-API-Key: TU_CLAVE"
```

`mode=transit` devuelve lo mismo que `/api/v1/analyze-document`; `mode=ocr` lo mismo que `/api/v1/ocr`
(con `&lines=true`, `&fields=...`, `&deep=true` opcionales). El resultado queda en memoria 30 minutos
(`DELETE /api/v1/jobs/{id}` lo borra) y solo lo puede leer la app que lo creó. Un trabajo en cola nunca se rechaza
con 503: espera su turno. Máximo 10 trabajos pendientes por app (429 si se excede).

## Campos a pedido y tablas (OCR general)

- `POST /api/v1/ocr?fields=numeroContrato,total,fecha,correo`: devuelve `fields` con el valor de cada campo pedido
  (`value`, `source`, `page`) o `null` si no aparece. Busca entre los pares "etiqueta: valor" y las líneas del texto,
  con sinónimos (total ~ valor total ~ monto) y tipos (fecha, correo, teléfono, importe). Con `&deep=true` los campos
  que no estén en el texto se le piden al modelo de visión (~1 min por página, y solo se acepta un valor que también
  aparezca en lo que leyó el OCR: nunca inventa).
- Cada página trae `tables`: filas y columnas (`rows`) de las tablas detectadas.

## Panel de escritorio y panel web

- **Escritorio (Windows):** `panel_api.pyw` (acceso directo "Panel API OCR"). Enciende/apaga la API, prueba
  documentos con avance en vivo y vista con recuadros, estadísticas, y creación/revocación de claves por app.
- **Web:** `http://<servidor>:8000/panel` desde cualquier dispositivo que llegue a la API (arrastrar y soltar, vista
  con recuadros, estadísticas). Pide la clave de la app y la guarda solo en ese navegador. `ENABLE_PANEL=0` la apaga.
- `GET /api/v1/stats` (con clave): totales, tiempos, errores y últimas peticiones. El historial vive en
  `data/history.db` y **solo guarda metadatos** (app, servicio, tamaño, tiempo, estado), nunca el contenido de los
  archivos. En Docker monta `data/` como volumen si quieres conservarlo.
- **Caché en memoria:** el mismo archivo con los mismos parámetros se responde al instante (`CACHE_ENABLED=0` la apaga;
  `CACHE_TTL_SECONDS`, `CACHE_MAX_ENTRIES`). Nunca se escribe a disco.

## Medir la precisión con tus documentos

Copia documentos reales a `tests/samples/` con un `<nombre>.expected.json` por cada uno (formato en el encabezado de
`tests/evaluate.py`) y ejecuta `python tests/evaluate.py`. Muestra qué campos acierta, compara con una referencia
(`--save-baseline`) y avisa si un cambio empeora. `tests/samples/` no se sube a GitHub (datos personales).

## Acentos y eñe (modelo de lectura latino)

La API usa el modelo de reconocimiento latino PP-OCRv5 si existe `models_extra/latin_PP-OCRv5_rec_mobile_infer.onnx`
(se descarga con `python tools/download_models.py`; el Dockerfile lo hace al construir). Lee "Señor Núñez", "Año",
"¿Cómo estás?" y los espacios entre palabras, y mejoró el banco de pruebas de 200 a 205 de 209 campos. Sin el
archivo la API sigue funcionando con el modelo por defecto (sin acentos). `OCR_REC_MODEL=` (vacío) lo desactiva.

## Formatos aceptados

PDF, imágenes (JPG, PNG, WEBP, TIFF, BMP, GIF y **HEIC/HEIF** de iPhone), y **Word (.docx) y Excel (.xlsx)**, cuyo texto
y tablas se leen directamente sin OCR (cada hoja de Excel es una página). Los .doc y .xls antiguos responden 415:
guárdalos como .docx/.xlsx o PDF.
