#!/usr/bin/env bash
# Corre EN LA JETSON, una sola vez: sudo ./tools/jetson/instalar_puente_jetson.sh
# Instala cloudflared (arm64), guarda la configuracion del puente en /etc/api-puente.env (solo root) y deja el servicio
# api-puente encendido (arranca con la Jetson). Antes debe estar la API en marcha (./deploy.sh).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Ejecutalo con sudo."; exit 1; }
AQUI="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE=/etc/api-puente.env

if ! command -v cloudflared >/dev/null; then
  case "$(uname -m)" in aarch64|arm64) ARCH=arm64 ;; x86_64) ARCH=amd64 ;; *) echo "Arquitectura no soportada: $(uname -m)"; exit 1 ;; esac
  echo "Descargando cloudflared ($ARCH)..."
  curl -fsSL -o /usr/local/bin/cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH"
  chmod 755 /usr/local/bin/cloudflared
fi
cloudflared --version

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$AQUI/jetson.puente.env" ]; then
    install -m 600 "$AQUI/jetson.puente.env" "$ENV_FILE"
    echo "Configuracion tomada de tools/jetson/jetson.puente.env (borra ese archivo cuando termines)."
  else
    read -rp "Direccion del Worker (https://api-puente.<tu-subdominio>.workers.dev): " WU
    read -rsp "Clave de registro del Worker (no se muestra): " RK; echo
    umask 077
    printf 'TUNNEL_REGISTER_URL=%s\nTUNNEL_REGISTER_KEY=%s\n' "$WU" "$RK" > "$ENV_FILE"
  fi
fi
chmod 600 "$ENV_FILE"

chmod 755 "$AQUI/tunel_jetson.sh"
sed "s#__SCRIPT__#$AQUI/tunel_jetson.sh#" "$AQUI/api-puente.service" > /etc/systemd/system/api-puente.service
systemctl daemon-reload
systemctl enable --now api-puente
sleep 3
systemctl --no-pager --lines=12 status api-puente || true
echo
echo "Listo. Logs: journalctl -u api-puente -f   |   Apagar: sudo systemctl stop api-puente   |   Estado: systemctl status api-puente"
