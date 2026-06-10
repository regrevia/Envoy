import json
from typing import Any

import httpx

from app.core.config import OLLAMA_BASE_URL, OLLAMA_MAX_PROMPT_CHARS, OLLAMA_MODEL, OLLAMA_TIMEOUT_SEC

_RUNTIME_CONFIG = {
    "base_url": OLLAMA_BASE_URL.rstrip("/") or "http://127.0.0.1:11434",
    "model": OLLAMA_MODEL.strip(),
    "timeout_sec": max(int(OLLAMA_TIMEOUT_SEC or 1), 1),
    "max_prompt_chars": max(int(OLLAMA_MAX_PROMPT_CHARS or 1), 1),
    "configured_from": "environment",
}


def get_ollama_config() -> dict[str, Any]:
    return dict(_RUNTIME_CONFIG)


def update_ollama_config(payload: dict[str, Any]) -> dict[str, Any]:
    base_url = str(payload.get("base_url") or _RUNTIME_CONFIG["base_url"]).strip().rstrip("/")
    model = str(payload.get("model") if "model" in payload else _RUNTIME_CONFIG["model"]).strip()
    try:
        timeout_sec = int(payload.get("timeout_sec", _RUNTIME_CONFIG["timeout_sec"]))
    except (TypeError, ValueError):
        timeout_sec = _RUNTIME_CONFIG["timeout_sec"]
    try:
        max_prompt_chars = int(payload.get("max_prompt_chars", _RUNTIME_CONFIG["max_prompt_chars"]))
    except (TypeError, ValueError):
        max_prompt_chars = _RUNTIME_CONFIG["max_prompt_chars"]

    _RUNTIME_CONFIG.update({
        "base_url": base_url or "http://127.0.0.1:11434",
        "model": model,
        "timeout_sec": timeout_sec if timeout_sec > 0 else _RUNTIME_CONFIG["timeout_sec"],
        "max_prompt_chars": max_prompt_chars if max_prompt_chars > 0 else _RUNTIME_CONFIG["max_prompt_chars"],
        "configured_from": "runtime",
    })
    return get_ollama_config()


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


def get_ollama_status(client: Any | None = None, probe: bool = True) -> dict[str, Any]:
    config = get_ollama_config()
    base_url = config["base_url"]
    model = config["model"]
    status = {
        "enabled": bool(model),
        "status": "disabled",
        "base_url": base_url,
        "model": model,
        "timeout_sec": config["timeout_sec"],
        "model_available": False,
        "available_models": [],
        "error": None,
    }
    if not model:
        return status
    if not probe:
        status["status"] = "configured"
        return status

    http_client = client or httpx
    try:
        response = http_client.get(f"{base_url}/api/tags", timeout=config["timeout_sec"])
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
    config = get_ollama_config()
    raw_prompt = f"{instruction.strip()}\n\n{content.strip()}".strip()
    max_chars = max(int(config["max_prompt_chars"] or 1), 1)
    prompt = raw_prompt[:max_chars]
    metadata = {
        "truncated": len(raw_prompt) > len(prompt),
        "original_prompt_chars": len(raw_prompt),
        "sent_prompt_chars": len(prompt),
        "max_prompt_chars": max_chars,
    }
    if not config["model"]:
        return {"enabled": False, "status": "disabled", "text": "", "error": None, **metadata}

    http_client = client or httpx
    try:
        response = http_client.post(
            f"{config['base_url']}/api/generate",
            json={"model": config["model"], "prompt": prompt, "stream": False},
            timeout=config["timeout_sec"],
        )
        response.raise_for_status()
        data = response.json()
        text = str(data.get("response", "")).strip()
        if not text:
            return {"enabled": True, "status": "failed", "text": "", "error": "ollama returned empty response", **metadata}
        return {"enabled": True, "status": "success", "text": text, "error": None, **metadata}
    except Exception as exc:
        return {"enabled": True, "status": "failed", "text": "", "error": str(exc), **metadata}
