"""Cliente mínimo de Ollama (sin dependencias extra). Devuelve JSON o None si el modelo no responde."""
import json
import logging
import time
import urllib.request
from typing import Any, Dict, List, Optional

from app import live
from app.config import LLM_MODEL, LLM_TIMEOUT, LLM_URL

logger = logging.getLogger("document-api.llm")


def enabled() -> bool:
    return bool(LLM_URL)


def chat_json(prompt: str, schema: Dict[str, Any], model: Optional[str] = None,
              images: Optional[List[str]] = None, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pide al modelo una respuesta que cumpla `schema` (salida estructurada de Ollama).
    `images`: imágenes en base64 para un modelo de visión. `label`: nombre de la consulta para el seguimiento en vivo.
    Con un documento en seguimiento (panel, KEEP_RECEIVED=1) la respuesta se lee en flujo para mostrarla mientras se genera."""
    streaming = live.active()
    message: Dict[str, Any] = {"role": "user", "content": prompt}
    if images:
        message["images"] = images
    body = {
        "model": model or LLM_MODEL,
        "messages": [message],
        "stream": streaming,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 6144, "num_predict": 600},
        "keep_alive": "30m",  # mantener el modelo cargado entre peticiones
    }
    req = urllib.request.Request(f"{LLM_URL}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    ok = False
    if streaming:
        live.vlm_begin(label or model or LLM_MODEL)
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            if streaming:
                t0, parts = time.monotonic(), []
                for raw in resp:  # una línea JSON por fragmento
                    if time.monotonic() - t0 > LLM_TIMEOUT:  # el tiempo máximo vale para toda la respuesta, no por fragmento
                        raise TimeoutError("tiempo agotado generando la respuesta")
                    raw = raw.strip()
                    if not raw:
                        continue
                    obj = json.loads(raw)
                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        live.vlm_chunk(piece)
                    if obj.get("done"):
                        break
                content = "".join(parts)
            else:
                content = json.loads(resp.read())["message"]["content"]
        out = json.loads(content)
        ok = isinstance(out, dict)
        return out if ok else None
    except Exception as e:  # sin conexión, tiempo agotado, JSON cortado...: se sigue sin el modelo
        logger.warning("El modelo de lenguaje no aportó resultado (%s: %s)", type(e).__name__, e)
        return None
    finally:
        if streaming:
            live.vlm_end(ok)
