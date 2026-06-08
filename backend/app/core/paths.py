from pathlib import Path
from app.core.config import EXPORT_DIR, LOCAL_STORAGE_DIR

BASE_DIR = Path(__file__).resolve().parents[3]
TMP_DIR = BASE_DIR / "tmp"
SEGMENT_DIR = TMP_DIR / "segments"
FRAME_DIR = TMP_DIR / "frames"
AUDIO_DIR = TMP_DIR / "audio"

for p in [TMP_DIR, SEGMENT_DIR, FRAME_DIR, AUDIO_DIR, EXPORT_DIR, LOCAL_STORAGE_DIR]:
    p.mkdir(parents=True, exist_ok=True)
