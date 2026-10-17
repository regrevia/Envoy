from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import BASE_DIR


class VisionServiceError(ValueError):
    pass


def _config_path() -> Path:
    return Path(os.getenv("ENVOY_VISION_CONFIG_PATH", str(BASE_DIR / "tmp" / "config" / "vision.enc")))


def _key_path() -> Path:
    return Path(os.getenv("ENVOY_VISION_KEY_PATH", str(BASE_DIR / "tmp" / "config" / "vision.key")))


def _fernet() -> Fernet:
    env_key = os.getenv("ENVOY_VISION_ENCRYPTION_KEY", "").strip()
    path = _key_path()
    if env_key:
        key = env_key.encode("ascii")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.write_bytes(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
    try:
        return Fernet(key)
    except Exception as exc:
        raise VisionServiceError("Vision settings encryption key is invalid") from exc


def _validate_vision_settings(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, str]:
    current = existing or {}
    if not current:
        current = {
            "api_key": os.getenv("HERMES_VISION_TOKEN") or os.getenv("HERMES_TOKEN") or os.getenv("HERMES_API_KEY") or "",
            "base_url": os.getenv("HERMES_VISION_BASE_URL") or os.getenv("HERMES_BASE_URL") or os.getenv("HERMES_API_URL") or "",
            "model": os.getenv("HERMES_VISION_MODEL") or "deepseek-v4.1flash",
            "protocol": os.getenv("HERMES_VISION_PROTOCOL") or "chat",
        }
    base_url = str(payload.get("base_url", current.get("base_url", "")) or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VisionServiceError("Base URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise VisionServiceError("Do not put credentials in the Base URL")
    model = str(payload.get("model", current.get("model", "")) or "").strip()
    if not model:
        raise VisionServiceError("Model name is required")
    protocol = str(payload.get("protocol", current.get("protocol", "chat")) or "chat").strip().lower()
    if protocol not in {"chat", "messages"}:
        raise VisionServiceError("Protocol must be chat or messages")
    api_key = str(payload.get("api_key") or current.get("api_key") or "").strip()
    if not api_key:
        raise VisionServiceError("API key is required")
    return {"base_url": base_url, "model": model, "protocol": protocol, "api_key": api_key}


def _read_saved_config() -> dict[str, str]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(_fernet().decrypt(path.read_bytes()).decode("utf-8"))
    except (InvalidToken, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisionServiceError("Saved vision configuration cannot be decrypted") from exc
    return payload if isinstance(payload, dict) else {}


def save_vision_config(payload: dict[str, Any]) -> dict[str, Any]:
    settings = _validate_vision_settings(payload, _read_saved_config())
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet().encrypt(json.dumps(settings).encode("utf-8")))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return _public_config(settings)


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured": bool(config.get("base_url") and config.get("model") and config.get("api_key")),
        "base_url": config.get("base_url") or "",
        "model": config.get("model") or "",
        "protocol": config.get("protocol") or "chat",
        "api_key_configured": bool(config.get("api_key")),
    }


def vision_config() -> dict[str, Any]:
    saved = _read_saved_config()
    env_base_url = (
        os.getenv("HERMES_VISION_BASE_URL")
        or os.getenv("HERMES_BASE_URL")
        or os.getenv("HERMES_API_URL")
        or ""
    ).strip().rstrip("/")
    env_token = os.getenv("HERMES_VISION_TOKEN") or os.getenv("HERMES_TOKEN") or os.getenv("HERMES_API_KEY") or ""
    env_model = os.getenv("HERMES_VISION_MODEL") or ""
    env_protocol = os.getenv("HERMES_VISION_PROTOCOL") or ""
    has_environment_override = bool(env_base_url or env_token or env_model or env_protocol)
    if saved and not has_environment_override:
        base_url = saved.get("base_url", "")
        token = saved.get("api_key", "")
        model = saved.get("model", "")
        protocol = saved.get("protocol", "chat")
    else:
        base_url = env_base_url or saved.get("base_url", "")
        token = env_token or saved.get("api_key", "")
        model = env_model or saved.get("model", "")
        protocol = env_protocol or saved.get("protocol", "chat")
    protocol = protocol.strip().lower()
    if protocol not in {"chat", "messages"}:
        protocol = "chat"
    return {
        "base_url": base_url,
        "token": token,
        "model": (model or "deepseek-v4.1flash").strip(),
        "protocol": protocol,
        "timeout_sec": max(int(os.getenv("HERMES_VISION_TIMEOUT_SEC", "180") or 180), 30),
        "max_images": max(int(os.getenv("HERMES_VISION_MAX_IMAGES", "6") or 6), 1),
    }


def vision_status() -> dict[str, Any]:
    config = vision_config()
    return {
        "enabled": bool(config["base_url"] and config["token"]),
        "configured": bool(config["base_url"] and config["token"]),
        "model": config["model"],
        "protocol": config["protocol"],
        "base_url": config["base_url"],
        "api_key_configured": bool(config["token"]),
    }


def _image_data_url(path: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise VisionServiceError(f"Vision frame not found: {path}")
    mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_text(data: dict[str, Any], protocol: str) -> str:
    if protocol == "messages":
        content = data.get("content")
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "").strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        return str(content or "").strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
    return ""


def _endpoint(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    clean = suffix.lstrip("/")
    if base.lower().endswith(clean.lower()):
        return base
    if clean == "messages":
        if base.lower().endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"
    if clean == "chat/completions" and not base.lower().endswith("/v1"):
        return f"{base}/{clean}"
    return f"{base}/{clean}"


def _headers(config: dict[str, Any]) -> dict[str, str]:
    token = config["token"]
    headers = {"Content-Type": "application/json"}
    if config["protocol"] == "messages":
        headers.update({"x-api-key": token, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _response_text(response: Any, protocol: str) -> str:
    return _extract_text(response.json(), protocol)


def test_vision_connection(client: Any | None = None) -> dict[str, Any]:
    config = vision_config()
    if not config.get("base_url") or not config.get("token") or not config.get("model"):
        return {"status": "not_configured", "model": config.get("model", ""), "error": "Vision API settings are incomplete"}
    if config["protocol"] == "messages":
        endpoint = _endpoint(config["base_url"], "messages")
        payload = {
            "model": config["model"],
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with OK."}],
        }
    else:
        endpoint = _endpoint(config["base_url"], "chat/completions")
        payload = {
            "model": config["model"],
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with OK."}],
        }
    try:
        response = (client or httpx).post(
            endpoint,
            headers=_headers(config),
            json=payload,
            timeout=config["timeout_sec"],
        )
        response.raise_for_status()
        text = _response_text(response, config["protocol"])
        if not text:
            raise VisionServiceError("Provider returned an empty response")
        return {"status": "success", "model": config["model"], "protocol": config["protocol"], "response": text[:200]}
    except Exception as exc:
        error = str(exc).replace(config["token"], "[redacted]")[:1200]
        return {"status": "failed", "model": config["model"], "protocol": config["protocol"], "error": error}


def _json_object_from_text(text: str) -> dict[str, Any]:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for offset, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _complete_text(prompt: str, client: Any | None = None) -> str:
    config = vision_config()
    if not config["base_url"] or not config["token"]:
        raise VisionServiceError("Vision API is not configured")
    messages = [{"role": "user", "content": prompt}]
    if config["protocol"] == "messages":
        payload = {"model": config["model"], "max_tokens": 3000, "messages": messages}
        endpoint = _endpoint(config["base_url"], "messages")
    else:
        payload = {"model": config["model"], "temperature": 0.1, "max_tokens": 3000, "messages": messages}
        endpoint = _endpoint(config["base_url"], "chat/completions")
    response = (client or httpx).post(
        endpoint,
        headers=_headers(config),
        json=payload,
        timeout=config["timeout_sec"],
    )
    response.raise_for_status()
    text = _response_text(response, config["protocol"])
    if not text:
        raise VisionServiceError("Vision provider returned an empty response")
    return text


def synthesize_video_topics(sources: list[dict[str, Any]], client: Any | None = None) -> dict[str, Any]:
    """Create a theme-first navigation layer; complete evidence remains in JSONL."""
    config = vision_config()
    if not config["base_url"] or not config["token"]:
        return {
            "status": "skipped",
            "model": config["model"],
            "topics": [],
            "error": "Vision API is not configured; all source evidence remains available in the evidence index",
        }
    if not sources:
        return {"status": "skipped", "model": config["model"], "topics": [], "error": "No source videos"}

    allowed_evidence_ids = {
        str(evidence["id"])
        for source in sources
        for evidence in source.get("evidence", [])
        if evidence.get("id")
    }
    source_groups = [sources[i : i + 4] for i in range(0, len(sources), 4)]
    partial_topics: list[dict[str, Any]] = []
    try:
        for group in source_groups:
            source_blocks = []
            for source in group:
                evidence_lines = []
                remaining = 6000
                for evidence in source.get("evidence", []):
                    if remaining <= 0:
                        break
                    content = str(evidence.get("content") or "").strip()
                    snippet = content[: min(1200, remaining)]
                    if snippet:
                        evidence_lines.append(
                            f"- evidence_id={evidence['id']} ({evidence.get('kind', 'knowledge')}): {snippet}"
                        )
                        remaining -= len(snippet)
                source_blocks.append(
                    f"VIDEO_ID={source.get('video_id')}\nTITLE={source.get('title')}\n"
                    + "\n".join(evidence_lines)
                )
            prompt = (
                "为知识库制作主题索引，不要按视频分别写文档。把不同视频中讲同一对象/机制/流程的内容归入同主题。"
                "仅总结下面提供的证据，不推测、不补充常识。返回严格 JSON："
                "{\"topics\":[{\"title\":\"主题名\",\"summary\":\"不超过三句的主题摘要\","
                "\"points\":[{\"text\":\"具体知识点\",\"evidence_ids\":[\"输入中原样的 evidence_id\"]}]}]}。"
                "每个 point 必须带至少一个有效 evidence_id，无法归属则不要输出。\n\n"
                + "\n\n".join(source_blocks)
            )
            parsed = _json_object_from_text(_complete_text(prompt, client=client))
            partial_topics.extend(
                topic for topic in parsed.get("topics", []) if isinstance(topic, dict)
            )

        if len(partial_topics) > 8:
            prompt = (
                "合并重复/高度相关主题，仍返回严格 JSON schema。保留所有来源 evidence_ids，"
                "不得增加事实或证据编号。\n\n"
                + json.dumps({"topics": partial_topics}, ensure_ascii=False)
            )
            parsed = _json_object_from_text(_complete_text(prompt, client=client))
            raw_topics = parsed.get("topics", [])
        else:
            raw_topics = partial_topics

        topics = []
        for topic in raw_topics:
            if not isinstance(topic, dict):
                continue
            title = str(topic.get("title") or "").strip()
            points = []
            for point in topic.get("points", []):
                if not isinstance(point, dict):
                    continue
                refs = [str(ref) for ref in point.get("evidence_ids", []) if str(ref) in allowed_evidence_ids]
                text = str(point.get("text") or "").strip()
                if text and refs:
                    points.append({"text": text, "evidence_ids": refs})
            if title and points:
                topics.append({
                    "title": title,
                    "summary": str(topic.get("summary") or "").strip(),
                    "points": points,
                })
        if not topics:
            raise VisionServiceError("Provider returned no topics with valid evidence references")
        return {"status": "success", "model": config["model"], "topics": topics, "error": None}
    except Exception as exc:
        error = str(exc).replace(config["token"], "[redacted]")[:1500]
        return {"status": "failed", "model": config["model"], "topics": [], "error": error}


def understand_window(
    instruction: str,
    transcript: str,
    frame_paths: list[str],
    client: Any | None = None,
    context: str = "",
    window_label: str = "",
) -> dict[str, Any]:
    config = vision_config()
    if not config["base_url"] or not config["token"]:
        return {"status": "disabled", "text": "", "error": None, "model": config["model"]}

    selected_frames = frame_paths[: config["max_images"]]
    if not selected_frames:
        return {"status": "skipped", "text": "", "error": "no frames available", "model": config["model"]}

    prompt = (
        f"{instruction.strip()}\n\n"
        "结合连续截图和与其时间相邻的口播，提取确定的知识，不要输出时间轴。"
        "如果看不清或无法确认，明确写 unknown，不要猜测。"
        "如果出现总结表、清单、地图或菜单，尽可能保留其中所有可读条目和关系。\n\n"
        f"窗口范围：{window_label or '未标注'}\n"
        f"前一窗口状态（用于连续理解）：{context.strip()[:2500] or '无'}\n\n"
        f"口播片段：{transcript.strip()[:12000]}"
    ).strip()
    image_urls = [_image_data_url(path) for path in selected_frames]
    http_client = client or httpx
    headers = _headers(config)
    if config["protocol"] == "messages":
        endpoint = _endpoint(config["base_url"], "messages")
        payload = {
            "model": config["model"],
            "max_tokens": 1800,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *[
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": url.split(";", 1)[0].split(":", 1)[-1],
                                "data": url.split(",", 1)[-1],
                            },
                        }
                        for url in image_urls
                    ],
                ],
            }],
        }
    else:
        endpoint = _endpoint(config["base_url"], "chat/completions")
        payload = {
            "model": config["model"],
            "temperature": 0.1,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                ],
            }],
        }

    try:
        response = http_client.post(endpoint, headers=headers, json=payload, timeout=config["timeout_sec"])
        response.raise_for_status()
        data = response.json()
        text = _extract_text(data, config["protocol"])
        if not text:
            return {"status": "failed", "text": "", "error": "vision provider returned empty response", "model": config["model"]}
        return {
            "status": "success",
            "text": text,
            "error": None,
            "model": config["model"],
            "frame_count": len(selected_frames),
        }
    except Exception as exc:
        error = str(exc).replace(config["token"], "[redacted]")
        return {"status": "failed", "text": "", "error": error, "model": config["model"]}
