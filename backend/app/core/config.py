import os
from pathlib import Path


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:password@127.0.0.1:3307/envoy_v1")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
TASK_EXECUTION_MODE = os.getenv("ENVOY_TASK_EXECUTION_MODE", "local").lower()
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
MEDIA_COMMAND_TIMEOUT_SEC = _env_positive_int("MEDIA_COMMAND_TIMEOUT_SEC", 300)
MEDIA_SEGMENT_SECONDS = _env_positive_int("MEDIA_SEGMENT_SECONDS", 600)
MEDIA_FRAME_INTERVAL_SECONDS = _env_positive_int("MEDIA_FRAME_INTERVAL_SECONDS", 15)
TEXT_PREPROCESS_CHUNK_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_CHARS", 6000)
TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS", 400)
SAMPLE_GENERATION_BATCH_LIMIT = _env_positive_int("SAMPLE_GENERATION_BATCH_LIMIT", 50)
