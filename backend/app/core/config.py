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
LOCAL_TASK_MAX_CONCURRENCY = min(_env_positive_int("ENVOY_LOCAL_TASK_MAX_CONCURRENCY", 2), 8)
TASK_STALE_AFTER_SECONDS = _env_positive_int("TASK_STALE_AFTER_SECONDS", 1800)
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "envoy")
STORAGE_MODE = os.getenv("ENVOY_STORAGE_MODE", "minio").lower()

BASE_DIR = Path(__file__).resolve().parents[3]
LOCAL_STORAGE_DIR = Path(os.getenv("ENVOY_LOCAL_STORAGE_DIR", str(BASE_DIR / "tmp" / "storage")))
EXPORT_DIR = Path(os.getenv("ENVOY_EXPORT_DIR", str(BASE_DIR / "tmp" / "exports")))
VIDEO_DOWNLOAD_DIR = Path(os.getenv("ENVOY_VIDEO_DOWNLOAD_DIR", str(BASE_DIR / "tmp" / "video-downloads")))
VIDEO_BATCH_MAX_PARALLEL = min(_env_positive_int("ENVOY_VIDEO_BATCH_MAX_PARALLEL", 2), 8)
VIDEO_BATCH_MAX_ITEMS = min(_env_positive_int("ENVOY_VIDEO_BATCH_MAX_ITEMS", 20), 20)
VIDEO_PLAYLIST_DISCOVERY_LIMIT = min(_env_positive_int("ENVOY_VIDEO_PLAYLIST_DISCOVERY_LIMIT", 100), 100)
VIDEO_UNDERSTANDING_WINDOW_FRAMES = min(_env_positive_int("ENVOY_VIDEO_UNDERSTANDING_WINDOW_FRAMES", 6), 12)
VIDEO_UNDERSTANDING_WINDOW_OVERLAP = min(_env_positive_int("ENVOY_VIDEO_UNDERSTANDING_WINDOW_OVERLAP", 1), 5)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_TIMEOUT_SEC = _env_positive_int("OLLAMA_TIMEOUT_SEC", 120)
OLLAMA_MAX_PROMPT_CHARS = _env_positive_int("OLLAMA_MAX_PROMPT_CHARS", 12000)
OLLAMA_RUNTIME_CONFIG_PATH = Path(os.getenv("OLLAMA_RUNTIME_CONFIG_PATH", str(BASE_DIR / "tmp" / "config" / "ollama_runtime.json")))
MARKDOWN_CHUNK_TARGET_CHARS = _env_positive_int("MARKDOWN_CHUNK_TARGET_CHARS", 3500)
MARKDOWN_CHUNK_MAX_CHARS = _env_positive_int("MARKDOWN_CHUNK_MAX_CHARS", 5000)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
MEDIA_COMMAND_TIMEOUT_SEC = _env_positive_int("MEDIA_COMMAND_TIMEOUT_SEC", 300)
MEDIA_TRANSCRIPTION_TIMEOUT_SEC = _env_positive_int("MEDIA_TRANSCRIPTION_TIMEOUT_SEC", 900)
MEDIA_OCR_TIMEOUT_SEC = _env_positive_int("MEDIA_OCR_TIMEOUT_SEC", 300)
MEDIA_SEGMENT_SECONDS = _env_positive_int("MEDIA_SEGMENT_SECONDS", 600)
MEDIA_MAX_SEGMENT_COUNT = _env_positive_int("MEDIA_MAX_SEGMENT_COUNT", 12)
MEDIA_FRAME_INTERVAL_SECONDS = _env_positive_int("MEDIA_FRAME_INTERVAL_SECONDS", 15)
MEDIA_TARGET_FRAME_RATE = _env_positive_int("MEDIA_TARGET_FRAME_RATE", 0)
MEDIA_MAX_FRAME_COUNT = _env_positive_int("MEDIA_MAX_FRAME_COUNT", 240)
MEDIA_WHISPER_MODEL = os.getenv("MEDIA_WHISPER_MODEL", "small")
MEDIA_WHISPER_LANGUAGE = os.getenv("MEDIA_WHISPER_LANGUAGE", "zh").strip() or "zh"
MEDIA_WHISPER_INITIAL_PROMPT = os.getenv(
    "MEDIA_WHISPER_INITIAL_PROMPT",
    "这是中文游戏攻略视频，涉及球员、进阶天赋、徽章、篮球战术、技能和数值。",
).strip()
MEDIA_ENABLE_FRAME_EXTRACTION = _env_bool("MEDIA_ENABLE_FRAME_EXTRACTION", True)
MEDIA_ENABLE_OCR = _env_bool("MEDIA_ENABLE_OCR", True)
TEXT_PREPROCESS_CHUNK_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_CHARS", 6000)
TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS = _env_positive_int("TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS", 400)
SAMPLE_GENERATION_BATCH_LIMIT = _env_positive_int("SAMPLE_GENERATION_BATCH_LIMIT", 50)
