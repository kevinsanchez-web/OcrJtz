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
