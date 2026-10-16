from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx


class VisionServiceError(RuntimeError):
    pass


def vision_config() -> dict[str, Any]:
    base_url = (
        os.getenv("HERMES_VISION_BASE_URL")
        or os.getenv("HERMES_BASE_URL")
        or os.getenv("HERMES_API_URL")
        or ""
    ).strip().rstrip("/")
    token = os.getenv("HERMES_VISION_TOKEN") or os.getenv("HERMES_TOKEN") or os.getenv("HERMES_API_KEY") or ""
    protocol = (os.getenv("HERMES_VISION_PROTOCOL") or "chat").strip().lower()
    if protocol not in {"chat", "messages"}:
        protocol = "chat"
    return {
        "base_url": base_url,
        "token": token,
        "model": (os.getenv("HERMES_VISION_MODEL") or "deepseek-v4.1flash").strip(),
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


def understand_window(
    instruction: str,
    transcript: str,
    frame_paths: list[str],
    client: Any | None = None,
) -> dict[str, Any]:
    config = vision_config()
    if not config["base_url"] or not config["token"]:
        return {"status": "disabled", "text": "", "error": None, "model": config["model"]}

    selected_frames = frame_paths[: config["max_images"]]
    if not selected_frames:
        return {"status": "skipped", "text": "", "error": "no frames available", "model": config["model"]}

    prompt = (
        f"{instruction.strip()}\n\n"
        "结合连续截图和口播，提取确定的知识，不要写时间轴。"
        "如果看不清或无法确认，明确写 unknown，不要猜测。\n\n"
        f"口播片段：{transcript.strip()[:12000]}"
    ).strip()
    image_urls = [_image_data_url(path) for path in selected_frames]
    http_client = client or httpx
    headers = {"Authorization": f"Bearer {config['token']}", "Content-Type": "application/json"}
    if config["protocol"] == "messages":
        endpoint = f"{config['base_url']}/messages"
        headers["x-api-key"] = config["token"]
        payload = {
            "model": config["model"],
            "max_tokens": 1800,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    *[{"type": "image", "source": {"type": "url", "url": url}} for url in image_urls],
                ],
            }],
        }
    else:
        endpoint = f"{config['base_url']}/chat/completions"
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
        return {"status": "failed", "text": "", "error": str(exc), "model": config["model"]}
