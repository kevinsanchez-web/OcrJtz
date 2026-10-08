#!/usr/bin/env bash
# Despliegue en Jetson (aarch64): ./deploy.sh
# Se puede volver a ejecutar para actualizar (reconstruye y reinicia).
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Falta Docker. Instálalo: https://docs.docker.com/engine/install/ubuntu/"; exit 1; }
if docker compose version >/dev/null 2>&1; then DC="docker compose"; 
elif command -v docker-compose >/dev/null; then DC="docker-compose"; 
else echo "Falta el plugin 'docker compose' (sudo apt install docker-compose-v2)"; exit 1; fi
docker info >/dev/null 2>&1 || { echo "Sin permisos para Docker. Usa: sudo usermod -aG docker \$USER y vuelve a iniciar sesión (o ejecuta con sudo)."; exit 1; }

show_api_info() {
  set -a; . ./.env; set +a
  KEY="${API_KEYS%%,*}"
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  PATH_API="/api/v1/analyze-document"
  if [ "$DOMAIN" = ":80" ]; then
    NET_URL="http://${IP:-IP_DE_LA_JETSON}${PATH_API}"
  else
    NET_URL="https://${DOMAIN}${PATH_API}"
  fi
  echo
  echo "=============================================================="
  echo " API CREADA"
  echo "=============================================================="
  echo " Desde la propia Jetson:  http://127.0.0.1:8000${PATH_API}"
  echo " Desde otros equipos:     ${NET_URL}"
  echo " Salud:                   GET ${NET_URL%$PATH_API}/health"
  echo
  echo " Configura la otra app con:"
  echo "   JETSON_API_URL=\"${NET_URL}\""
  echo "   JETSON_API_KEY=\"${KEY}\""
  echo "=============================================================="
  echo " (la clave está en .env; no la compartas por chats ni repositorios)"
}

if [ ! -f .env ]; then
  echo "== Primera configuración =="
  read -rp "Dominio público (ej. api.midominio.com). Deja vacío para HTTP sin dominio (solo pruebas/red local): " DOMAIN
  KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')
  if [ -n "$DOMAIN" ]; then HOSTS="$DOMAIN"; else DOMAIN=":80"; HOSTS="*"; fi
  cat > .env <<ENV
DOMAIN=$DOMAIN
API_KEYS=$KEY
ALLOWED_HOSTS=$HOSTS
MAX_UPLOAD_MB=20
MAX_PDF_PAGES=20
MAX_CONCURRENT_JOBS=2
RATE_LIMIT_PER_MIN=30
MAX_AUTH_FAILURES=10
ENABLE_DOCS=0
ENV
  chmod 600 .env
  echo
  echo "Clave de API generada (guárdala, es la que usará la otra app):"
  echo "  $KEY"
  echo
fi

$DC up -d --build
echo
echo "Esperando a que la API arranque (la primera vez carga los modelos)..."
for i in $(seq 1 40); do
  if $DC exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" >/dev/null 2>&1; then
    echo "API lista."; $DC ps; show_api_info; exit 0
  fi
  sleep 3
done
echo "La API no respondió a tiempo. Revisa: $DC logs api"; exit 1
