#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess


REQUIRED_TOOLS = {
    "ffmpeg": "video_audio_split",
    "ffprobe": "video_metadata_probe",
}
OPTIONAL_TOOLS = {
    "pdfinfo": "pdf_page_count",
    "pdftoppm": "pdf_page_render",
    "tesseract": "ocr_fallback",
}
VERSION_ARGS = {
    "ffmpeg": ["-version"],
    "ffprobe": ["-version"],
    "pdfinfo": ["-v"],
    "pdftoppm": ["-v"],
    "tesseract": ["--version"],
}
BINARY_ENV = {
    "ffmpeg": "FFMPEG_BIN",
    "ffprobe": "FFPROBE_BIN",
}
PYTHON_MODULES = {
    "whisper": "audio_transcription",
    "paddleocr": "frame_ocr",
    "paddle": "paddleocr_backend",
}


def _resolve_tool(tool: str):
    configured = os.getenv(BINARY_ENV.get(tool, ""))
    candidate = configured or tool
    if os.path.sep in candidate:
        path = candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None
    else:
        path = shutil.which(candidate)
    return candidate, path


def _tool_status(tool: str, purpose: str):
    command, path = _resolve_tool(tool)
    status = {
        "name": tool,
        "purpose": purpose,
        "command": command,
        "available": bool(path),
        "path": path,
        "version": None,
    }
    if not path:
        return status
    try:
        completed = subprocess.run(
            [path, *VERSION_ARGS.get(tool, ["--version"])],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
        first_line = (completed.stdout or "").splitlines()[0] if completed.stdout else ""
        status["version"] = first_line.strip() or None
    except Exception as exc:  # pragma: no cover - version probing is best effort
        status["version"] = f"version_probe_failed: {exc}"
    return status


def _python_module_status(module: str, purpose: str):
    available = importlib.util.find_spec(module) is not None
    return {
        "name": module,
        "purpose": purpose,
        "available": available,
    }


def check_runtime():
    required = [_tool_status(tool, purpose) for tool, purpose in REQUIRED_TOOLS.items()]
    optional = [_tool_status(tool, purpose) for tool, purpose in OPTIONAL_TOOLS.items()]
    python_modules = [_python_module_status(module, purpose) for module, purpose in PYTHON_MODULES.items()]
    python_available = {item["name"]: item["available"] for item in python_modules}
    missing_required = [item["name"] for item in required if not item["available"]]
    missing_optional = [item["name"] for item in optional if not item["available"]]
    has_frame_ocr = bool(python_available.get("paddleocr") and python_available.get("paddle"))
    has_video_text_extractor = bool(python_available.get("whisper") or has_frame_ocr)
    missing_python_required = [] if has_video_text_extractor else ["whisper_or_paddleocr"]
    return {
        "schema_version": "envoy.delivery.runtime.v1",
        "status": "failed" if missing_required or missing_python_required else "ok",
        "required": required,
        "optional": optional,
        "python_modules": python_modules,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "missing_python_required": missing_python_required,
        "capabilities": {
            "frame_ocr": has_frame_ocr,
            "video_text_extraction": has_video_text_extractor,
            "long_video_delivery_e2e": not any(name in missing_required for name in ("ffmpeg", "ffprobe")) and has_video_text_extractor,
            "large_pdf_ocr_fallback": not any(name in missing_optional for name in ("pdfinfo", "pdftoppm", "tesseract")),
        },
    }


def main():
    result = check_runtime()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
