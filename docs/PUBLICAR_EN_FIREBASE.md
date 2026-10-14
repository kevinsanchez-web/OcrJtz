# Publicar la app "extracción-de-documentos-de-tránsito" en Firebase conectada a la API de la Jetson

Plan por escrito (camino A: túnel + función de Firebase). Nada de esto se ha ejecutado todavía.
No es asesoría legal: los puntos de la sección 6 conviene validarlos con quien lleve la protección de datos.

## 1. Cómo queda armado

```
Navegador (cualquier lugar)
   │  HTTPS
   ▼
Firebase Hosting  ── la app web (React)
   │  /api/**  (reescritura)
   ▼
Función de Firebase "tramitesApi"  ── guarda la clave de la API como SECRETO (el navegador nunca la ve)
   │  HTTPS + encabezados de acceso
   ▼
Túnel de Cloudflare (conexión SALIENTE desde la Jetson; no se abre ningún puerto)
   │
   ▼
Jetson Thor: API de lectura OCR en 127.0.0.1:8000
```

Por qué así:
- El navegador no puede guardar la clave de la API: si estuviera en el código de la web, cualquiera la copiaría.
- Firebase Hosting corta las peticiones a los 60 s y un PDF puede tardar minutos: la función usa los **trabajos
  asíncronos** de la API (`POST /api/v1/jobs` devuelve un `jobId` al instante y se consulta con `GET /api/v1/jobs/{id}`),
  así ninguna llamada dura más de unos segundos.
- Cloudflare corta a los ~100 s sin respuesta; con trabajos asíncronos tampoco importa.

## 2. Antes de empezar (obligatorio)

1. **Permiso de TI** para el túnel saliente desde la Jetson (texto en la sección 7).
2. **Rotar la clave de la API** que se pegó en el chat al inicio del proyecto (ya no es secreta) y no reutilizarla.
3. **Autorización de los titulares** y política de tratamiento de datos (sección 6).
4. Plan **Blaze** de Firebase (pago por uso) para las funciones, y un **dominio** propio (~10-15 USD/año) para tener
   una dirección fija del túnel (ej. `api.tudominio.com`).

## 3. Jetson

1. Descargar el repositorio y arrancar la API (README, "Despliegue en la Jetson"). En `.env.local`:
   ```
   API_KEYS=firebase:UNA_CLAVE_NUEVA_DE_32_O_MAS_CARACTERES
   ALLOWED_HOSTS=api.tudominio.com
   ENABLE_PANEL=0                 # el panel web NO debe quedar público
   RATE_LIMIT_PER_MIN=30          # al publicar, vuelve a poner límites (hoy están en 0 = sin límite)
   RATE_LIMIT_READS_PER_MIN=600
   ```
   Genera la clave con `python -c "import secrets;print(secrets.token_urlsafe(32))"`. La clave se llama `firebase`
   (nombre de la app), así el registro y las estadísticas distinguen sus peticiones de las demás.
2. Instalar `cloudflared` (un solo binario de Cloudflare) y crear un túnel con nombre:
   ```
   cloudflared tunnel login
   cloudflared tunnel create jetson-ocr
   cloudflared tunnel route dns jetson-ocr api.tudominio.com
   # config.yml: ingress: - hostname: api.tudominio.com  service: http://127.0.0.1:8000
   sudo cloudflared service install      # queda como servicio: arranca solo
   ```
3. En Cloudflare Zero Trust crear una **política de Access con "service token"** para `api.tudominio.com`: solo
   quien presente el token (la función de Firebase) llega a la API. Aunque alguien descubra la dirección, no pasa.

## 4. Firebase

1. Secretos de la función (`firebase functions:secrets:set ...`): `JETSON_API_URL`, `JETSON_API_KEY`,
   `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`.
2. Función (generación 2, región `southamerica-east1` para acercarla a Colombia y subir el límite de tamaño a 32 MB):
   - `POST /api/documents/analyze-jetson`: recibe el archivo, crea el trabajo en la Jetson (`mode=transit&lines=true`)
     y devuelve el `jobId` en menos de 2 s.
   - `GET /api/documents/analyze-jetson/{jobId}`: consulta el estado a la Jetson y devuelve el progreso o el resultado.
3. Interfaz (`App.tsx`): después de enviar el archivo, consultar cada 1-2 s hasta que termine y con el resultado armar
   los registros igual que hoy hace `server.ts`.
4. Como la app queda abierta a cualquiera con el enlace (sin login), protégela con:
   - **Firebase App Check** (reCAPTCHA): solo tu web puede llamar a la función, no scripts ajenos.
   - **Cuota por IP y tope diario** en la función (ej. 20 archivos por hora y 500 por día) y tamaño máximo de archivo.
   - **Casilla de consentimiento** antes de subir ("autorizo el tratamiento de mis datos para este trámite") y
     aviso de privacidad visible.
   - **Interruptor de emergencia**: apagar el servicio `cloudflared` o quitar la clave `firebase` (panel de escritorio,
     pestaña "Apps y claves") corta el acceso al instante.
   Recomendación: añadir inicio de sesión (Firebase Auth) más adelante; con acceso abierto cualquiera podría llenar
   la cola con archivos y hacerle gastar recursos a la Jetson.
5. Publicar: `npm run build` y `firebase deploy` (Hosting + Functions).

## 5. Cambios de código que haría yo cuando des luz verde

- API: leer la IP real del cliente (`CF-Connecting-IP`) cuando la petición llega del túnel, porque hoy todas
  parecerían venir de 127.0.0.1 y el bloqueo por clave incorrecta sería global.
- API: perfil de despliegue público (límites activados, panel apagado) y documentarlo.
- Función `tramitesApi` y ajuste de `App.tsx` descritos arriba, con App Check y cuotas.
- Pruebas de punta a punta con el PDF real y con la app desplegada (sin tocar la configuración local actual).

## 6. Lista de verificación legal (Colombia, Ley 1581 de 2012 y Decreto 1377 de 2013)

- [ ] **Autorización previa, expresa e informada** del titular, para una finalidad concreta (el trámite).
- [ ] **Política de tratamiento de datos** publicada y aviso de privacidad en la app.
- [ ] **Transferencia internacional:** Firebase/Google y Cloudflare pueden procesar datos fuera de Colombia. Verifica
      con la SIC (Circular Externa 005 de 2017, países con nivel adecuado) o incluye la transferencia en la autorización.
- [ ] **Seguridad:** HTTPS (ya lo da Firebase/Cloudflare), acceso restringido, claves rotadas, registros sin
      contenido (la API ya guarda solo metadatos y los resultados duran 30 minutos en memoria).
- [ ] **Retención mínima:** no conservar los documentos más tiempo del necesario; esta arquitectura no los guarda.
- [ ] **Responsable y encargado:** definir quién es el responsable del tratamiento y firmar los contratos de encargo
      que correspondan (Google y Cloudflare actúan como encargados).
- [ ] **Registro Nacional de Bases de Datos (RNBD):** consulta si tu organización está obligada.
- [ ] **Red de la Jetson:** autorización escrita de TI (sección 7).
- [ ] Atender las solicitudes de los titulares (consulta, corrección, supresión).

## 7. Texto para TI (solicitud)

> Asunto: Autorización de túnel saliente HTTPS para un equipo Jetson
>
> Necesitamos publicar una aplicación web (Firebase) que envía documentos a un equipo Jetson Thor de nuestra red
> para su lectura automática. Para ello el equipo ejecutará el servicio `cloudflared` (Cloudflare Tunnel), que abre
> **solo conexiones salientes** por el puerto 443 hacia Cloudflare (`*.cloudflare.com`, `*.argotunnel.com`). **No se
> abre ningún puerto de entrada** ni se redirige tráfico en el firewall. El acceso al servicio está protegido con un
> token de servicio (Cloudflare Access) y una clave de API, y el equipo no almacena el contenido de los documentos.
> Solicitamos autorización para: (1) permitir las conexiones salientes mencionadas desde la IP de la Jetson y
> (2) instalar y mantener el servicio `cloudflared`. Responsable: <nombre y contacto>. Equipo: <nombre, IP>.

## 8. Costos aproximados

- Firebase Hosting: dentro de la capa gratuita para este uso. Functions: Blaze (pago por uso, normalmente centavos
  con este volumen). Cloudflare Tunnel y Access (hasta 50 usuarios): gratis. Dominio: ~10-15 USD al año.

## 9. Revertir

Detener `cloudflared` en la Jetson (o revocar la clave `firebase` en el panel) deja la API inaccesible desde internet
al instante; la app seguirá abierta pero sin poder analizar. `firebase hosting:disable` la retira por completo.

## 10. Ajustes necesarios en la app "extracción-de-documentos-de-tránsito"

Revisados en `functions/src/index.ts`, `src/App.tsx`, `server.ts`, `firebase.json` y `firestore.rules`.

**Privacidad (obligatorios antes de abrirla a cualquiera)**
1. `GET /api/documents` devuelve **todos** los documentos de Firestore y `GET /api/external-system-db` también: con la
   app pública, cualquier visitante vería las cédulas y datos de todos. Además `DELETE /api/external-system-db/clear`
   y `DELETE/PATCH /api/documents/:id` no verifican de quién es el documento. Solución: Firebase Auth **anónimo**
   (sin pantalla de login: cada navegador recibe su propio `uid`), la función valida el token y cada registro lleva su
   `ownerId`; las consultas y borrados se limitan a ese dueño.
2. Retención: Firestore guarda `extractedData` (datos personales) sin vencimiento. Activar una política **TTL**
   (por ejemplo 24 horas) sobre `scannedAt`/`expireAt`, y un botón de "borrar mis documentos".
3. CORS está en `*` (cualquier sitio puede llamar a la función): quitarlo; con la reescritura de Hosting es mismo origen.
4. **App Check** (reCAPTCHA) y cuotas por IP/uid en la función. Retirar o proteger `/api/external-system-mock` y `/api/validate`.

**Funcionalidad**
5. `functions/src/index.ts` **no tiene** la ruta `analyze-jetson` (solo existe en `server.ts`, el servidor local). Agregarla
   como dos rutas: crear el trabajo (`POST`, responde en segundos con el `jobId`) y consultarlo (`GET`), que reenvían a
   la API de la Jetson con la clave y las credenciales de Cloudflare Access guardadas como secretos (`defineSecret`).
6. `firebase-functions` v6 con `functions.https.onRequest` es la **generación 1**: límite de 10 MB por petición y
   60 s. Pasar a `onRequest` de `firebase-functions/v2/https` con `region: "southamerica-east1"`, los secretos y
   `memory/timeoutSeconds`; el límite sube a 32 MB. Como la interfaz envía el archivo en base64 (+33 %), limitar el
   archivo a ~15 MB en la interfaz.
7. `src/App.tsx`, `handleAnalyzeDocument`: hoy hace un solo `fetch` y espera. Con la Jetson debe crear el trabajo y
   consultar cada 1-2 s hasta que termine, mostrando el avance real ("página 2 de 6") en lugar de los mensajes
   simulados por temporizador, y armar los registros igual que `server.ts` (formato `documents[]` de la API).
8. `firebase.json`: añadir `"region": "southamerica-east1"` a la reescritura `/api/**` para que apunte a la función nueva.
9. `.env` (`JETSON_API_URL`, `JETSON_API_KEY`) solo sirve para el servidor local. En Firebase esos valores van como
   secretos de la función, nunca en el código de la web.
10. Mensajes de error claros cuando la Jetson no responde (502), está ocupada (503) o la clave no coincide (401), y
    aviso de privacidad con casilla de consentimiento antes de subir.
11. Compartir el mapeo "resultado de la API → registros" entre `server.ts` y `functions/src/` (hoy están duplicados
    los módulos) para que ambos entornos se comporten igual.

### Estado de los ajustes de la sección 10

- Hechos (funcionamiento): 5 a 11. Archivos nuevos/cambiados en la app: `functions/src/jetson.ts` (lógica compartida),
  `functions/src/index.ts` (rutas `analyze-jetson` + función 2.ª gen. `tramitesApiV2`), `server.ts`, `src/lib/jetsonClient.ts`,
  `src/App.tsx`, `src/components/DocumentDropzone.tsx` (tope de 15 MB), `firebase.json` y `.env.example`.
- Pendientes: 1 a 4 (privacidad). **No publicar la app abierta a cualquiera hasta resolverlos.**
- Para desplegar: `firebase functions:secrets:set JETSON_API_KEY` (y `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`, con
  `-` si no se usa Access), el parámetro `JETSON_API_URL` (se pide al desplegar o va en `functions/.env`) y `firebase deploy`.
  La función anterior `tramitesApi` se conserva; bórrala cuando `tramitesApiV2` funcione.

## 11. Probar TODO antes de desplegar (emuladores de Firebase, sin tocar la nube)

Los emuladores ejecutan la función real (`tramitesApiV2`), Hosting con su reescritura `/api/**` y Firestore en tu PC:
nada se escribe en el proyecto de Firebase.

1. API de la Jetson (o la de este PC) encendida en `http://127.0.0.1:8000`.
2. En la app, crea (están ignorados por Git):
   - `functions/.env.local` con `JETSON_API_URL=http://127.0.0.1:8000`
   - `functions/.secret.local` con `JETSON_API_KEY=<la clave de la app>`, `CF_ACCESS_CLIENT_ID=-` y `CF_ACCESS_CLIENT_SECRET=-`
     (`.env.local` y `.secret.local` solo los lee el emulador; el despliegue usa `functions/.env` y los secretos de Firebase).
3. `npm run emulators` (compila la web y la función, y arranca todo). Abre `http://127.0.0.1:5000`, elige el motor
   "Local (OCR)" y sube un documento: pasa por Hosting, la función, la API y Firestore, igual que en producción.
4. Para ver qué hace la función: los registros salen en la ventana de los emuladores.

Para probar contra una Jetson remota por el túnel, cambia `JETSON_API_URL` en `functions/.env.local` por
`https://api.tudominio.com` (y las credenciales de Access en `.secret.local`).

## 12. Qué hacer en la Jetson (paso a paso)

Requisitos: JetPack con Docker y `docker compose`, acceso a internet para el primer arranque (baja la imagen base,
los paquetes y el modelo latino de ~8 MB) y, para publicar, el permiso de TI y el dominio (secciones 2 y 3).

1. **Descargar la API**
   ```bash
   git clone https://github.com/kevinsanchez-web/OcrJtz.git && cd OcrJtz
   ```
2. **Primer arranque**: `./deploy.sh`. Pregunta el dominio: **déjalo vacío** (el túnel no lo necesita). Crea `.env` con una
   clave generada, construye la imagen y arranca. Al terminar muestra la clave y la dirección local.
3. **Ajustar `.env`** (`nano .env`) y aplicar con `docker compose up -d api`:
   ```
   # clave principal (app de tránsito en local) + una clave NUEVA con nombre para la función de Firebase
   API_KEYS=<la_que_generó_deploy>,firebase:<otra_clave_de_32_o_mas_caracteres>
   ALLOWED_HOSTS=api.tudominio.com,127.0.0.1,localhost
   ENABLE_PANEL=0
   RATE_LIMIT_PER_MIN=30
   RATE_LIMIT_READS_PER_MIN=600
   ```
   Genera la clave nueva con `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`.
4. **Probar en la propia Jetson**
   ```bash
   curl http://127.0.0.1:8000/health
   curl -X POST http://127.0.0.1:8000/api/v1/ocr -H "X-API-Key: <clave>" -F "file=@documento.pdf"
   docker compose logs -f api        # ver las peticiones en vivo
   ```
5. **Modelo de visión (opcional, recomendado en la Jetson por la GPU)**: instalar Ollama en la Jetson,
   `ollama pull qwen2.5vl:7b`, y en `.env`:
   ```
   LLM_URL=http://host.docker.internal:11434
   VLM_MODEL=qwen2.5vl:7b
   LLM_TIMEOUT=300
   ```
   (`docker-compose.yml` ya permite que el contenedor llegue a Ollama.)
6. **Túnel (solo si publicas)**: instalar `cloudflared` (paquete arm64) como servicio y apuntarlo a
   `http://127.0.0.1:8000` (el puerto ya está publicado solo en loopback). **No hace falta Caddy**: arranca solo la API con
   `docker compose up -d --build api` y deja cerrados los puertos 80 y 443 del firewall. Protege el túnel con Cloudflare Access
   (sección 3).
7. **Probar desde fuera** (emuladores de la sección 11 con `JETSON_API_URL=https://api.tudominio.com`) y después desplegar
   la app en Firebase (sección 4).
8. **Actualizar**: `git pull && docker compose up -d --build api`. **Apagar**: `docker compose stop api` (y `sudo systemctl stop
   cloudflared` para cortar el acceso desde internet).

Pendiente de validar: la imagen Docker nunca se ha construido en ARM64. En el primer `./deploy.sh` revisa que `pip install`
termine sin errores (los paquetes usados tienen versiones para aarch64, pero no está comprobado en tu Jetson). El OCR corre en
CPU; el VLM por Ollama sí usa la GPU.

## 13. Puente temporal solo hacia la API (sin exponer la web)

La app de tránsito **no** se publica por el túnel: solo la API. La app (donde corra: otro equipo, otro lugar o la función de Firebase)
se conecta con dos campos de su `.env`:

```
JETSON_API_URL="https://xxxx.trycloudflare.com/api/v1/analyze-document"   # la dirección que muestra el túnel
JETSON_API_KEY="<clave de la app>"                                         # la de API_KEYS (.env.local) que no lleva nombre
```

1. `Iniciar_tunel_api.bat` (deja la ventana abierta): activa los límites por minuto (30 subidas, 600 consultas), reinicia la API, abre un túnel
   temporal de Cloudflare solo hacia el puerto 8000, muestra la dirección y la copia al portapapeles.
2. Pon esa dirección en `JETSON_API_URL` de la app y reinicia la app (lee el `.env` al arrancar). La API sigue exigiendo la clave.
3. `Detener_tunel_api.bat` cierra el túnel y devuelve la API a «sin límites por minuto».

Notas:
- La dirección cambia cada vez que se abre el túnel; si la app está en Firebase habría que redesplegar la función con la nueva
  (para una dirección fija hace falta un túnel con nombre y dominio, sección 3).
- Si la app corre en el mismo PC que la API, déjala con `http://127.0.0.1:8000`: este PC (red institucional) no resuelve `trycloudflare.com`.
- **Rota la clave si se ha compartido**: una clave pegada en un chat o un repositorio ya no es secreta. La clave de tránsito se rotó el
  2026-10-14 (la anterior dejó de funcionar); la nueva está en `.env.local` y en el `.env` de la app, y no debe pegarse en ningún sitio.
- La IP real del visitante se toma de `CF-Connecting-IP` solo si la conexión llega desde el propio equipo (así el bloqueo por clave
  incorrecta no afecta a todos).

## 14. Acceso por usuarios: DESCARTADO

Se probó una versión con inicio de sesión de Google, aprobación de usuarios por un administrador, cuotas y panel de administración,
y se decidió **no hacerla**. La rama que la contenía se eliminó y `main` no tiene nada de eso.

**Consecuencia: la app NO debe publicarse abierta a cualquiera.** Sin control de acceso, la función de Firebase devuelve todos los
documentos a cualquier visitante (`GET /api/documents`, la base externa de prueba, borrados y ediciones sin dueño), tiene CORS
abierto y permite usar el OCR sin límite. Mientras eso sea así, úsala solo en local (`npm run dev` o `npm run emulators`) o
con la prueba temporal de la sección 13. Para publicarla habría que resolver el acceso de otra forma (por ejemplo, datos separados por
navegador con Firebase Auth anónimo, quitar CORS y poner cuotas).

Estado de los puntos de privacidad de la sección 10: 1 a 4 **pendientes** en `main`.
