import os
import subprocess
from pathlib import Path
from app.core.config import MEDIA_COMMAND_TIMEOUT_SEC, MEDIA_FRAME_INTERVAL_SECONDS, MEDIA_SEGMENT_SECONDS


class MediaProcessingError(RuntimeError):
    pass


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def _error_detail(exc: Exception) -> str:
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        decoded = stderr.decode("utf-8", errors="replace").strip()
        if decoded:
            return decoded
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    return str(exc)


def _run_media_command(stage: str, cmd: list[str]):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=MEDIA_COMMAND_TIMEOUT_SEC)
    except Exception as exc:
        raise MediaProcessingError(f"{stage} failed: {_error_detail(exc)}") from exc


def split_video(video_path: str, output_dir: str):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "segment_%06d.mp4")
    if video_path:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(MEDIA_SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            output_pattern,
        ]
        _run_media_command("split_video", cmd)
        segment_paths = sorted(str(path) for path in Path(output_dir).glob("segment_*.mp4"))
        return segment_paths or [output_pattern]
    else:
        output_path = os.path.join(output_dir, "segment_000001.mp4")
        Path(output_path).touch(exist_ok=True)
        return [output_path]


def extract_frames(video_path: str, output_dir: str):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "frame_%06d.jpg")
    if video_path:
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps=1/{MEDIA_FRAME_INTERVAL_SECONDS}", output_pattern]
        _run_media_command("extract_frames", cmd)
    else:
        Path(output_pattern.replace("%06d", "000001")).touch(exist_ok=True)
    return [output_pattern]


def transcribe_audio(audio_path: str):
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)
        return {"text": result.get("text", ""), "segments": result.get("segments", [])}
    except Exception:
        return {"text": "", "segments": []}


def run_ocr_on_frames(frame_paths: list[str]):
    results = []
    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="ch")
        for path in frame_paths:
            try:
                out = ocr.ocr(path, cls=True)
                text = " ".join([item[1][0] for line in out for item in line]) if out else ""
                results.append({"frame": path, "text": text, "confidence": 0.0})
            except Exception:
                results.append({"frame": path, "text": "", "confidence": 0.0})
    except Exception:
        results = [{"frame": p, "text": "", "confidence": 0.0} for p in frame_paths]
    return results
