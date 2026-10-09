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
