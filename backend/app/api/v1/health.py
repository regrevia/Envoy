from fastapi import APIRouter

from app.core.config import (
    MEDIA_ENABLE_FRAME_EXTRACTION,
    MEDIA_ENABLE_OCR,
    MEDIA_FRAME_INTERVAL_SECONDS,
    MEDIA_MAX_FRAME_COUNT,
    MEDIA_MAX_SEGMENT_COUNT,
    MEDIA_SEGMENT_SECONDS,
    MEDIA_TRANSCRIPTION_TIMEOUT_SEC,
)
from app.services.ollama_service import get_ollama_status

router = APIRouter()


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/health/ollama')
def ollama_health():
    return get_ollama_status()


@router.get('/health/media')
def media_health():
    return {
        "segment_seconds": MEDIA_SEGMENT_SECONDS,
        "max_segment_count": MEDIA_MAX_SEGMENT_COUNT,
        "max_input_seconds": MEDIA_SEGMENT_SECONDS * MEDIA_MAX_SEGMENT_COUNT,
        "frame_interval_seconds": MEDIA_FRAME_INTERVAL_SECONDS,
        "max_frame_count": MEDIA_MAX_FRAME_COUNT,
        "frame_extraction_enabled": MEDIA_ENABLE_FRAME_EXTRACTION,
        "ocr_enabled": MEDIA_ENABLE_OCR,
        "transcription_timeout_sec": MEDIA_TRANSCRIPTION_TIMEOUT_SEC,
    }
