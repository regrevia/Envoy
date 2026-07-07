from fastapi import APIRouter, HTTPException

from app.core.config import (
    MEDIA_ENABLE_FRAME_EXTRACTION,
    MEDIA_ENABLE_OCR,
    MEDIA_FRAME_INTERVAL_SECONDS,
    MEDIA_MAX_FRAME_COUNT,
    MEDIA_MAX_SEGMENT_COUNT,
    MEDIA_OCR_TIMEOUT_SEC,
    MEDIA_SEGMENT_SECONDS,
    MEDIA_TARGET_FRAME_RATE,
    MEDIA_TRANSCRIPTION_TIMEOUT_SEC,
)
from app.services.ollama_service import get_ollama_config, get_ollama_status, update_ollama_config

router = APIRouter()


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/health/ollama')
def ollama_health():
    return get_ollama_status()


@router.put('/health/ollama/config')
def configure_ollama(payload: dict):
    try:
        update_ollama_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    status = get_ollama_status(probe=False)
    config = get_ollama_config()
    return {
        **status,
        "max_prompt_chars": config["max_prompt_chars"],
        "configured_from": config["configured_from"],
    }


@router.get('/health/media')
def media_health():
    return {
        "segment_seconds": MEDIA_SEGMENT_SECONDS,
        "max_segment_count": MEDIA_MAX_SEGMENT_COUNT,
        "max_input_seconds": MEDIA_SEGMENT_SECONDS * MEDIA_MAX_SEGMENT_COUNT,
        "frame_interval_seconds": MEDIA_FRAME_INTERVAL_SECONDS,
        "target_frame_rate": MEDIA_TARGET_FRAME_RATE,
        "max_frame_count": MEDIA_MAX_FRAME_COUNT,
        "frame_extraction_enabled": MEDIA_ENABLE_FRAME_EXTRACTION,
        "ocr_enabled": MEDIA_ENABLE_OCR,
        "transcription_timeout_sec": MEDIA_TRANSCRIPTION_TIMEOUT_SEC,
        "ocr_timeout_sec": MEDIA_OCR_TIMEOUT_SEC,
    }
