from __future__ import annotations

import os
import multiprocessing
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from queue import Empty
from pathlib import Path
from app.core.config import FFMPEG_BIN, MEDIA_COMMAND_TIMEOUT_SEC, MEDIA_FRAME_INTERVAL_SECONDS, MEDIA_MAX_FRAME_COUNT, MEDIA_MAX_SEGMENT_COUNT, MEDIA_OCR_LANG, MEDIA_OCR_PROFILE, MEDIA_OCR_TIMEOUT_SEC, MEDIA_SEGMENT_SECONDS, MEDIA_TARGET_FRAME_RATE, MEDIA_TRANSCRIPTION_TIMEOUT_SEC, MEDIA_WHISPER_INITIAL_PROMPT, MEDIA_WHISPER_LANGUAGE, MEDIA_WHISPER_MODEL

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


def _terminate_subprocess(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _terminate_worker_process(process):
    process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)


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
                    _terminate_subprocess(process)
                    raise MediaProcessingError(f"{stage} canceled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_subprocess(process)
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
            FFMPEG_BIN,
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
            FFMPEG_BIN,
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


def _positive_int(value, default: int):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _frame_filter(target_frame_rate: int | None = None, frame_interval_seconds: int | None = None):
    fps = _positive_int(target_frame_rate, MEDIA_TARGET_FRAME_RATE)
    if fps > 0:
        return f"fps={fps}"
    interval = _positive_int(frame_interval_seconds, MEDIA_FRAME_INTERVAL_SECONDS)
    return f"fps=1/{interval}"


def extract_frames(
    video_path: str,
    output_dir: str,
    should_cancel: Callable[[], bool] | None = None,
    target_frame_rate: int | None = None,
    frame_interval_seconds: int | None = None,
    max_frame_count: int | None = None,
):
    ensure_dir(output_dir)
    output_pattern = os.path.join(output_dir, "frame_%06d.jpg")
    if video_path:
        frame_count = _positive_int(max_frame_count, MEDIA_MAX_FRAME_COUNT)
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i",
            video_path,
            "-vf",
            _frame_filter(target_frame_rate=target_frame_rate, frame_interval_seconds=frame_interval_seconds),
            "-frames:v",
            str(frame_count),
            "-c:v",
            "mjpeg",
            "-pix_fmt",
            "yuvj420p",
            output_pattern,
        ]
        _run_media_command("extract_frames", cmd, should_cancel=should_cancel)
        frame_paths = sorted(str(path) for path in Path(output_dir).glob("frame_*.jpg"))
        if not frame_paths and Path(video_path).is_file() and not (should_cancel and should_cancel()):
            fallback_path = os.path.join(output_dir, "frame_000001.jpg")
            fallback_cmd = [
                FFMPEG_BIN,
                "-y",
                "-i",
                video_path,
                "-vf",
                "select='eq(n,0)'",
                "-frames:v",
                "1",
                "-c:v",
                "mjpeg",
                "-pix_fmt",
                "yuvj420p",
                fallback_path,
            ]
            _run_media_command("extract_first_frame", fallback_cmd, should_cancel=should_cancel)
            frame_paths = sorted(str(path) for path in Path(output_dir).glob("frame_*.jpg"))
        return frame_paths
    else:
        Path(output_pattern.replace("%06d", "000001")).touch(exist_ok=True)
    return sorted(str(path) for path in Path(output_dir).glob("frame_*.jpg"))


def _run_whisper_transcription(audio_path: str):
    import whisper
    model_name = MEDIA_WHISPER_MODEL
    model = _WHISPER_MODELS.get(model_name)
    if model is None:
        model = whisper.load_model(model_name)
        _WHISPER_MODELS[model_name] = model
    result = model.transcribe(
        audio_path,
        language=MEDIA_WHISPER_LANGUAGE,
        task="transcribe",
        initial_prompt=MEDIA_WHISPER_INITIAL_PROMPT,
        temperature=0,
        fp16=False,
    )
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
            _terminate_worker_process(process)
            return {"status": "canceled", "text": "", "segments": [], "error": "transcription canceled"}
    if process.is_alive():
        _terminate_worker_process(process)
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


def _empty_ocr_results(frame_paths: list[str]):
    return [{"frame": p, "text": "", "confidence": 0.0} for p in frame_paths]


def _sequence_from_points(points):
    if hasattr(points, "tolist"):
        points = points.tolist()
    return points


def _bbox_from_points(points):
    points = _sequence_from_points(points)
    if not isinstance(points, (list, tuple)) or not points:
        return None
    xs = []
    ys = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
            except (TypeError, ValueError):
                continue
    if not xs or not ys:
        return None
    left = min(xs)
    top = min(ys)
    right = max(xs)
    bottom = max(ys)
    return {"x": round(left), "y": round(top), "width": round(right - left), "height": round(bottom - top)}


def _bbox_from_box(box):
    box = _sequence_from_points(box)
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    try:
        left = float(box[0])
        top = float(box[1])
        right = float(box[2])
        bottom = float(box[3])
    except (TypeError, ValueError):
        return None
    return {"x": round(left), "y": round(top), "width": round(right - left), "height": round(bottom - top)}


def _ocr_mapping_from_result(result):
    if isinstance(result, dict):
        return result
    json_value = getattr(result, "json", None)
    if callable(json_value):
        try:
            json_value = json_value()
        except Exception:
            json_value = None
    if isinstance(json_value, dict):
        return json_value.get("res") if isinstance(json_value.get("res"), dict) else json_value
    return None


def _normalize_paddle_predict_output(output):
    normalized = []
    if isinstance(output, list):
        for item in output:
            normalized.extend(_normalize_paddle_predict_output(item))
        return normalized

    mapping = _ocr_mapping_from_result(output)
    if not mapping:
        return normalized

    texts = mapping.get("rec_texts")
    if not isinstance(texts, list):
        return normalized
    scores = mapping.get("rec_scores") if isinstance(mapping.get("rec_scores"), list) else []
    polygons = mapping.get("rec_polys") if isinstance(mapping.get("rec_polys"), list) else mapping.get("dt_polys")
    if not isinstance(polygons, list):
        polygons = []
    boxes = mapping.get("rec_boxes")
    boxes = _sequence_from_points(boxes)
    if not isinstance(boxes, list):
        boxes = []

    for index, text_value in enumerate(texts):
        text = str(text_value or "").strip()
        if not text:
            continue
        try:
            confidence = float(scores[index])
        except (TypeError, ValueError, IndexError):
            confidence = 0.0
        bbox = None
        if index < len(polygons):
            bbox = _bbox_from_points(polygons[index])
        if bbox is None and index < len(boxes):
            bbox = _bbox_from_box(boxes[index])
        normalized.append({
            "text": text,
            "confidence": confidence,
            "bbox": bbox or {"x": 0, "y": 0, "width": 0, "height": 0},
        })
    return sorted(normalized, key=_ocr_sort_key)


def _flatten_paddle_ocr_output(output):
    if not isinstance(output, list):
        return []
    if output and all(
        isinstance(item, list)
        and len(item) >= 2
        and isinstance(item[0], list)
        and item[0]
        and isinstance(item[0][0], (list, tuple))
        and isinstance(item[1], (list, tuple))
        and item[1]
        and isinstance(item[1][0], str)
        for item in output
    ):
        return output
    flattened = []
    for item in output:
        flattened.extend(_flatten_paddle_ocr_output(item))
    return flattened


def _ocr_sort_key(item: dict):
    bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
    return (int(bbox.get("y", 0)), int(bbox.get("x", 0)))


def _join_ocr_items_by_lines(items: list[dict]):
    if not items:
        return ""
    lines: list[list[dict]] = []
    for item in sorted(items, key=_ocr_sort_key):
        bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
        y = int(bbox.get("y", 0))
        height = max(int(bbox.get("height", 0)), 1)
        if not lines:
            lines.append([item])
            continue
        last_bbox = lines[-1][0].get("bbox") if isinstance(lines[-1][0].get("bbox"), dict) else {}
        last_y = int(last_bbox.get("y", 0))
        threshold = max(height, int(last_bbox.get("height", 0) or 1)) * 0.6
        if abs(y - last_y) <= threshold:
            lines[-1].append(item)
        else:
            lines.append([item])
    text_lines = []
    for line in lines:
        ordered = sorted(line, key=lambda item: int((item.get("bbox") or {}).get("x", 0)))
        words = [str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip()]
        if words:
            text_lines.append(" ".join(words))
    return "\n".join(text_lines).strip()


def normalize_ocr_items(raw_items):
    normalized = _normalize_paddle_predict_output(raw_items)
    for raw in _flatten_paddle_ocr_output(raw_items):
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        bbox = _bbox_from_points(raw[0])
        payload = raw[1]
        if not isinstance(payload, (list, tuple)) or not payload:
            continue
        text = str(payload[0] or "").strip()
        if not text:
            continue
        try:
            confidence = float(payload[1])
        except (TypeError, ValueError, IndexError):
            confidence = 0.0
        normalized.append({"text": text, "confidence": confidence, "bbox": bbox or {"x": 0, "y": 0, "width": 0, "height": 0}})
    return sorted(normalized, key=_ocr_sort_key)


def compress_consecutive_ocr_results(results: list[dict]):
    compressed = []
    for item in results:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if compressed and text and text == str(compressed[-1].get("text") or "").strip():
            compressed[-1]["repeat_count"] = int(compressed[-1].get("repeat_count") or 1) + 1
            frames = compressed[-1].setdefault("frames", [compressed[-1].get("frame")])
            frames.append(item.get("frame"))
            continue
        next_item = dict(item)
        if text:
            next_item["repeat_count"] = int(next_item.get("repeat_count") or 1)
        compressed.append(next_item)
    return compressed


def _run_tesseract_ocr_logic(frame_paths: list[str]):
    if shutil.which("tesseract") is None:
        return _empty_ocr_results(frame_paths)
    results = []
    for path in frame_paths:
        try:
            image_bytes = Path(path).read_bytes()
            command = ["tesseract", "stdin", "stdout", "--psm", "6", "-l", "chi_sim+eng", "tsv"]
            try:
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=False,
                    input=image_bytes,
                    timeout=MEDIA_OCR_TIMEOUT_SEC,
                )
            except subprocess.CalledProcessError:
                command[-2] = "eng"
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=False,
                    input=image_bytes,
                    timeout=MEDIA_OCR_TIMEOUT_SEC,
                )
            stdout = completed.stdout.decode("utf-8", errors="replace") if isinstance(completed.stdout, bytes) else str(completed.stdout or "")
            lines = [line for line in stdout.splitlines() if line.strip()]
            header = lines[0].split("\t") if lines else []
            items = []
            for line in lines[1:]:
                columns = line.split("\t")
                if len(columns) < len(header):
                    columns.extend([""] * (len(header) - len(columns)))
                row = dict(zip(header, columns))
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                try:
                    confidence = float(row.get("conf") or 0)
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence < 0:
                    continue
                items.append({
                    "text": text,
                    "confidence": confidence / 100 if confidence > 1 else confidence,
                    "bbox": {
                        "x": int(float(row.get("left") or 0)),
                        "y": int(float(row.get("top") or 0)),
                        "width": int(float(row.get("width") or 0)),
                        "height": int(float(row.get("height") or 0)),
                    },
                })
            items.sort(key=_ocr_sort_key)
            result = {
                "frame": path,
                "text": _join_ocr_items_by_lines(items),
                "confidence": max((item["confidence"] for item in items), default=0.0),
            }
            if items:
                result["items"] = items
                result["reading_order"] = "top_left"
            results.append(result)
        except Exception:
            results.append({"frame": path, "text": "", "confidence": 0.0})
    return compress_consecutive_ocr_results(results)


def _run_ocr_worker_logic(frame_paths: list[str], profile: str | None = None):
    results = []
    try:
        from paddleocr import PaddleOCR
        profile_models = {
            "fast": ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"),
            "balanced": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),
            "quality": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
            "pro": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),
        }
        selected_profile = profile if profile in profile_models else MEDIA_OCR_PROFILE
        detection_model, recognition_model = profile_models[selected_profile]
        try:
            ocr = PaddleOCR(
                text_detection_model_name=detection_model,
                text_recognition_model_name=recognition_model,
                lang=MEDIA_OCR_LANG,
            )
        except TypeError:
            # Older PaddleOCR releases do not accept named v6 model selection.
            ocr = PaddleOCR(use_angle_cls=True, lang=MEDIA_OCR_LANG)
        for path in frame_paths:
            try:
                try:
                    out = ocr.ocr(path, cls=True)
                except TypeError:
                    if not hasattr(ocr, "predict"):
                        raise
                    out = ocr.predict(path)
                items = normalize_ocr_items(out)
                text = _join_ocr_items_by_lines(items)
                confidence_values = [float(item.get("confidence") or 0.0) for item in items]
                result = {
                    "frame": path,
                    "text": text,
                    "confidence": max(confidence_values) if confidence_values else 0.0,
                }
                if items:
                    result["items"] = items
                    result["reading_order"] = "top_left"
                results.append(result)
            except Exception:
                results.append({"frame": path, "text": "", "confidence": 0.0})
    except Exception:
        results = _run_tesseract_ocr_logic(frame_paths)
    return compress_consecutive_ocr_results(results)


def _ocr_worker(frame_paths: list[str], result_queue, profile: str | None = None):
    try:
        result_queue.put(_run_ocr_worker_logic(frame_paths, profile=profile))
    except Exception:
        result_queue.put(_empty_ocr_results(frame_paths))


def run_ocr_on_frames(
    frame_paths: list[str],
    should_cancel: Callable[[], bool] | None = None,
    profile: str | None = None,
):
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_ocr_worker, args=(frame_paths, result_queue, profile), daemon=True)
    timeout_sec = max(int(MEDIA_OCR_TIMEOUT_SEC or 1), 1)
    process.start()
    waited = 0
    while process.is_alive() and waited < timeout_sec:
        wait_seconds = min(1, timeout_sec - waited)
        process.join(wait_seconds)
        waited += wait_seconds
        if process.is_alive() and should_cancel is not None and should_cancel():
            _terminate_worker_process(process)
            return _empty_ocr_results(frame_paths)
    if process.is_alive():
        _terminate_worker_process(process)
        return _empty_ocr_results(frame_paths)
    try:
        result = result_queue.get_nowait()
    except Empty:
        return _empty_ocr_results(frame_paths)
    if isinstance(result, list):
        normalized = []
        for index, item in enumerate(result):
            if isinstance(item, dict):
                normalized.append({
                    "frame": item.get("frame") or (frame_paths[index] if index < len(frame_paths) else ""),
                    "text": str(item.get("text", "") or ""),
                    "confidence": float(item.get("confidence", 0.0) or 0.0),
                    **({"items": item.get("items")} if isinstance(item.get("items"), list) else {}),
                    **({"reading_order": item.get("reading_order")} if item.get("reading_order") else {}),
                    **({"repeat_count": item.get("repeat_count")} if item.get("repeat_count") else {}),
                    **({"frames": item.get("frames")} if isinstance(item.get("frames"), list) else {}),
                })
        return compress_consecutive_ocr_results(normalized) if normalized else _empty_ocr_results(frame_paths)
    return _empty_ocr_results(frame_paths)
