import json
from typing import Any

import httpx

from app.core.config import OLLAMA_BASE_URL, OLLAMA_MAX_PROMPT_CHARS, OLLAMA_MODEL, OLLAMA_TIMEOUT_SEC


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


def get_ollama_status(client: Any | None = None) -> dict[str, Any]:
    base_url = OLLAMA_BASE_URL.rstrip("/")
    model = OLLAMA_MODEL.strip()
    status = {
        "enabled": bool(model),
        "status": "disabled",
        "base_url": OLLAMA_BASE_URL,
        "model": model,
        "timeout_sec": OLLAMA_TIMEOUT_SEC,
        "model_available": False,
        "available_models": [],
        "error": None,
    }
    if not model:
        return status

    http_client = client or httpx
    try:
        response = http_client.get(f"{base_url}/api/tags", timeout=OLLAMA_TIMEOUT_SEC)
        response.raise_for_status()
        data = response.json()
        models = data.get("models", []) if isinstance(data, dict) else []
        names = [
            item.get("name")
            for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        status["available_models"] = names
        status["model_available"] = model in names
        status["status"] = "available" if status["model_available"] else "model_missing"
    except Exception as exc:
        status["status"] = "unreachable"
        status["error"] = str(exc)
    return status


def generate_with_ollama(instruction: str, content: str, client: Any | None = None) -> dict[str, Any]:
    raw_prompt = f"{instruction.strip()}\n\n{content.strip()}".strip()
    max_chars = max(int(OLLAMA_MAX_PROMPT_CHARS or 1), 1)
    prompt = raw_prompt[:max_chars]
    metadata = {
        "truncated": len(raw_prompt) > len(prompt),
        "original_prompt_chars": len(raw_prompt),
        "sent_prompt_chars": len(prompt),
        "max_prompt_chars": max_chars,
    }
    if not OLLAMA_MODEL:
        return {"enabled": False, "status": "disabled", "text": "", "error": None, **metadata}

    http_client = client or httpx
    try:
        response = http_client.post(
            f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
        return {"enabled": True, "status": "success", "text": data.get("response", ""), "error": None, **metadata}
    except Exception as exc:
        return {"enabled": True, "status": "failed", "text": "", "error": str(exc), **metadata}
