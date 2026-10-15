#!/usr/bin/env bash
# Corre EN LA JETSON (lo arranca el servicio api-puente): abre un tunel temporal de Cloudflare hacia la API local y
# avisa su direccion al Worker de direccion fija, para que la app siga usando siempre la misma JETSON_API_URL.
# Si el tunel se cae, el script termina y systemd lo vuelve a lanzar (nueva direccion, vuelve a registrarse sola).
# Variables (de /etc/api-puente.env): TUNNEL_REGISTER_URL, TUNNEL_REGISTER_KEY. Opcionales: LOCAL_API, CLOUDFLARED.
set -uo pipefail

ENV_FILE="${ENV_FILE:-/etc/api-puente.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
: "${TUNNEL_REGISTER_URL:?Falta TUNNEL_REGISTER_URL (direccion del Worker)}"
: "${TUNNEL_REGISTER_KEY:?Falta TUNNEL_REGISTER_KEY (clave de registro del Worker)}"
WORKER="${TUNNEL_REGISTER_URL%/}"
LOCAL="${LOCAL_API:-http://127.0.0.1:8000}"
CF="${CLOUDFLARED:-cloudflared}"
LOG="${RUNTIME_DIRECTORY:-${TMPDIR:-/tmp}}/cloudflared.log"
UA="User-Agent: jetson-puente/1.0"   # Cloudflare bloquea el User-Agent por defecto de algunos clientes

registrar() {  # registrar <accion: set|clear> [url]
  if [ "$1" = "set" ]; then
    curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST -H "X-Register-Key: $TUNNEL_REGISTER_KEY" -H "$UA" \
      -H 'Content-Type: application/json' -d "{\"url\":\"$2\"}" "$WORKER/__tunnel" 2>/dev/null
  else
    curl -sS -m 20 -o /dev/null -w '%{http_code}' -X DELETE -H "X-Register-Key: $TUNNEL_REGISTER_KEY" -H "$UA" \
      "$WORKER/__tunnel" 2>/dev/null
  fi
}

PID=""
limpiar() {
  echo "[puente] cerrando: quito la direccion fija y apago el tunel"
  registrar clear >/dev/null || true
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
  exit 0
}
trap limpiar TERM INT

echo "[puente] esperando a la API en $LOCAL ..."
for _ in $(seq 1 120); do
  curl -fsS -m 3 "$LOCAL/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS -m 3 "$LOCAL/health" >/dev/null 2>&1 || { echo "[puente] la API no responde en $LOCAL"; exit 1; }

mkdir -p "$(dirname "$LOG")"
"$CF" tunnel --url "$LOCAL" --http-host-header "127.0.0.1:8000" --no-autoupdate >"$LOG" 2>&1 &
PID=$!

URL=""
for _ in $(seq 1 40); do
  sleep 2
  kill -0 "$PID" 2>/dev/null || { echo "[puente] cloudflared termino antes de dar una direccion:"; tail -n 5 "$LOG"; exit 1; }
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | grep -v '^https://api\.' | tail -n 1 || true)
  [ -n "$URL" ] && break
done
[ -n "$URL" ] || { echo "[puente] el tunel no dio una direccion (revisa la salida a internet de la Jetson)"; kill "$PID" 2>/dev/null; exit 1; }
echo "[puente] tunel temporal: $URL"

for intento in 1 2 3 4 5; do
  codigo=$(registrar set "$URL")
  [ "$codigo" = "200" ] && { echo "[puente] direccion fija actualizada: $WORKER"; break; }
  echo "[puente] registro HTTP ${codigo:-sin respuesta} (intento $intento)"; sleep 5
done

# Vigilancia: si el tunel muere, se sale (systemd lo relanza); si el Worker pierde el enlace (503), se vuelve a registrar
while kill -0 "$PID" 2>/dev/null; do
  sleep 30 &
  wait $! || true
  codigo=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -H "$UA" "$WORKER/health" 2>/dev/null || true)
  if [ "$codigo" = "503" ]; then
    echo "[puente] la direccion fija perdio el enlace: vuelvo a registrar"
    registrar set "$URL" >/dev/null || true
  fi
done
echo "[puente] cloudflared se detuvo"
registrar clear >/dev/null || true
exit 1
