import os
from pathlib import Path


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:password@127.0.0.1:3307/envoy_v1")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
TASK_EXECUTION_MODE = os.getenv("ENVOY_TASK_EXECUTION_MODE", "local").lower()
TASK_STALE_AFTER_SECONDS = _env_positive_int("TASK_STALE_AFTER_SECONDS", 1800)
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "envoy")
STORAGE_MODE = os.getenv("ENVOY_STORAGE_MODE", "minio").lower()

BASE_DIR = Path(__file__).resolve().parents[3]
LOCAL_STORAGE_DIR = Path(os.getenv("ENVOY_LOCAL_STORAGE_DIR", str(BASE_DIR / "tmp" / "storage")))
EXPORT_DIR = Path(os.getenv("ENVOY_EXPORT_DIR", str(BASE_DIR / "tmp" / "exports")))

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_TIMEOUT_SEC = _env_positive_int("OLLAMA_TIMEOUT_SEC", 120)
OLLAMA_MAX_PROMPT_CHARS = _env_positive_int("OLLAMA_MAX_PROMPT_CHARS", 12000)
OLLAMA_RUNTIME_CONFIG_PATH = Path(os.getenv("OLLAMA_RUNTIME_CONFIG_PATH", str(BASE_DIR / "tmp" / "config" / "ollama_runtime.json")))
MEDIA_COMMAND_TIMEOUT_SEC = _env_positive_int("MEDIA_COMMAND_TIMEOUT_SEC", 300)
MEDIA_TRANSCRIPTION_TIMEOUT_SEC = _env_positive_int("MEDIA_TRANSCRIPTION_TIMEOUT_SEC", 900)
MEDIA_OCR_TIMEOUT_SEC = _env_positive_int("MEDIA_OCR_TIMEOUT_SEC", 300)
MEDIA_SEGMENT_SECONDS = _env_positive_int("MEDIA_SEGMENT_SECONDS", 600)
MEDIA_MAX_SEGMENT_COUNT = _env_positive_int("MEDIA_MAX_SEGMENT_COUNT", 12)
MEDIA_FRAME_INTERVAL_SECONDS = _env_positive_int("MEDIA_FRAME_INTERVAL_SECONDS", 15)
MEDIA_MAX_FRAME_COUNT = _env_positive_int("MEDIA_MAX_FRAME_COUNT", 240)
MEDIA_ENABLE_FRAME_EXTRACTION = _env_bool("MEDIA_ENABLE_FRAME_EXTRACTION", True)
MEDIA_ENABLE_OCR = _env_bool("MEDIA_ENABLE_OCR", True)
TEXT_PREPROCESS_CHUNK_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_CHARS", 6000)
TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS", 400)
SAMPLE_GENERATION_BATCH_LIMIT = _env_positive_int("SAMPLE_GENERATION_BATCH_LIMIT", 50)
