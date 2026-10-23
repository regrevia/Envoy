from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from typing import Any


OCR_PROFILES = {"fast", "balanced", "quality", "pro"}
OCR_PIPELINE_REVISION = "tesseract-tsv-page-dimensions-v2"
PADDLE_LANGUAGE_ALIASES = {
    "chi_sim": "ch",
    "chi_tra": "chinese_cht",
}


def ocr_cache_key(
    image_sha256: str,
    *,
    profile: str,
    language: str = "eng",
    backend: str = "auto",
    engine_version: str = "unknown",
) -> str:
    if profile not in OCR_PROFILES:
        raise ValueError(f"Unsupported OCR profile: {profile}")
    payload = "\0".join((
        image_sha256.lower(),
        profile,
        language,
        backend,
        engine_version,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_ocr_backend(backend: str = "auto") -> str:
    available = available_ocr_backends()
    selected = backend
    if selected == "auto":
        selected = "paddleocr" if available["paddleocr"] else "tesseract" if available["tesseract"] else ""
    if selected not in {"paddleocr", "tesseract"} or not available.get(selected, False):
        raise RuntimeError("No selected OCR backend is installed; install PaddleOCR or Tesseract")
    return selected


def _engine_version(backend: str) -> str:
    if backend == "paddleocr":
        try:
            return version("paddleocr")
        except PackageNotFoundError:
            return "unknown"
    executable = shutil.which("tesseract")
    if not executable:
        return "unavailable"
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    first_line = (result.stdout or result.stderr).splitlines()
    return first_line[0].strip() if first_line else "unknown"


def _tesseract_command(language: str) -> list[str]:
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract is not installed")
    return [executable, "stdin", "stdout", "--psm", "6", "-l", language, "tsv"]


def _parse_tesseract_tsv(payload: str, *, width: int, height: int) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    image_width = max(width, 1)
    image_height = max(height, 1)
    for row in csv.DictReader(io.StringIO(payload), delimiter="\t"):
        if str(row.get("level") or "") == "1":
            try:
                page_width = int(row.get("width") or 0)
                page_height = int(row.get("height") or 0)
            except (TypeError, ValueError):
                page_width = page_height = 0
            if page_width > 0 and page_height > 0:
                image_width = page_width
                image_height = page_height
            continue
        text = " ".join(str(row.get("text") or "").split())
        if not text:
            continue
        try:
            left = int(row.get("left") or 0)
            top = int(row.get("top") or 0)
            box_width = int(row.get("width") or 0)
            box_height = int(row.get("height") or 0)
            confidence = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        if box_width <= 0 or box_height <= 0:
            continue
        regions.append({
            "text": text,
            "bbox": [
                max(0.0, left / image_width),
                max(0.0, top / image_height),
                min(1.0, (left + box_width) / image_width),
                min(1.0, (top + box_height) / image_height),
            ],
            "confidence": max(0.0, min(1.0, confidence / 100.0)),
        })
    return regions


def _image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except ImportError:
        return (1, 1)
    except Exception as exc:
        raise RuntimeError(f"Could not inspect image dimensions ({type(exc).__name__})") from exc


def _run_tesseract(path: Path, *, language: str) -> list[dict[str, Any]]:
    width, height = _image_size(path)
    command = _tesseract_command(language)
    completed = subprocess.run(
        command,
        input=path.read_bytes(),
        capture_output=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"Tesseract OCR failed{': ' + detail if detail else ''}")
    return _parse_tesseract_tsv(
        completed.stdout.decode("utf-8", errors="replace"),
        width=width,
        height=height,
    )


def _run_paddle(path: Path, *, language: str, profile: str) -> list[dict[str, Any]]:
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError("PaddleOCR is not installed") from exc
    width, height = _image_size(path)
    paddle_language = PADDLE_LANGUAGE_ALIASES.get(language, language)
    kwargs = {"lang": paddle_language}
    if profile in {"quality", "pro"}:
        kwargs.update({"use_doc_orientation_classify": False, "use_doc_unwarping": False})
    try:
        engine = PaddleOCR(**kwargs)
        result = engine.predict(str(path))
    except AttributeError:
        engine = PaddleOCR(lang=paddle_language)
        result = engine.ocr(str(path), cls=True)
    regions: list[dict[str, Any]] = []
    for page in result or []:
        payload = page if isinstance(page, dict) else getattr(page, "json", None)
        if callable(payload):
            payload = payload()
        if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
            payload = payload["res"]
        if not isinstance(payload, dict):
            continue
        boxes = payload.get("rec_polys") or payload.get("dt_polys") or payload.get("boxes") or []
        texts = payload.get("rec_texts") or payload.get("texts") or []
        scores = payload.get("rec_scores") or payload.get("scores") or []
        for box, text, score in zip(boxes, texts, scores):
            points = [(float(item[0]), float(item[1])) for item in box]
            if not points or not str(text).strip():
                continue
            left = min(item[0] for item in points)
            top = min(item[1] for item in points)
            right = max(item[0] for item in points)
            bottom = max(item[1] for item in points)
            regions.append({
                "text": " ".join(str(text).split()),
                "bbox": [
                    max(0.0, left / max(width, 1)),
                    max(0.0, top / max(height, 1)),
                    min(1.0, right / max(width, 1)),
                    min(1.0, bottom / max(height, 1)),
                ],
                "confidence": max(0.0, min(1.0, float(score))),
            })
    return regions


def available_ocr_backends() -> dict[str, bool]:
    return {
        "paddleocr": _module_available("paddleocr"),
        "tesseract": shutil.which("tesseract") is not None,
    }


def _module_available(name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def extract_image_ocr(
    path: str | Path,
    *,
    profile: str,
    language: str = "eng",
    backend: str = "auto",
) -> dict[str, Any]:
    if profile not in OCR_PROFILES:
        raise ValueError(f"Unsupported OCR profile: {profile}")
    source = Path(path).expanduser().resolve(strict=True)
    if source.is_symlink() or not source.is_file():
        raise ValueError("OCR input must be a regular local image file")
    backends = available_ocr_backends()
    selected = select_ocr_backend(backend)
    if selected == "paddleocr":
        regions = _run_paddle(source, language=language, profile=profile)
    elif selected == "tesseract":
        regions = _run_tesseract(source, language=language)
    else:
        raise ValueError(f"Unsupported OCR backend: {backend}")
    return {
        "backend": selected,
        "engine_version": _engine_version(selected),
        "profile": profile,
        "language": language,
        "regions": regions,
        "text": "\n".join(item["text"] for item in regions),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_path": str(source),
    }
