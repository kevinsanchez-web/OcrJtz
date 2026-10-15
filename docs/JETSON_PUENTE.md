# Pasar el puente a la Jetson (misma dirección fija, mismas claves)

Objetivo: que la app de tránsito siga usando **la misma** `JETSON_API_URL` y `JETSON_API_KEY`, pero que quien responda sea la
API de la Jetson y no este PC. **La app no se modifica.**

```
App / Firebase ──▶ Worker de Cloudflare (dirección fija) ──▶ túnel temporal ──▶ API en la Jetson (127.0.0.1:8000)
```

El Worker (`api-puente`, `tools/worker/worker.js`) guarda una sola dirección de túnel y reenvía todo a ella. Quien registre
su túnel último es quien responde. En la Jetson lo hace el servicio `api-puente`.

## Lo que la app necesita (ya lo tiene)
- `JETSON_API_URL="https://api-puente.<tu-subdominio>.workers.dev/api/v1/analyze-document"` (en el `.env` de la app).
- `JETSON_API_KEY` = la clave sin nombre de `API_KEYS` de la Jetson. Debe ser **la misma** que ya tiene la app.

## Archivos con tus valores (están en `tools/jetson/`, ignorados por git, contienen claves)
| Archivo | Para qué | Dónde va en la Jetson |
|---|---|---|
| `jetson.api.env` | `.env` de la API (mismas `API_KEYS`, sin dominio, límites 30/600, panel apagado, modelo de visión) | `<repositorio>/.env`, **antes** de `./deploy.sh` |
| `jetson.puente.env` | Dirección del Worker y clave de registro | `/etc/api-puente.env` (el instalador lo hace) |

Pásalos por `scp` o USB, nunca por chat ni por git, y **bórralos de la Jetson al terminar**. Si se pierden, se regeneran
desde `.env.local` de este PC.

## Pasos en la Jetson
1. **Permiso de TI** si la Jetson está en la red institucional (túnel saliente por el puerto 443): ver
   `docs/PUBLICAR_EN_FIREBASE.md`, sección 7.
2. Clonar el repositorio y copiar `jetson.api.env` como `.env` (el `deploy.sh` no crea clave nueva si `.env` ya existe).
3. `./deploy.sh` (levanta la API en Docker; `127.0.0.1:8000` queda publicado solo en la propia Jetson).
4. Copiar `jetson.puente.env` a `tools/jetson/` y ejecutar: `sudo ./tools/jetson/instalar_puente_jetson.sh`.
   Instala `cloudflared` (arm64), crea `/etc/api-puente.env` (modo 600) y deja el servicio `api-puente` activo y al arranque.
5. Comprobar: `systemctl status api-puente` y `curl https://api-puente.<tu-subdominio>.workers.dev/health` → `{"status":"ok"}`
   (tarda ~30 s tras arrancar). Un documento de prueba con la clave de la app debe devolver `202` en `/api/v1/jobs`.
6. Borrar de la Jetson los archivos `*.env` copiados (la configuración ya vive en `/etc/api-puente.env` y `.env`).

Operación: `journalctl -u api-puente -f` (registro), `sudo systemctl stop api-puente` (apaga el túnel y quita la dirección
fija), `sudo systemctl restart api-puente` (nueva dirección de túnel y se registra sola).

## Cuidados
- **Un solo origen a la vez.** El Worker guarda una única dirección. Mientras la Jetson sea el origen, **no uses los botones
  de cerrar túnel de este PC** (ni `Detener_tunel_api.bat`): borrarían el registro de la Jetson. Para volver a este PC, para
  el servicio en la Jetson y abre el túnel aquí.
- **IP del cliente.** Detrás del Worker y de Docker, la API ve la IP de Cloudflare o del puente de Docker, no la real: los
  límites por minuto (30 subidas / 600 consultas) y el bloqueo por intentos fallidos se comparten entre todos los clientes.
  Es aceptable para pocos usuarios; `client_ip()` en `app/security.py` no está ajustado para esto.
- **Mismas claves, mismo secreto.** La clave `API_KEYS` de la Jetson debe coincidir con `JETSON_API_KEY` de la app. Si
  rotas una, rota la otra en el mismo momento. `TUNNEL_REGISTER_KEY` debe coincidir con el secreto `REGISTER_KEY` del Worker.
- **Modelo de visión (VLM).** Si la Jetson tiene Ollama, `jetson.api.env` usa `LLM_URL=http://host.docker.internal:11434`
  (el contenedor llega a Ollama del anfitrión) y el mismo `VLM_MODEL` que este PC: hay que hacer `ollama pull <modelo>` allí.
- **Dirección del túnel temporal.** Cambia cada vez que el servicio arranca; no importa, porque la app solo conoce la del
  Worker. Si el Worker responde 503 "Servidor de lectura apagado", el túnel no está registrado: mira el registro del servicio.
- **Cloudflare bloquea el User-Agent por defecto de Python** (403) en `workers.dev`: cualquier cliente propio debe enviar uno.

## Costos
El Worker (plan gratuito: 100.000 peticiones al día), el KV y el túnel temporal son gratis y la cuenta no tiene tarjeta, así
que no hay cobro. Pasarse del límite hace que el Worker devuelva errores hasta el día siguiente. Solo se paga si se actualiza
a Workers Paid. Verifica las condiciones vigentes en la página de precios de Cloudflare.

## Si algún día hay dominio propio
Un túnel con nombre (`cloudflared tunnel create`, `route dns`) da una dirección fija sin Worker y sin registro. Es el camino
de producción (sección 3 de `docs/PUBLICAR_EN_FIREBASE.md`); el Worker es la solución mientras no haya dominio.
