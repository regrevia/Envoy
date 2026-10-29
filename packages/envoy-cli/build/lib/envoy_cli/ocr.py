from __future__ import annotations

import csv
import functools
import hashlib
import importlib
import importlib.util
import inspect
import io
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


OCR_PROFILES = {"fast", "balanced", "quality", "pro"}
# Bump whenever extraction/normalization semantics change so stale caches are ignored.
OCR_PIPELINE_REVISION = "ocr-backends-v3"
OCR_BACKENDS = ("vision", "paddleocr", "rapidocr", "tesseract")
AUTO_OCR_BACKEND_PRIORITY = ("vision", "paddleocr", "rapidocr", "tesseract")
DEFAULT_OCR_BACKEND = "auto"
DEFAULT_OCR_LANGUAGE = "auto"

OCR_BACKEND_INSTALL_HINTS = {
    "vision": "install the macOS Vision extra: pip install 'envoy-cli[ocr-vision]'",
    "paddleocr": "install PaddleOCR: pip install paddleocr paddlepaddle",
    "rapidocr": "install the RapidOCR extra: pip install 'envoy-cli[ocr-rapid]'",
    "tesseract": "install the tesseract binary with language data: pip install 'envoy-cli[ocr]'",
}

PADDLE_LANGUAGE_ALIASES = {
    "chi_sim": "ch",
    "chi_tra": "chinese_cht",
}

# Canonical language families. Everything else passes through so existing callers
# that already send a valid backend-specific tag keep working.
OCR_LANGUAGE_ALIASES = {
    "auto": "auto",
    "default": "auto",
    # Simplified Chinese
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh_cn": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh_hans": "zh-Hans",
    "chs": "zh-Hans",
    "cn": "zh-Hans",
    "chinese": "zh-Hans",
    "chinese_sim": "zh-Hans",
    "chi_sim": "zh-Hans",
    "chi-sim": "zh-Hans",
    "ch": "zh-Hans",
    # Traditional Chinese
    "zh-tw": "zh-Hant",
    "zh_tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh_hk": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh_hant": "zh-Hant",
    "cht": "zh-Hant",
    "chinese_cht": "zh-Hant",
    "chi_tra": "zh-Hant",
    "chi-tra": "zh-Hant",
    # English
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "eng": "en",
    "english": "en",
}


def ocr_cache_key(
    image_sha256: str,
    *,
    profile: str,
    language: str = DEFAULT_OCR_LANGUAGE,
    backend: str = DEFAULT_OCR_BACKEND,
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


def normalize_ocr_language(language: str | None) -> str:
    """Map a user-facing language tag onto a canonical language family."""
    if language is None:
        return DEFAULT_OCR_LANGUAGE
    token = str(language).strip()
    if not token:
        return DEFAULT_OCR_LANGUAGE
    return OCR_LANGUAGE_ALIASES.get(token.lower(), token)


def resolve_ocr_language(backend: str, language: str | None = None) -> str:
    """Return the language string actually handed to ``backend``.

    ``auto`` keeps the historical English support while defaulting to Chinese,
    which is what Chinese UI screenshots and mixed Chinese/English pages need.
    """
    if backend not in OCR_BACKENDS:
        raise ValueError(f"Unsupported OCR backend: {backend}")
    canonical = normalize_ocr_language(language)
    if backend == "vision":
        # Vision accepts BCP-47 tags; keep a Chinese/English preference list so
        # mixed screenshots do not silently degrade to English-only recognition.
        return {
            "auto": "zh-Hans+en-US",
            "zh-Hans": "zh-Hans+en-US",
            "zh-Hant": "zh-Hant+en-US",
            "en": "en-US",
        }.get(canonical, canonical)
    if backend == "tesseract":
        return {
            "auto": "chi_sim+eng",
            "zh-Hans": "chi_sim+eng",
            "zh-Hant": "chi_tra+eng",
            "en": "eng",
        }.get(canonical, canonical)
    # paddleocr / rapidocr
    return {
        "auto": "ch",
        "zh-Hans": "ch",
        "zh-Hant": "chinese_cht",
        "en": "en",
    }.get(canonical, PADDLE_LANGUAGE_ALIASES.get(canonical, canonical))


def select_ocr_backend(
    backend: str = DEFAULT_OCR_BACKEND,
    *,
    available: dict[str, bool] | None = None,
) -> str:
    requested = str(backend or DEFAULT_OCR_BACKEND).strip().lower()
    if requested not in {"auto", *OCR_BACKENDS}:
        raise ValueError(
            f"Unsupported OCR backend: {backend!r}; "
            f"expected one of: auto, {', '.join(OCR_BACKENDS)}"
        )
    availability = dict(available) if available is not None else available_ocr_backends()
    if requested == "auto":
        for candidate in AUTO_OCR_BACKEND_PRIORITY:
            if availability.get(candidate):
                return candidate
        raise RuntimeError(
            "No OCR backend is installed; "
            + " or ".join(OCR_BACKEND_INSTALL_HINTS[name] for name in AUTO_OCR_BACKEND_PRIORITY)
        )
    if not availability.get(requested):
        raise RuntimeError(
            f"Requested OCR backend '{requested}' is not available; "
            f"{OCR_BACKEND_INSTALL_HINTS[requested]}"
        )
    return requested


def available_ocr_backends() -> dict[str, bool]:
    return {
        "vision": _vision_available(),
        "paddleocr": _module_available("paddleocr"),
        "rapidocr": _module_available("rapidocr_onnxruntime") or _module_available("rapidocr"),
        "tesseract": shutil.which("tesseract") is not None,
    }


def _vision_available() -> bool:
    if sys.platform != "darwin":
        return False
    return _module_available("Vision") and _module_available("Quartz")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _module_version(*distributions: str) -> str:
    for distribution in distributions:
        try:
            return version(distribution)
        except PackageNotFoundError:
            continue
    return "unknown"


def _engine_version(backend: str) -> str:
    if backend == "vision":
        runtime_version = _module_version("pyobjc-framework-Vision", "pyobjc-core")
        return f"pyobjc-vision-{runtime_version}"
    if backend == "paddleocr":
        return _module_version("paddleocr")
    if backend == "rapidocr":
        return _module_version("rapidocr-onnxruntime", "rapidocr")
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

@functools.lru_cache(maxsize=1)
def _tesseract_languages() -> frozenset[str] | None:
    """Return installed traineddata names, or None when they cannot be read."""
    executable = shutil.which("tesseract")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            check=False,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    payload = completed.stdout or completed.stderr or ""
    languages = {
        line.strip()
        for line in payload.splitlines()[1:]
        if line.strip() and " " not in line.strip()
    }
    return frozenset(languages) if languages else None


def _adapt_tesseract_language(language: str, requested: str) -> str:
    """Drop unavailable languages from an ``auto`` request.

    Tesseract aborts the whole run when one traineddata file is missing, so a
    machine without ``chi_sim`` would stop working for English screenshots.
    Explicit language requests are left untouched and surface the real error.
    """
    if requested != "auto":
        return language
    installed = _tesseract_languages()
    if not installed:
        return language
    tokens = [token for token in language.split("+") if token]
    kept = [token for token in tokens if token in installed]
    if kept:
        return "+".join(kept)
    if "eng" in installed:
        return "eng"
    return language


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
    image_bytes = path.read_bytes()
    try:
        from PIL import Image

        with Image.open(path) as image:
            has_transparency = (
                "A" in image.getbands()
                or "transparency" in image.info
            )
            if has_transparency:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                composited = Image.alpha_composite(background, rgba).convert("RGB")
                with io.BytesIO() as encoded:
                    composited.save(encoded, format="PNG")
                    image_bytes = encoded.getvalue()
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Could not preprocess transparent OCR image ({type(exc).__name__})") from exc
    completed = subprocess.run(
        command,
        input=image_bytes,
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


def _run_vision(path: Path, *, language: str) -> list[dict[str, Any]]:
    if sys.platform != "darwin":
        raise RuntimeError("macOS Vision OCR is only available on macOS")
    try:
        import Quartz  # type: ignore[import-not-found]
        import Vision  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "macOS Vision OCR is not available; "
            + OCR_BACKEND_INSTALL_HINTS["vision"]
        ) from exc
    try:
        # NSURL lives in Foundation; Quartz re-exports it on some PyObjC builds
        # only, so prefer Foundation and fall back for compatibility.
        from Foundation import NSURL  # type: ignore[import-not-found]
    except ImportError:
        NSURL = Quartz.NSURL
    url = NSURL.fileURLWithPath_(str(path))
    image_source = Quartz.CGImageSourceCreateWithURL(url, None)
    if image_source is None:
        raise RuntimeError("macOS Vision could not open the image file")
    image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
    if image is None:
        raise RuntimeError("macOS Vision could not decode the image")
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    languages = [item for item in str(language or "").split("+") if item]
    if languages:
        try:
            request.setRecognitionLanguages_(languages)
        except Exception:
            # Unknown BCP-47 tags must not disable OCR entirely; Vision keeps its
            # default recognition languages instead.
            pass
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    try:
        outcome = handler.performRequests_error_([request], None)
    except Exception as exc:
        raise RuntimeError(f"macOS Vision OCR failed ({type(exc).__name__})") from exc
    if isinstance(outcome, tuple):
        # PyObjC returns ``(BOOL, NSError)`` for methods with an NSError** out
        # parameter; older stubs return just the BOOL.
        performed = outcome[0] if outcome else None
        error = outcome[1] if len(outcome) > 1 else None
    else:
        performed, error = outcome, None
    if performed is False:
        detail = f": {error}" if error else ""
        raise RuntimeError(f"macOS Vision OCR failed to process the image{detail}")
    return _parse_vision_observations(request.results() or [])


def _parse_vision_observations(observations: Any) -> list[dict[str, Any]]:
    """Convert Vision observations into normalized top-left-origin bboxes.

    ``VNRecognizedTextObservation.boundingBox`` is normalized with a bottom-left
    origin, while the Envoy protocol uses top-left origin. x stays as-is and
    ``top = 1 - y - height``.
    """
    regions: list[dict[str, Any]] = []
    for observation in observations or []:
        try:
            candidates = observation.topCandidates_(1)
        except (AttributeError, TypeError):
            continue
        if not candidates:
            continue
        candidate = candidates[0]
        try:
            text = " ".join(str(candidate.string() or "").split())
        except (AttributeError, TypeError):
            continue
        if not text:
            continue
        try:
            confidence = max(0.0, min(1.0, float(candidate.confidence())))
        except (AttributeError, TypeError, ValueError):
            confidence = 0.0
        try:
            box = observation.boundingBox()
            left = float(box.origin.x)
            bottom = float(box.origin.y)
            box_width = float(box.size.width)
            box_height = float(box.size.height)
        except (AttributeError, TypeError, ValueError):
            continue
        if box_width <= 0 or box_height <= 0:
            continue
        bbox = [
            max(0.0, min(1.0, left)),
            max(0.0, min(1.0, 1.0 - bottom - box_height)),
            max(0.0, min(1.0, left + box_width)),
            max(0.0, min(1.0, 1.0 - bottom)),
        ]
        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            continue
        regions.append({"text": text, "bbox": bbox, "confidence": confidence})
    return regions


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
            region = _polygon_region(box, text, score, width=width, height=height)
            if region is not None:
                regions.append(region)
    return regions


def _create_rapidocr_engine(*, language: str, profile: str):
    engine_class = None
    for module_name in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        engine_class = getattr(module, "RapidOCR", None)
        if engine_class is not None:
            break
    if engine_class is None:
        raise RuntimeError(
            "RapidOCR is not installed; "
            + OCR_BACKEND_INSTALL_HINTS["rapidocr"]
        )
    kwargs: dict[str, Any] = {}
    try:
        parameters = inspect.signature(engine_class).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "lang" in parameters:
        kwargs["lang"] = str(language or "ch").split("+", 1)[0]
    return engine_class(**kwargs)


def _run_rapidocr(path: Path, *, language: str, profile: str) -> list[dict[str, Any]]:
    engine = _create_rapidocr_engine(language=language, profile=profile)
    width, height = _image_size(path)
    try:
        output = engine(str(path))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"RapidOCR failed to process the image ({type(exc).__name__})") from exc
    return _parse_rapidocr_result(output, width=width, height=height)


def _parse_rapidocr_result(output: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    if isinstance(output, tuple) and len(output) == 2:
        # rapidocr_onnxruntime returns (result, elapsed); newer releases may
        # return a result object with .boxes/.txts/.scores instead.
        output = output[0]
    rows: list[Any] = []
    if isinstance(output, dict):
        boxes = output.get("boxes") or output.get("dt_polys") or []
        texts = output.get("txts") or output.get("texts") or []
        scores = output.get("scores") or []
        rows = list(zip(boxes, texts, scores))
    elif hasattr(output, "boxes") and hasattr(output, "txts"):
        boxes = list(getattr(output, "boxes") or [])
        texts = list(getattr(output, "txts") or [])
        scores = list(getattr(output, "scores") or [])
        if not scores:
            scores = [1.0] * len(texts)
        rows = list(zip(boxes, texts, scores))
    elif isinstance(output, (list, tuple)):
        rows = list(output)
    regions: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            box = row.get("box") or row.get("dt_poly")
            text = row.get("text")
            score = row.get("score", 1.0)
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            box, text = row[0], row[1]
            score = row[2] if len(row) > 2 else 1.0
        else:
            continue
        region = _polygon_region(box, text, score, width=width, height=height)
        if region is not None:
            regions.append(region)
    return regions


def _polygon_region(
    box: Any,
    text: Any,
    score: Any,
    *,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    try:
        points = [(float(item[0]), float(item[1])) for item in box]
        cleaned = " ".join(str(text).split())
        confidence = max(0.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        return None
    if not points or not cleaned:
        return None
    image_width = max(width, 1)
    image_height = max(height, 1)
    bbox = [
        max(0.0, min(point[0] for point in points) / image_width),
        max(0.0, min(point[1] for point in points) / image_height),
        min(1.0, max(point[0] for point in points) / image_width),
        min(1.0, max(point[1] for point in points) / image_height),
    ]
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        return None
    return {"text": cleaned, "bbox": bbox, "confidence": confidence}


def extract_image_ocr(
    path: str | Path,
    *,
    profile: str,
    language: str = DEFAULT_OCR_LANGUAGE,
    backend: str = DEFAULT_OCR_BACKEND,
    available: dict[str, bool] | None = None,
) -> dict[str, Any]:
    if profile not in OCR_PROFILES:
        raise ValueError(f"Unsupported OCR profile: {profile}")
    source = Path(path).expanduser().resolve(strict=True)
    if source.is_symlink() or not source.is_file():
        raise ValueError("OCR input must be a regular local image file")
    backends = dict(available) if available is not None else available_ocr_backends()
    selected = select_ocr_backend(backend, available=backends)
    requested_language = normalize_ocr_language(language)
    resolved_language = resolve_ocr_language(selected, requested_language)
    if selected == "tesseract":
        resolved_language = _adapt_tesseract_language(resolved_language, requested_language)
    if selected == "vision":
        regions = _run_vision(source, language=resolved_language)
    elif selected == "paddleocr":
        regions = _run_paddle(source, language=resolved_language, profile=profile)
    elif selected == "rapidocr":
        regions = _run_rapidocr(source, language=resolved_language, profile=profile)
    else:
        regions = _run_tesseract(source, language=resolved_language)
    return {
        "backend": selected,
        "engine_version": _engine_version(selected),
        "profile": profile,
        "language": resolved_language,
        "language_requested": requested_language,
        "regions": regions,
        "text": "\n".join(item["text"] for item in regions),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_path": str(source),
    }


def ocr_runtime_status(available: dict[str, bool] | None = None) -> dict[str, Any]:
    """Describe backend availability and how ``auto`` would resolve right now."""
    availability = dict(available) if available is not None else available_ocr_backends()
    selected: str | None = None
    selection_error: str | None = None
    try:
        selected = select_ocr_backend("auto", available=availability)
    except RuntimeError as exc:
        selection_error = str(exc)
    return {
        "backends": {
            backend: {
                "available": availability.get(backend, False),
                "engine_version": _engine_version(backend) if availability.get(backend) else None,
                "install_hint": OCR_BACKEND_INSTALL_HINTS[backend],
            }
            for backend in OCR_BACKENDS
        },
        "auto_backend": selected,
        "auto_priority": list(AUTO_OCR_BACKEND_PRIORITY),
        "auto_error": selection_error,
        "env": {
            "ENVOY_OCR_BACKEND": os.getenv("ENVOY_OCR_BACKEND", "") or "auto",
            "ENVOY_OCR_LANG": os.getenv("ENVOY_OCR_LANG", "") or "auto",
        },
    }
