import os
from pathlib import Path

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
OLLAMA_TIMEOUT_SEC = int(os.getenv("OLLAMA_TIMEOUT_SEC", "120"))
MEDIA_COMMAND_TIMEOUT_SEC = int(os.getenv("MEDIA_COMMAND_TIMEOUT_SEC", "300"))
TEXT_PREPROCESS_CHUNK_CHARS = int(os.getenv("TEXT_PREPROCESS_CHUNK_CHARS", "6000"))
TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS = int(os.getenv("TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS", "400"))
SAMPLE_GENERATION_BATCH_LIMIT = int(os.getenv("SAMPLE_GENERATION_BATCH_LIMIT", "50"))
