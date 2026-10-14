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
