"""Cliente mínimo de Ollama (sin dependencias extra). Devuelve JSON o None si el modelo no responde."""
import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from app.config import LLM_MODEL, LLM_TIMEOUT, LLM_URL

logger = logging.getLogger("document-api.llm")


def enabled() -> bool:
    return bool(LLM_URL)


def chat_json(prompt: str, schema: Dict[str, Any], model: Optional[str] = None,
              images: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Pide al modelo una respuesta que cumpla `schema` (salida estructurada de Ollama).
    `images`: imágenes en base64 para un modelo de visión."""
    message: Dict[str, Any] = {"role": "user", "content": prompt}
    if images:
        message["images"] = images
    body = {
        "model": model or LLM_MODEL,
        "messages": [message],
        "stream": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 6144, "num_predict": 600},
        "keep_alive": "30m",  # mantener el modelo cargado entre peticiones
    }
    req = urllib.request.Request(f"{LLM_URL}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            content = json.loads(resp.read())["message"]["content"]
        out = json.loads(content)
        return out if isinstance(out, dict) else None
    except Exception as e:  # sin conexión, tiempo agotado, JSON cortado...: se sigue sin el modelo
        logger.warning("El modelo de lenguaje no aportó resultado (%s: %s)", type(e).__name__, e)
        return None
