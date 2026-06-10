import os
import multiprocessing
import subprocess
import tempfile
import time
from collections.abc import Callable
from queue import Empty
from pathlib import Path
from app.core.config import MEDIA_COMMAND_TIMEOUT_SEC, MEDIA_FRAME_INTERVAL_SECONDS, MEDIA_MAX_FRAME_COUNT, MEDIA_MAX_SEGMENT_COUNT, MEDIA_SEGMENT_SECONDS, MEDIA_TRANSCRIPTION_TIMEOUT_SEC

_WHISPER_MODELS = {}


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


def _run_media_command(stage: str, cmd: list[str], should_cancel: Callable[[], bool] | None = None):
    if should_cancel is None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=MEDIA_COMMAND_TIMEOUT_SEC)
        except Exception as exc:
            raise MediaProcessingError(f"{stage} failed: {_error_detail(exc)}") from exc
        return

    with tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_file)
        deadline = time.monotonic() + max(int(MEDIA_COMMAND_TIMEOUT_SEC or 1), 1)
        try:
            while process.poll() is None:
                if should_cancel():
                    process.terminate()
                    process.wait(timeout=5)
                    raise MediaProcessingError(f"{stage} canceled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.terminate()
                    process.wait(timeout=5)
                    raise MediaProcessingError(f"{stage} failed: command timed out after {MEDIA_COMMAND_TIMEOUT_SEC} sec")
                try:
                    process.wait(timeout=min(1, remaining))
                except subprocess.TimeoutExpired:
                    continue

            if process.returncode:
                stderr_file.seek(0)
                stderr = stderr_file.read()
                raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr)
        except Exception as exc:
            if isinstance(exc, MediaProcessingError):
                raise
            raise MediaProcessingError(f"{stage} failed: {_error_detail(exc)}") from exc


def split_video(video_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "segment_%06d.mp4")
    if video_path:
        max_input_seconds = max(int(MEDIA_SEGMENT_SECONDS or 1), 1) * max(int(MEDIA_MAX_SEGMENT_COUNT or 1), 1)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-t",
            str(max_input_seconds),
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
        _run_media_command("split_video", cmd, should_cancel=should_cancel)
        segment_paths = sorted(str(path) for path in Path(output_dir).glob("segment_*.mp4"))
        return segment_paths or [output_pattern]
    else:
        output_path = os.path.join(output_dir, "segment_000001.mp4")
        Path(output_path).touch(exist_ok=True)
        return [output_path]


def split_audio(audio_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "audio_segment_%06d.wav")
    if audio_path:
        max_input_seconds = max(int(MEDIA_SEGMENT_SECONDS or 1), 1) * max(int(MEDIA_MAX_SEGMENT_COUNT or 1), 1)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            audio_path,
            "-t",
            str(max_input_seconds),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(MEDIA_SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            output_pattern,
        ]
        _run_media_command("split_audio", cmd, should_cancel=should_cancel)
        segment_paths = sorted(str(path) for path in Path(output_dir).glob("audio_segment_*.wav"))
        return segment_paths or [output_pattern]
    else:
        output_path = os.path.join(output_dir, "audio_segment_000001.wav")
        Path(output_path).touch(exist_ok=True)
        return [output_path]


def extract_frames(video_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "frame_%06d.jpg")
    if video_path:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vf",
            f"fps=1/{MEDIA_FRAME_INTERVAL_SECONDS}",
            "-frames:v",
            str(MEDIA_MAX_FRAME_COUNT),
            output_pattern,
        ]
        _run_media_command("extract_frames", cmd, should_cancel=should_cancel)
        frame_paths = sorted(str(path) for path in Path(output_dir).glob("frame_*.jpg"))
        return frame_paths
    else:
        Path(output_pattern.replace("%06d", "000001")).touch(exist_ok=True)
    return sorted(str(path) for path in Path(output_dir).glob("frame_*.jpg"))


def _run_whisper_transcription(audio_path: str):
    import whisper
    model_name = "base"
    model = _WHISPER_MODELS.get(model_name)
    if model is None:
        model = whisper.load_model(model_name)
        _WHISPER_MODELS[model_name] = model
    result = model.transcribe(audio_path)
    return {"status": "success", "text": result.get("text", ""), "segments": result.get("segments", []), "error": None}


def _transcribe_audio_worker(audio_path: str, result_queue):
    try:
        result_queue.put(_run_whisper_transcription(audio_path))
    except Exception as exc:
        result_queue.put({"status": "failed", "text": "", "segments": [], "error": str(exc)})


def transcribe_audio(audio_path: str, should_cancel: Callable[[], bool] | None = None):
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_transcribe_audio_worker, args=(audio_path, result_queue), daemon=True)
    timeout_sec = max(int(MEDIA_TRANSCRIPTION_TIMEOUT_SEC or 1), 1)
    process.start()
    waited = 0
    while process.is_alive() and waited < timeout_sec:
        wait_seconds = min(1, timeout_sec - waited)
        process.join(wait_seconds)
        waited += wait_seconds
        if process.is_alive() and should_cancel is not None and should_cancel():
            process.terminate()
            process.join(5)
            return {"status": "canceled", "text": "", "segments": [], "error": "transcription canceled"}
    if process.is_alive():
        process.terminate()
        process.join(5)
        return {"status": "failed", "text": "", "segments": [], "error": f"transcription timeout after {timeout_sec} sec"}
    try:
        result = result_queue.get_nowait()
    except Empty:
        return {"status": "failed", "text": "", "segments": [], "error": "transcription worker exited without result"}
    if isinstance(result, dict):
        return {
            "status": result.get("status") or "failed",
            "text": result.get("text") or "",
            "segments": result.get("segments") if isinstance(result.get("segments"), list) else [],
            "error": result.get("error"),
        }
    return {"status": "failed", "text": "", "segments": [], "error": "transcription worker returned invalid result"}


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
