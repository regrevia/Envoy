import json
from typing import Any

import httpx

from app.core.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT_SEC


def parse_ollama_json_object(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    if not content:
        return {}

    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def generate_with_ollama(instruction: str, content: str, client: Any | None = None) -> dict[str, Any]:
    if not OLLAMA_MODEL:
        return {"enabled": False, "status": "disabled", "text": "", "error": None}

    prompt = f"{instruction.strip()}\n\n{content.strip()}".strip()
    http_client = client or httpx
    try:
        response = http_client.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
        return {"enabled": True, "status": "success", "text": data.get("response", ""), "error": None}
    except Exception as exc:
        return {"enabled": True, "status": "failed", "text": "", "error": str(exc)}
