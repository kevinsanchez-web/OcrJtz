// Worker de Cloudflare con direccion fija (*.workers.dev) que reenvia a la API por el tunel temporal.
//  - POST /__tunnel  (cabecera X-Register-Key = secreto REGISTER_KEY, cuerpo JSON {"url": "https://xxx.trycloudflare.com"})
//    guarda el origen vigente; DELETE /__tunnel lo borra. Lo llama tools/tunel_api.ps1 al abrir/cerrar el tunel.
//  - Cualquier otra ruta se reenvia tal cual (metodo, cabeceras, cuerpo) a ese origen. La API sigue exigiendo X-API-Key.
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const reply = (status, body, extra = {}) => new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...extra } });

function sameSecret(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/__tunnel") {
      if (!sameSecret(request.headers.get("x-register-key") || "", env.REGISTER_KEY || "")) return reply(401, { detail: "No autorizado" });
      if (request.method === "DELETE") { await env.TUNNEL.delete("origin"); return reply(200, { ok: true }); }
      if (request.method !== "POST") return reply(405, { detail: "Metodo no permitido" });
      let origin = "";
      try { origin = new URL((await request.json()).url).origin; } catch { return reply(400, { detail: "url invalida" }); }
      if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(origin)) return reply(400, { detail: "solo se aceptan tuneles trycloudflare.com" });
      await env.TUNNEL.put("origin", origin);
      return reply(200, { ok: true, origin });
    }

    const origin = await env.TUNNEL.get("origin");
    if (!origin) return reply(503, { detail: "Servidor de lectura apagado" }, { "retry-after": "30" });

    const headers = new Headers(request.headers);
    headers.delete("host");
    const target = new URL(url.pathname + url.search, origin);
    try {
      return await fetch(new Request(target, { method: request.method, headers, body: request.body, redirect: "manual" }));
    } catch (e) {
      return reply(502, { detail: "No se pudo contactar con el servidor de lectura" });
    }
  },
};
