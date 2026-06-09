from fastapi import APIRouter

from app.services.ollama_service import get_ollama_status

router = APIRouter()


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/health/ollama')
def ollama_health():
    return get_ollama_status()
