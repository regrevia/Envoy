from __future__ import annotations

import json
import base64
import hashlib
import inspect
import re
import shutil
import zipfile
import zlib
import subprocess
import tempfile
from html import escape as html_escape
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.services.crud_service import update_task_status, create_artifact, create_asset, create_task
from app.core.storage import storage_client
from app.services.media_service import MediaProcessingError, split_video, split_audio, extract_frames, transcribe_audio, run_ocr_on_frames, compress_consecutive_ocr_results
from app.services.export_service import build_dataset_jsonl, build_rich_export_report
from app.services.train_export_service import export_skill_markdown, export_markdown_bundle, export_markdown_from_video_summary, export_video_library, _video_library_slug
from app.services.analysis_service import build_analysis_payload
from app.services.ollama_service import generate_with_ollama, parse_ollama_json_object
from app.core.paths import SEGMENT_DIR, FRAME_DIR, AUDIO_DIR, EXPORT_DIR, VIDEO_DOWNLOAD_DIR
from app.core.config import MEDIA_COMMAND_TIMEOUT_SEC, MEDIA_ENABLE_FRAME_EXTRACTION, MEDIA_ENABLE_OCR, MEDIA_FRAME_INTERVAL_SECONDS, MEDIA_MAX_FRAME_COUNT, MEDIA_MAX_SEGMENT_COUNT, MEDIA_OCR_TIMEOUT_SEC, MEDIA_SEGMENT_SECONDS, MEDIA_TARGET_FRAME_RATE, SAMPLE_GENERATION_BATCH_LIMIT, TEXT_PREPROCESS_CHUNK_CHARS, TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS, VIDEO_BATCH_MAX_ITEMS, VIDEO_BATCH_MAX_PARALLEL, VIDEO_UNDERSTANDING_WINDOW_FRAMES, VIDEO_UNDERSTANDING_WINDOW_OVERLAP
from app.models.semantic_unit import SemanticUnit
from app.models.asset import Asset
from app.models.generated_sample import GeneratedSample
from app.models.processing_task import ProcessingTask
from app.models.artifact import Artifact
from app.models.trace_link import TraceLink
from app.schemas.artifact import ArtifactCreate
from app.schemas.asset import AssetCreate
from app.schemas.task import TaskCreate
from app.services.video_source_service import download_video
from app.services.vision_service import understand_window, vision_status, synthesize_video_topics


def _safe_update(db, task_id, status, progress=None, result_summary=None, error_message=None):
    if task_id:
        update_task_status(db, task_id, status, progress, result_summary, error_message)


def read_video_task_checkpoint(config: dict, stage: str):
    checkpoints = config.get("checkpoints") if isinstance(config, dict) else None
    if not isinstance(checkpoints, dict):
        return None
    value = checkpoints.get(stage)
    return value if isinstance(value, dict) else None


def merge_video_task_checkpoint(config: dict, stage: str, value: dict):
    if not isinstance(config, dict):
        return
    checkpoints = config.setdefault("checkpoints", {})
    if not isinstance(checkpoints, dict):
        checkpoints = {}
        config["checkpoints"] = checkpoints
    checkpoints[stage] = dict(value)


def _compact_error_message(message, max_chars: int = 1200):
    text = str(message or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact_lines = []
    repeated_count = 0
    previous = None
    for line in lines:
        if line == previous:
            repeated_count += 1
            continue
        if repeated_count:
            compact_lines.append(f"... repeated previous line {repeated_count} time(s)")
            repeated_count = 0
        compact_lines.append(line)
        previous = line
    if repeated_count:
        compact_lines.append(f"... repeated previous line {repeated_count} time(s)")
    compacted = "\n".join(compact_lines) if compact_lines else text
    if len(compacted) <= max_chars:
        return compacted
    omitted = len(compacted) - max_chars
    suffix = f"\n... omitted {omitted} chars"
    return compacted[: max(0, max_chars - len(suffix))].rstrip() + suffix


def _task_is_canceled(db, task_id):
    if not task_id:
        return False
    db.expire_all()
    task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
    return task is not None and task.status == "canceled"


def _semantic_unit_is_sample_source(unit: SemanticUnit, tasks_by_id: dict[str, ProcessingTask]):
    labels = unit.labels if isinstance(unit.labels, dict) else {}
    if labels.get("checkpoint") == "segment_transcript":
        return False
    task_id = labels.get("task_id")
    if not task_id:
        return True
    task = tasks_by_id.get(task_id)
    if task is None:
        return True
    return task.status == "success"


def _generated_sample_is_export_source(sample: GeneratedSample, exportable_semantic_ids: set[str], project_semantic_ids: set[str]):
    if not sample.source_unit_id:
        return True
    if sample.source_unit_id not in project_semantic_ids:
        return True
    return sample.source_unit_id in exportable_semantic_ids


def _generated_sample_has_export_content(sample: GeneratedSample):
    input_text = str(sample.input_text or "").strip()
    output_text = str(sample.output_text or "").strip()
    if not input_text or not output_text:
        return False
    return output_text != "No source content was available."


def _transcribe_audio_for_task(db, task_id: str | None, segment_path: str):
    candidate = getattr(transcribe_audio, "side_effect", None) or transcribe_audio
    try:
        signature = inspect.signature(candidate)
    except (TypeError, ValueError):
        signature = None
    if signature is not None and "should_cancel" in signature.parameters:
        return transcribe_audio(segment_path, should_cancel=lambda: _task_is_canceled(db, task_id))
    return transcribe_audio(segment_path)


def _supports_should_cancel(fn):
    candidate = getattr(fn, "side_effect", None) or fn
    try:
        signature = inspect.signature(candidate)
    except (TypeError, ValueError):
        return False
    return "should_cancel" in signature.parameters


def _supports_named_parameter(fn, name: str):
    candidate = getattr(fn, "side_effect", None) or fn
    try:
        signature = inspect.signature(candidate)
    except (TypeError, ValueError):
        return False
    return name in signature.parameters


def _split_audio_for_task(db, task_id: str | None, source_path: str, output_dir: str):
    if _supports_should_cancel(split_audio):
        return split_audio(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
    return split_audio(source_path, output_dir)


def _split_video_for_task(db, task_id: str | None, source_path: str, output_dir: str):
    if _supports_should_cancel(split_video):
        return split_video(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
    return split_video(source_path, output_dir)


def _extract_frames_for_task(db, task_id: str | None, source_path: str, output_dir: str, frame_config: dict | None = None):
    frame_kwargs = {
        key: value
        for key, value in (frame_config or {}).items()
        if key in {"target_frame_rate", "frame_interval_seconds", "max_frame_count"}
    }
    if not all(_supports_named_parameter(extract_frames, key) for key in frame_kwargs):
        frame_kwargs = {}
    if _supports_should_cancel(extract_frames):
        return extract_frames(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id), **frame_kwargs)
    return extract_frames(source_path, output_dir, **frame_kwargs)


def _run_ocr_for_task(db, task_id: str | None, frame_paths: list[str]):
    profile = None
    task = (
        db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
        if task_id and hasattr(db, "query")
        else None
    )
    if task is not None and isinstance(task.input_config, dict):
        profile = task.input_config.get("ocr_profile")
    profile_kwargs = {"profile": profile} if profile in {"fast", "balanced", "quality"} else {}
    supports_profile = _supports_named_parameter(run_ocr_on_frames, "profile")
    if _supports_should_cancel(run_ocr_on_frames):
        if supports_profile:
            return run_ocr_on_frames(frame_paths, should_cancel=lambda: _task_is_canceled(db, task_id), **profile_kwargs)
        return run_ocr_on_frames(frame_paths, should_cancel=lambda: _task_is_canceled(db, task_id))
    if supports_profile:
        return run_ocr_on_frames(frame_paths, **profile_kwargs)
    return run_ocr_on_frames(frame_paths)


def _extract_frames_for_task_compat(db, task_id: str | None, source_path: str, output_dir: str, frame_config: dict):
    if _supports_named_parameter(_extract_frames_for_task, "frame_config"):
        return _extract_frames_for_task(db, task_id, source_path, output_dir, frame_config=frame_config)
    return _extract_frames_for_task(db, task_id, source_path, output_dir)


def _canceled_result(payload: dict, semantic: dict | None = None):
    result = {"status": "canceled", "payload": payload}
    if semantic is not None:
        result["semantic"] = semantic
    return result


def _ensure_trace_link(db, source_type: str, source_id: str, target_type: str, target_id: str, relation_type: str):
    existing = (
        db.query(TraceLink)
        .filter(
            TraceLink.source_type == source_type,
            TraceLink.source_id == source_id,
            TraceLink.target_type == target_type,
            TraceLink.target_id == target_id,
            TraceLink.relation_type == relation_type,
        )
        .first()
    )
    if existing is not None:
        return existing
    link = TraceLink(id=str(uuid4()), source_type=source_type, source_id=source_id, target_type=target_type, target_id=target_id, relation_type=relation_type)
    db.add(link)
    return link


def _preprocess_semantic_units(db, asset_id: str | None, only_task_id: str | None = None, exclude_task_id: str | None = None):
    if not asset_id:
        return []
    units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).all()
    preprocess_units = []
    for unit in units:
        labels = unit.labels if isinstance(unit.labels, dict) else {}
        if labels.get("stage") != "preprocess":
            continue
        label_task_id = labels.get("task_id")
        if only_task_id is not None and label_task_id != only_task_id:
            continue
        if exclude_task_id is not None and label_task_id == exclude_task_id:
            continue
        preprocess_units.append(unit)
    return preprocess_units


def _clear_preprocess_semantic_units(db, asset_id: str | None, only_task_id: str | None = None, exclude_task_id: str | None = None):
    preprocess_units = _preprocess_semantic_units(db, asset_id, only_task_id=only_task_id, exclude_task_id=exclude_task_id)
    unit_ids = [unit.id for unit in preprocess_units]
    if not unit_ids:
        return 0
    trace_links = (
        db.query(TraceLink)
        .filter(
            (TraceLink.source_id.in_(unit_ids))
            | (TraceLink.target_id.in_(unit_ids))
        )
        .all()
    )
    for link in trace_links:
        db.delete(link)
    for unit in preprocess_units:
        db.delete(unit)
    db.commit()
    return len(preprocess_units)


def _has_previous_preprocess_units(db, asset_id: str | None, task_id: str | None):
    return bool(_preprocess_semantic_units(db, asset_id, exclude_task_id=task_id))


def _cleanup_canceled_preprocess_rerun(db, asset_id: str | None, task_id: str | None, has_previous_preprocess: bool):
    if has_previous_preprocess:
        _clear_preprocess_semantic_units(db, asset_id, only_task_id=task_id)


def _successful_preprocess_replaces_previous(db, asset_id: str | None, task_id: str | None):
    _clear_preprocess_semantic_units(db, asset_id, exclude_task_id=task_id)


def _canceled_preprocess_result(db, asset_id: str | None, task_id: str | None, has_previous_preprocess: bool, payload: dict, semantic: dict | None = None):
    _cleanup_canceled_preprocess_rerun(db, asset_id, task_id, has_previous_preprocess)
    return _canceled_result(payload, semantic)


def _is_text_asset(asset):
    if asset is None:
        return False
    mime_type = asset.mime_type or ""
    text_extensions = (".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".log", ".srt", ".vtt")
    return asset.asset_type in {"text", "markdown"} or mime_type.startswith("text/") or asset.file_name.lower().endswith(text_extensions)


def _is_document_asset(asset):
    if asset is None:
        return False
    mime_type = asset.mime_type or ""
    document_extensions = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
    return asset.asset_type == "document" or mime_type in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    } or asset.file_name.lower().endswith(document_extensions)


def _is_image_asset(asset):
    if asset is None:
        return False
    mime_type = asset.mime_type or ""
    return asset.asset_type == "image" or mime_type.startswith("image/") or asset.file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"))


def _is_audio_asset(asset):
    if asset is None:
        return False
    mime_type = asset.mime_type or ""
    return asset.asset_type == "audio" or mime_type.startswith("audio/") or asset.file_name.lower().endswith((".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"))


def _resolve_source_path(payload: dict, asset: Asset | None) -> str:
    explicit_path = payload.get("source_path") or (payload.get("input_config") or {}).get("source_path")
    if explicit_path:
        return str(explicit_path)
    if asset is not None and asset.file_path:
        if storage_client.mode == "local":
            return str(storage_client._local_path(asset.file_path))
        return str(asset.file_path)
    return ""


def _is_remote_source(source_path: str) -> bool:
    scheme = urlparse(source_path).scheme.lower()
    return scheme in {"http", "https", "s3"}


def _local_source_file_missing(source_path: str) -> bool:
    return not source_path or (not _is_remote_source(source_path) and not Path(source_path).is_file())


def _media_task_temp_dirs(task_id: str | None):
    suffix = task_id or str(uuid4())
    return SEGMENT_DIR / f"task-{suffix}", FRAME_DIR / f"task-{suffix}"


def _audio_task_temp_dir(task_id: str | None):
    suffix = task_id or str(uuid4())
    return AUDIO_DIR / f"task-{suffix}"


def _cleanup_media_task_temp_dirs(temp_dirs: list[Path]):
    tmp_root = SEGMENT_DIR.parent.resolve()
    for temp_dir in temp_dirs:
        resolved = temp_dir.resolve()
        if resolved == tmp_root or tmp_root not in resolved.parents:
            continue
        shutil.rmtree(resolved, ignore_errors=True)


def _read_video_understanding_inputs(db, asset_id: str | None):
    units = (
        db.query(SemanticUnit)
        .filter(SemanticUnit.asset_id == asset_id)
        .order_by(SemanticUnit.created_at.asc())
        .all()
    )
    frames = []
    transcript_parts = []
    transcript_segments = []
    frame_interval = MEDIA_FRAME_INTERVAL_SECONDS
    for unit in units:
        labels = unit.labels if isinstance(unit.labels, dict) else {}
        if labels.get("source") == "media_asset":
            try:
                payload = json.loads(unit.content or "")
            except (TypeError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                interval = payload.get("frame_config", {}).get("frame_interval_seconds") if isinstance(payload.get("frame_config"), dict) else None
                try:
                    frame_interval = max(int(interval or frame_interval), 1)
                except (TypeError, ValueError):
                    pass
                for path in payload.get("frames", []):
                    path = str(path).strip()
                    if not path:
                        continue
                    match = re.search(r"frame_(\d+)", Path(path).name)
                    frame_index = max(int(match.group(1)) - 1, 0) if match else len(frames)
                    frames.append({"path": path, "start_sec": frame_index * frame_interval, "end_sec": (frame_index + 1) * frame_interval})
                transcript = payload.get("transcript")
                if isinstance(transcript, dict):
                    if str(transcript.get("text") or "").strip():
                        transcript_parts.append(str(transcript["text"]).strip())
                    for segment in transcript.get("segments", []):
                        if not isinstance(segment, dict):
                            continue
                        segment_index = int(segment.get("segment_index") or 0)
                        start_sec = float(segment.get("start_sec", segment_index * MEDIA_SEGMENT_SECONDS + float(segment.get("start") or 0)))
                        end_sec = float(segment.get("end_sec", segment_index * MEDIA_SEGMENT_SECONDS + float(segment.get("end") or segment.get("start") or 0)))
                        transcript_segments.append({
                            "start_sec": start_sec,
                            "end_sec": end_sec,
                            "text": str(segment.get("text") or ""),
                        })
        elif labels.get("source") == "media_transcript":
            transcript_parts.append(str(unit.content or "").strip())
    return frames, "\n".join(dict.fromkeys(part for part in transcript_parts if part)), transcript_segments, frame_interval


def _video_understanding_unit(
    db,
    project_id: str,
    asset_id: str,
    task_id: str,
    result: dict,
    window_index: int,
    window_start_sec: int,
    window_end_sec: int,
    frame_paths: list[str] | None = None,
):
    content = json.dumps({
        "summary": result.get("text") or "",
        "error": result.get("error"),
        "model": result.get("model"),
        "frame_count": result.get("frame_count", 0),
        "source": "continuous_frame_window",
        "status": result.get("status"),
        "window_start_sec": window_start_sec,
        "window_end_sec": window_end_sec,
        "frame_paths": list(frame_paths or []),
    }, ensure_ascii=False)
    labels = {
        "stage": "understand",
        "source": "video_understanding",
        "task_id": task_id,
        "checkpoint": "vision_window",
        "window_index": window_index,
        "window_start_sec": window_start_sec,
        "window_end_sec": window_end_sec,
        "model": result.get("model"),
        "status": result.get("status"),
        "frame_paths": list(frame_paths or []),
    }
    unit = (
        db.query(SemanticUnit)
        .filter(SemanticUnit.asset_id == asset_id)
        .all()
    )
    unit = next(
        (
            item for item in unit
            if isinstance(item.labels, dict)
            and item.labels.get("task_id") == task_id
            and item.labels.get("checkpoint") == "vision_window"
            and item.labels.get("window_index") == window_index
        ),
        None,
    )
    if unit is None:
        unit = SemanticUnit(
            id=str(uuid4()),
            project_id=project_id,
            asset_id=asset_id,
            segment_id=None,
            unit_type="scene",
            title=f"连续画面理解窗口 {window_index + 1}",
            content=content,
            labels=labels,
            confidence=0.78 if result.get("status") == "success" else 0.35,
        )
        db.add(unit)
    else:
        unit.content = content
        unit.labels = labels
        unit.confidence = 0.78 if result.get("status") == "success" else 0.35
    db.commit()
    db.refresh(unit)
    return unit


def _plan_video_frame_windows(frames: list[dict], window_size: int, overlap: int) -> list[list[dict]]:
    if not frames:
        return []
    size = max(int(window_size or 1), 1)
    overlap = min(max(int(overlap or 0), 0), size - 1)
    stride = max(size - overlap, 1)
    windows = []
    for start in range(0, len(frames), stride):
        window = frames[start : start + size]
        if window:
            windows.append(window)
        if start + size >= len(frames):
            break
    return windows


def _transcript_for_video_window(
    segments: list[dict],
    full_transcript: str,
    start_sec: int,
    end_sec: int,
) -> str:
    selected = [
        str(segment.get("text") or "").strip()
        for segment in segments
        if float(segment.get("start_sec") or 0) <= end_sec
        and float(segment.get("end_sec") or segment.get("start_sec") or 0) >= start_sec
        and str(segment.get("text") or "").strip()
    ]
    return "\n".join(selected) if selected else full_transcript


def _build_file_semantic_payload(asset: Asset | None, source_path: str, source: str):
    size_bytes = None
    if source_path and not _is_remote_source(source_path):
        path = Path(source_path)
        if path.is_file():
            size_bytes = path.stat().st_size
    return {
        "file_name": asset.file_name if asset else None,
        "mime_type": asset.mime_type if asset else None,
        "asset_type": asset.asset_type if asset else None,
        "size_bytes": size_bytes,
        "source_path": source_path,
        "stage": "preprocess",
        "source": source,
        "extraction_status": "metadata_only",
    }


def _media_limit_metadata():
    segment_seconds = max(int(MEDIA_SEGMENT_SECONDS or 1), 1)
    max_segment_count = max(int(MEDIA_MAX_SEGMENT_COUNT or 1), 1)
    return {
        "segment_seconds": segment_seconds,
        "max_segment_count": max_segment_count,
        "max_input_seconds": segment_seconds * max_segment_count,
    }


def _positive_int_or_default(value, default: int):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _video_frame_config(payload: dict, task: ProcessingTask | None = None):
    config = {}
    if task is not None and isinstance(task.input_config, dict):
        config.update(task.input_config)
    payload_config = payload.get("input_config")
    if isinstance(payload_config, dict):
        config.update(payload_config)
    target_frame_rate = _positive_int_or_default(
        config.get("video_target_frame_rate") or config.get("target_frame_rate") or config.get("frame_rate"),
        MEDIA_TARGET_FRAME_RATE,
    )
    frame_interval_seconds = _positive_int_or_default(
        config.get("video_frame_interval_seconds") or config.get("frame_interval_seconds"),
        MEDIA_FRAME_INTERVAL_SECONDS,
    )
    max_frame_count = _positive_int_or_default(
        config.get("video_max_frame_count") or config.get("max_frame_count"),
        MEDIA_MAX_FRAME_COUNT,
    )
    return {
        "target_frame_rate": target_frame_rate,
        "frame_interval_seconds": frame_interval_seconds,
        "max_frame_count": max_frame_count,
        "mode": "target_fps" if target_frame_rate > 0 else "interval",
    }


def _limit_media_segments(segment_paths: list[str], source_path: str, allow_source_fallback: bool = True):
    inputs = [str(path) for path in segment_paths if str(path).strip()]
    if not inputs and allow_source_fallback:
        inputs = [source_path]
    limit = max(int(MEDIA_MAX_SEGMENT_COUNT or 1), 1)
    original_count = len(inputs)
    limited_inputs = inputs[:limit]
    return limited_inputs, original_count > len(limited_inputs), original_count


def _segment_truncation_suffix(segment_input_count: int, processed_segment_count: int):
    if segment_input_count <= processed_segment_count:
        return ""
    return f"; segment input truncated to {processed_segment_count}/{segment_input_count} segment(s)"


def _transcription_segment_summary(transcription_results: list[dict]):
    failed_segments = []
    success_count = 0
    for item in transcription_results:
        transcript = item.get("transcript") if isinstance(item, dict) else None
        if not isinstance(transcript, dict):
            continue
        if transcript.get("status") == "failed":
            failed_segments.append({
                "segment": item.get("segment"),
                "error": str(transcript.get("error") or "unknown transcription error"),
            })
            continue
        if str(transcript.get("text") or "").strip():
            success_count += 1
    failed_count = len(failed_segments)
    return {
        "success_count": success_count,
        "failed_count": failed_count,
        "failed_segments": failed_segments,
        "extraction_status": "partial_text_extracted" if failed_count else "text_extracted",
    }


def _partial_transcription_suffix(transcription_summary: dict):
    failed_count = int(transcription_summary.get("failed_count") or 0)
    if not failed_count:
        return ""
    total = int(transcription_summary.get("success_count") or 0) + failed_count
    return f"; partial transcription: {failed_count}/{max(total, failed_count)} segment(s) failed"


def _media_degraded_capabilities(transcription_summary: dict, segment_truncated: bool, transcript_text: str, ocr_text: str):
    degraded = []
    failed_count = int(transcription_summary.get("failed_count") or 0)
    success_count = int(transcription_summary.get("success_count") or 0)
    if failed_count and success_count:
        degraded.append("partial_transcript")
    elif failed_count and not transcript_text:
        degraded.append("transcript_unavailable")
    if segment_truncated:
        degraded.append("truncated_segments")
    if not ocr_text:
        degraded.append("ocr_unavailable")
    return degraded


def _media_extraction_status(transcription_summary: dict, segment_truncated: bool, transcript_text: str, ocr_text: str):
    if not transcript_text and not ocr_text:
        return "failed" if int(transcription_summary.get("failed_count") or 0) else "metadata_only"
    if _media_degraded_capabilities(transcription_summary, segment_truncated, transcript_text, ocr_text):
        return "partial_success"
    return "success"


def _decode_pdf_literal(value: bytes) -> str:
    output = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char == 92 and index + 1 < len(value):
            index += 1
            escaped = value[index]
            output.extend({
                110: b"\n",
                114: b"\r",
                116: b"\t",
                98: b"\b",
                102: b"\f",
                40: b"(",
                41: b")",
                92: b"\\",
            }.get(escaped, bytes([escaped])))
        else:
            output.append(char)
        index += 1
    return output.decode("latin-1", errors="replace")


def _extract_pdf_text_with_optional_library(source_path: str) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        PdfReader = None
    if PdfReader is None:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            PdfReader = None
    if PdfReader is None:
        return ""

    try:
        reader = PdfReader(source_path)
        return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception:
        return ""


def _extract_pdf_page_texts_with_optional_library(source_path: str) -> list[str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        PdfReader = None
    if PdfReader is None:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            PdfReader = None
    if PdfReader is None:
        return []

    try:
        reader = PdfReader(source_path)
        return [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception:
        return []


def _extract_pdf_text_fallback(source_path: str) -> str:
    try:
        data = Path(source_path).read_bytes()
    except Exception:
        return ""

    chunks = []
    for match in re.finditer(rb"stream\s*(.*?)\s*endstream", data, re.DOTALL):
        stream = match.group(1).strip(b"\r\n")
        candidates = [stream]
        try:
            candidates.append(base64.a85decode(stream, adobe=True))
        except Exception:
            pass
        for candidate in candidates:
            try:
                chunks.append(zlib.decompress(candidate))
            except Exception:
                chunks.append(candidate)

    text_parts = []
    for chunk in chunks:
        for literal in re.findall(rb"\((?:\\.|[^\\()])*\)\s*Tj", chunk):
            text_parts.append(_decode_pdf_literal(literal[1:literal.rfind(b")")]))
        for array in re.findall(rb"\[(.*?)\]\s*TJ", chunk, re.DOTALL):
            for literal in re.findall(rb"\((?:\\.|[^\\()])*\)", array):
                text_parts.append(_decode_pdf_literal(literal[1:-1]))
    return " ".join(part.strip() for part in text_parts if part.strip()).strip()


def _extract_pdf_text(source_path: str) -> str:
    return _extract_pdf_text_with_optional_library(source_path) or _extract_pdf_text_fallback(source_path)


def _extract_pdf_page_texts(source_path: str) -> list[str]:
    page_texts = _extract_pdf_page_texts_with_optional_library(source_path)
    if page_texts:
        return page_texts
    fallback_text = _extract_pdf_text_fallback(source_path)
    return [fallback_text] if fallback_text else []


def _pdf_page_ocr_available():
    return shutil.which("pdftoppm") is not None and shutil.which("tesseract") is not None and shutil.which("pdfinfo") is not None


def _extract_pdf_page_texts_with_ocr_fallback(source_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    if not _pdf_page_ocr_available():
        return []

    page_texts = [text.strip() for text in _extract_pdf_page_texts_with_optional_library(source_path)]
    if any(page_texts):
        return page_texts

    page_results = _extract_pdf_page_texts_via_ocr(source_path, output_dir, should_cancel=should_cancel)
    return [str(item.get("text", "")).strip() for item in page_results]


def _extract_pdf_page_semantics(source_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    page_texts = [text.strip() for text in _extract_pdf_page_texts_with_optional_library(source_path)]
    if any(page_texts):
        return [{"page_number": index + 1, "text": text, "source": "document_text", "mode": "text"} for index, text in enumerate(page_texts)]
    if not _pdf_page_ocr_available():
        return [{"page_number": index + 1, "text": text, "source": "document_text", "mode": "text"} for index, text in enumerate(page_texts) if text]
    page_results = _extract_pdf_page_texts_via_ocr(source_path, output_dir, should_cancel=should_cancel)
    return [
        {
            "page_number": int(item.get("page_number") or 0),
            "text": str(item.get("text") or "").strip(),
            "source": "document_ocr",
            "mode": "ocr",
            "items": item.get("items") if isinstance(item.get("items"), list) else [],
            "reading_order": item.get("reading_order") or "top_left",
            "image_path": item.get("image_path"),
        }
        for item in page_results
        if str(item.get("text") or "").strip()
    ]


PDF_CHUNKING_STRATEGIES = {"auto", "content", "module", "page"}
PDF_DEFAULT_CHUNK_TARGET_CHARS = 2500
PDF_DEFAULT_CHUNK_MAX_CHARS = 4500
PDF_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百千万零〇两\d]+[章节篇编部分]\s*[^\n]{0,60}|"
    r"\d+(?:\.\d+){1,3}\s+[\u4e00-\u9fffA-Za-z][^\n]{0,60}|"
    r"Chapter\s+\d+[^\n]{0,60})",
    re.IGNORECASE,
)


def _pdf_input_config(payload: dict, task: ProcessingTask | None = None):
    config = {}
    if task is not None and isinstance(task.input_config, dict):
        config.update(task.input_config)
    payload_config = payload.get("input_config")
    if isinstance(payload_config, dict):
        config.update(payload_config)
    strategy = str(config.get("pdf_chunking_strategy") or "auto").strip().lower()
    if strategy not in PDF_CHUNKING_STRATEGIES:
        strategy = "auto"
    try:
        target_chars = int(config.get("pdf_chunk_target_chars") or PDF_DEFAULT_CHUNK_TARGET_CHARS)
    except (TypeError, ValueError):
        target_chars = PDF_DEFAULT_CHUNK_TARGET_CHARS
    try:
        max_chars = int(config.get("pdf_chunk_max_chars") or PDF_DEFAULT_CHUNK_MAX_CHARS)
    except (TypeError, ValueError):
        max_chars = PDF_DEFAULT_CHUNK_MAX_CHARS
    target_chars = max(target_chars, 500)
    max_chars = max(max_chars, target_chars)
    return {
        "strategy": strategy,
        "target_chars": target_chars,
        "max_chars": max_chars,
        "preserve_page_refs": bool(config.get("pdf_preserve_page_refs", True)),
        "fallback_to_page_chunks": bool(config.get("pdf_fallback_to_page_chunks", True)),
    }


def _pdf_page_heading(text: str):
    for raw_line in str(text or "").splitlines()[:8]:
        line = raw_line.strip()
        if not line:
            continue
        match = PDF_HEADING_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def _pdf_page_chunk_text(pages: list[dict], preserve_page_refs: bool = True):
    parts = []
    for page in pages:
        text = str(page.get("text") or "").strip()
        if not text:
            continue
        if preserve_page_refs:
            parts.append(f"## 第 {page.get('page_number')} 页\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _new_pdf_semantic_chunk(pages: list[dict], strategy: str, preserve_page_refs: bool = True):
    if not pages:
        return None
    first_page = int(pages[0].get("page_number") or 1)
    last_page = int(pages[-1].get("page_number") or first_page)
    first_heading = next((_pdf_page_heading(str(page.get("text") or "")) for page in pages if _pdf_page_heading(str(page.get("text") or ""))), "")
    title = first_heading or (f"第 {first_page} 页" if first_page == last_page else f"第 {first_page}-{last_page} 页")
    return {
        "title": title,
        "content": _pdf_page_chunk_text(pages, preserve_page_refs=preserve_page_refs),
        "page_range": [first_page, last_page],
        "section_path": [title],
        "source": str(pages[0].get("source") or "document_text"),
        "mode": str(pages[0].get("mode") or "text"),
        "strategy": strategy,
        "page_count": len(pages),
        "ocr_pages": [
            {
                "page_number": page.get("page_number"),
                "items": page.get("items") if isinstance(page.get("items"), list) else [],
                "reading_order": page.get("reading_order") or "top_left",
                "image_path": page.get("image_path"),
            }
            for page in pages
            if page.get("mode") == "ocr"
        ],
    }


def _split_pdf_pages_by_strategy(page_semantics: list[dict], config: dict):
    pages = [
        {
            **page,
            "page_number": int(page.get("page_number") or (index + 1)),
            "text": str(page.get("text") or "").strip(),
        }
        for index, page in enumerate(page_semantics)
        if str(page.get("text") or "").strip()
    ]
    if not pages:
        return [], "empty"

    requested_strategy = str(config.get("strategy") or "auto")
    preserve_page_refs = bool(config.get("preserve_page_refs", True))
    target_chars = int(config.get("target_chars") or PDF_DEFAULT_CHUNK_TARGET_CHARS)
    max_chars = int(config.get("max_chars") or PDF_DEFAULT_CHUNK_MAX_CHARS)
    heading_count = sum(1 for page in pages if _pdf_page_heading(str(page.get("text") or "")))
    strategy = requested_strategy
    if requested_strategy == "auto":
        strategy = "module" if heading_count >= 2 else "content"
    if strategy == "module" and heading_count == 0 and bool(config.get("fallback_to_page_chunks", True)):
        strategy = "page"

    chunks = []
    current_pages = []
    current_chars = 0
    for page in pages:
        page_text = str(page.get("text") or "").strip()
        page_heading = _pdf_page_heading(page_text)
        should_start_new = False
        if strategy == "page":
            should_start_new = bool(current_pages)
        elif strategy == "module":
            should_start_new = bool(current_pages and page_heading)
        elif strategy == "content":
            should_start_new = bool(current_pages and (current_chars + len(page_text) > max_chars or (page_heading and current_chars >= target_chars)))
        if should_start_new:
            chunk = _new_pdf_semantic_chunk(current_pages, strategy, preserve_page_refs=preserve_page_refs)
            if chunk:
                chunks.append(chunk)
            current_pages = []
            current_chars = 0
        current_pages.append(page)
        current_chars += len(page_text)
    chunk = _new_pdf_semantic_chunk(current_pages, strategy, preserve_page_refs=preserve_page_refs)
    if chunk:
        chunks.append(chunk)
    return chunks, strategy


def _ocr_sort_key(item: dict):
    bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
    return (int(bbox.get("y", 0)), int(bbox.get("x", 0)))


def _join_ocr_items_by_lines(items: list[dict]):
    lines: list[list[dict]] = []
    for item in sorted(items, key=_ocr_sort_key):
        bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
        y = int(bbox.get("y", 0))
        height = max(int(bbox.get("height", 0)), 1)
        if not lines:
            lines.append([item])
            continue
        first = lines[-1][0]
        first_bbox = first.get("bbox") if isinstance(first.get("bbox"), dict) else {}
        first_y = int(first_bbox.get("y", 0))
        threshold = max(height, int(first_bbox.get("height", 0) or 1)) * 0.6
        if abs(y - first_y) <= threshold:
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


def _parse_tesseract_tsv(tsv_text: str):
    lines = [line for line in str(tsv_text or "").splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
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
            confidence = float(row.get("conf", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0:
            continue
        bbox = {
            "x": _positive_int_or_default(row.get("left"), 0),
            "y": _positive_int_or_default(row.get("top"), 0),
            "width": _positive_int_or_default(row.get("width"), 0),
            "height": _positive_int_or_default(row.get("height"), 0),
        }
        items.append({
            "text": text,
            "confidence": confidence / 100 if confidence > 1 else confidence,
            "bbox": bbox,
            "block_num": _positive_int_or_default(row.get("block_num"), 0),
            "line_num": _positive_int_or_default(row.get("line_num"), 0),
            "word_num": _positive_int_or_default(row.get("word_num"), 0),
        })
    return sorted(items, key=_ocr_sort_key)


def _extract_pdf_page_texts_via_ocr(source_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    pdfinfo_cmd = ["pdfinfo", source_path]
    try:
        pdfinfo_result = subprocess.run(pdfinfo_cmd, check=True, capture_output=True, text=True)
    except Exception:
        return []
    page_count = 0
    for line in pdfinfo_result.stdout.splitlines():
        if line.lower().startswith("pages:"):
            try:
                page_count = int(line.split(":", 1)[1].strip())
            except Exception:
                page_count = 0
            break
    if page_count <= 0:
        return []
    page_results = []
    for page_number in range(1, page_count + 1):
        if should_cancel is not None and should_cancel():
            break
        prefix = str(Path(output_dir) / f"page_{page_number:06d}")
        render_cmd = [
            "pdftoppm",
            "-png",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            source_path,
            prefix,
        ]
        try:
            subprocess.run(render_cmd, check=True, capture_output=True, timeout=MEDIA_COMMAND_TIMEOUT_SEC)
        except Exception as exc:
            page_results.append({"page_number": page_number, "image_path": "", "text": "", "confidence": 0.0, "error": f"render_failed: {exc}"})
            continue
        rendered_images = sorted(Path(output_dir).glob(f"page_{page_number:06d}-*.png"))
        image_path = str(rendered_images[0]) if rendered_images else f"{prefix}-{page_number:06d}.png"
        if not Path(image_path).is_file():
            page_results.append({"page_number": page_number, "image_path": "", "text": "", "confidence": 0.0, "error": "render_missing"})
            continue
        ocr_cmd = ["tesseract", image_path, "stdout", "--psm", "6", "-l", "chi_sim+eng", "tsv"]
        try:
            ocr_result = subprocess.run(ocr_cmd, check=True, capture_output=True, text=True, timeout=MEDIA_OCR_TIMEOUT_SEC)
            items = _parse_tesseract_tsv(str(ocr_result.stdout or ""))
            text = _join_ocr_items_by_lines(items)
        except Exception as exc:
            text = ""
            items = []
            ocr_error = str(exc)
        else:
            ocr_error = ""
        confidence_values = [float(item.get("confidence") or 0.0) for item in items]
        page_results.append({
            "page_number": page_number,
            "image_path": image_path,
            "text": text,
            "confidence": max(confidence_values) if confidence_values else (0.8 if text else 0.0),
            "items": items,
            "reading_order": "top_left" if items else None,
            "error": ocr_error,
        })
    return page_results


def _extract_pdf_page_ocr_results(source_path: str, output_dir: str, should_cancel: Callable[[], bool] | None = None):
    return _extract_pdf_page_texts_via_ocr(source_path, output_dir, should_cancel=should_cancel)


def _xml_text_nodes(xml_bytes: bytes) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []
    values = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""
        if tag in {"t", "v"} and node.text and node.text.strip():
            values.append(node.text.strip())
    return values


def _extract_ooxml_text(source_path: str) -> str:
    path = Path(source_path)
    suffix = path.suffix.lower()
    if suffix not in {".docx", ".pptx", ".xlsx"}:
        return ""
    try:
        with zipfile.ZipFile(source_path) as archive:
            names = archive.namelist()
            if suffix == ".docx":
                xml_names = [name for name in names if name.startswith("word/") and name.endswith(".xml")]
            elif suffix == ".pptx":
                xml_names = [name for name in names if name.startswith("ppt/slides/") and name.endswith(".xml")]
            else:
                xml_names = [
                    name for name in names
                    if (name == "xl/sharedStrings.xml" or name.startswith("xl/worksheets/")) and name.endswith(".xml")
                ]
            parts = []
            for name in sorted(xml_names):
                parts.extend(_xml_text_nodes(archive.read(name)))
    except (OSError, RuntimeError, zipfile.BadZipFile, KeyError):
        return ""
    return "\n".join(part for part in parts if part).strip()


def _persist_text_semantic_units(
    db,
    project_id: str,
    asset_id: str | None,
    title_base: str,
    unit_type: str,
    content: str,
    source: str,
    confidence: float,
    task_id: str | None = None,
    progress_label: str | None = None,
    progress_start: int = 15,
    progress_end: int = 95,
):
    chunks = _text_chunks(content)
    chunk_count = len(chunks)
    units = []
    for index, chunk in enumerate(chunks):
        if _task_is_canceled(db, task_id):
            break
        title = title_base if chunk_count == 1 else f"{title_base} chunk {index + 1}/{chunk_count}"
        labels = {
            "stage": "preprocess",
            "source": source,
            "task_id": task_id,
            "chunk_index": index,
            "chunk_count": chunk_count,
            "char_start": chunk["start"],
            "char_end": chunk["end"],
            "extraction_status": "text_extracted",
        }
        unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type=unit_type, title=title, content=chunk["content"], labels=labels, confidence=confidence)
        db.add(unit)
        db.commit()
        db.refresh(unit)
        units.append(unit)
        db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=unit.id, relation_type="derived_from"))
        db.commit()
        if progress_label:
            progress_span = max(progress_end - progress_start, 1)
            progress = min(progress_end, progress_start + int(((index + 1) / max(chunk_count, 1)) * progress_span))
            _safe_update(db, task_id, "running", progress, result_summary=f"{progress_label}: {index + 1}/{chunk_count}")
            if _task_is_canceled(db, task_id):
                break
    return units


def _persist_single_semantic_unit(db, project_id: str, asset_id: str | None, unit_type: str, title: str, content: str, labels: dict, confidence: float):
    unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type=unit_type, title=title, content=content, labels=labels, confidence=confidence)
    db.add(unit)
    db.commit()
    db.refresh(unit)
    db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=unit.id, relation_type="derived_from"))
    db.commit()
    return unit


def _media_transcript_checkpoints(db, asset_id: str | None, task_id: str | None, source: str = "media_transcript"):
    if not asset_id or not task_id:
        return []
    units = _preprocess_semantic_units(db, asset_id, only_task_id=task_id)
    return [
        unit
        for unit in units
        if isinstance(unit.labels, dict)
        and unit.labels.get("source") == source
        and unit.labels.get("checkpoint") == "segment_transcript"
    ]


def _clear_media_transcript_checkpoints(db, asset_id: str | None, task_id: str | None, source: str = "media_transcript"):
    checkpoints = _media_transcript_checkpoints(db, asset_id, task_id, source=source)
    if not checkpoints:
        return 0
    checkpoint_ids = [unit.id for unit in checkpoints]
    trace_links = (
        db.query(TraceLink)
        .filter(
            (TraceLink.source_id.in_(checkpoint_ids))
            | (TraceLink.target_id.in_(checkpoint_ids))
        )
        .all()
    )
    for link in trace_links:
        db.delete(link)
    for unit in checkpoints:
        db.delete(unit)
    db.commit()
    return len(checkpoints)


def _upsert_media_transcript_checkpoint(
    db,
    project_id: str,
    asset_id: str | None,
    task_id: str | None,
    transcript_parts: list[str],
    segment_index: int,
    segment_count: int,
    source: str = "media_transcript",
    title_prefix: str = "media transcript",
):
    content = "\n".join(part for part in transcript_parts if part).strip()
    if not content:
        return None
    labels = {
        "stage": "preprocess",
        "source": source,
        "task_id": task_id,
        "checkpoint": "segment_transcript",
        "segment_index": 0,
        "segment_count": segment_count,
        "segment_completed_count": segment_index + 1,
        "extraction_status": "partial_text_extracted",
    }
    title = f"{title_prefix} segments 1-{segment_index + 1}/{segment_count}"
    existing = _media_transcript_checkpoints(db, asset_id, task_id, source=source)
    if existing:
        unit = existing[0]
        unit.title = title
        unit.content = content
        unit.labels = labels
        unit.confidence = 0.86
        db.commit()
        db.refresh(unit)
        return unit
    return _persist_single_semantic_unit(db, project_id, asset_id, "transcript", title, content, labels, 0.86)


def _text_chunks(content: str, chunk_chars: int = TEXT_PREPROCESS_CHUNK_CHARS, overlap_chars: int = TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS):
    text = content or ""
    size = max(int(chunk_chars or 1), 1)
    overlap = max(min(int(overlap_chars or 0), size - 1), 0)
    if len(text) <= size:
        return [{"content": text, "start": 0, "end": len(text)}]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append({"content": text[start:end], "start": start, "end": end})
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _append_readable_parts(parts: list[str], value):
    if isinstance(value, str) and value.strip():
        parts.append(value.strip())
        return
    if isinstance(value, list):
        for item in value:
            _append_readable_parts(parts, item)
        return
    if isinstance(value, dict):
        for key in ("summary", "text", "content", "name", "rule", "title"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
                return


def _readable_source_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(parsed, (dict, list)):
        return text

    parts = []
    if isinstance(parsed, list):
        _append_readable_parts(parts, parsed)
    else:
        transcript = parsed.get("transcript")
        if isinstance(transcript, dict):
            _append_readable_parts(parts, transcript.get("text"))
        for key in ("ocr", "scenes", "rules", "concepts", "steps", "actions", "keywords"):
            _append_readable_parts(parts, parsed.get(key))
        for key in ("summary", "preview", "content", "text", "title"):
            _append_readable_parts(parts, parsed.get(key))

    deduped = []
    seen = set()
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped.append(part)
    return "\n".join(deduped).strip()


def _readable_sample_source_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(parsed, (dict, list)):
        return text

    parts = []
    if isinstance(parsed, list):
        _append_readable_parts(parts, parsed)
    else:
        transcript = parsed.get("transcript")
        if isinstance(transcript, dict):
            _append_readable_parts(parts, transcript.get("text"))
        for key in ("ocr", "scenes", "rules", "concepts", "steps", "actions", "keywords"):
            _append_readable_parts(parts, parsed.get(key))
        for key in ("summary", "preview", "content", "text", "title"):
            _append_readable_parts(parts, parsed.get(key))

    deduped = []
    seen = set()
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped.append(part)
    return "\n".join(deduped).strip()


def _semantic_unit_has_sample_content(unit: SemanticUnit | None):
    if unit is None:
        return False
    return bool(_readable_sample_source_text(unit.content))


def _semantic_unit_has_export_content(unit: SemanticUnit | None):
    if unit is None:
        return False
    return bool(_readable_source_text(unit.content))


def _source_content_format(value):
    try:
        parsed = json.loads(str(value or "").strip())
    except (TypeError, ValueError):
        return "text"
    return "json" if isinstance(parsed, (dict, list)) else "text"


def _sample_fallback(unit: SemanticUnit | None, payload: dict):
    title = (unit.title if unit else None) or payload.get("title") or "source material"
    content = (unit.content if unit else None) or payload.get("content") or payload.get("text") or ""
    content = _readable_sample_source_text(content)
    prompt = f"What are the key points from {title}?"
    answer = content[:1200] if content else "No source content was available."
    return {
        "sample_type": "qa",
        "input_text": f"{title}\n\n{prompt}",
        "output_text": answer,
        "quality_score": 0.78 if content else 0.45,
    }


def _parse_sample_text(text: str):
    if not text.strip():
        return {}
    return parse_ollama_json_object(text)


def _build_generated_sample(unit: SemanticUnit | None, payload: dict):
    fallback = _sample_fallback(unit, payload)
    source_text = _readable_sample_source_text(unit.content if unit else fallback["output_text"])
    llm_result = generate_with_ollama(
        "Create one high-quality training sample from the source material. Return JSON with sample_type, input, output, and quality_score.",
        str(source_text or ""),
    )
    if llm_result.get("status") != "success":
        return fallback

    parsed = _parse_sample_text(llm_result.get("text", ""))
    input_text = parsed.get("input") or parsed.get("input_text")
    output_text = parsed.get("output") or parsed.get("output_text")
    if not isinstance(input_text, str) or not input_text.strip() or not isinstance(output_text, str) or not output_text.strip():
        return fallback

    try:
        quality_score = float(parsed.get("quality_score", fallback["quality_score"]))
    except (TypeError, ValueError):
        quality_score = fallback["quality_score"]
    quality_score = max(0.0, min(quality_score, 1.0))

    return {
        "sample_type": str(parsed.get("sample_type") or fallback["sample_type"]),
        "input_text": input_text.strip(),
        "output_text": output_text.strip(),
        "quality_score": quality_score,
    }


def _persist_generated_sample(db, project_id: str, unit: SemanticUnit | None, payload: dict):
    source_unit_id = unit.id if unit else None
    sample_payload = _build_generated_sample(unit, payload)
    sample = GeneratedSample(
        id=str(uuid4()),
        project_id=project_id,
        source_unit_id=source_unit_id,
        sample_type=sample_payload["sample_type"],
        input_text=sample_payload["input_text"],
        output_text=sample_payload["output_text"],
        quality_score=sample_payload["quality_score"],
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    if source_unit_id:
        db.add(TraceLink(id=str(uuid4()), source_type="semantic_unit", source_id=source_unit_id, target_type="generated_sample", target_id=sample.id, relation_type="derived_from"))
        db.commit()
    return sample


def _semantic_unit_export_row(unit: SemanticUnit):
    raw_content = unit.content or ""
    readable_content = _readable_source_text(raw_content)
    return {
        "record_type": "semantic_unit",
        "id": unit.id,
        "project_id": unit.project_id,
        "asset_id": unit.asset_id,
        "segment_id": unit.segment_id,
        "unit_type": unit.unit_type,
        "title": unit.title,
        "content": readable_content,
        "content_format": _source_content_format(raw_content),
        "labels": unit.labels or {},
        "confidence": unit.confidence,
    }


def _generated_sample_export_row(sample: GeneratedSample):
    return {
        "record_type": "generated_sample",
        "id": sample.id,
        "project_id": sample.project_id,
        "source_unit_id": sample.source_unit_id,
        "sample_type": sample.sample_type,
        "input_text": sample.input_text,
        "output_text": sample.output_text,
        "quality_score": sample.quality_score,
    }


def _trace_link_export_row(link: TraceLink):
    return {
        "id": link.id,
        "source_type": link.source_type,
        "source_id": link.source_id,
        "target_type": link.target_type,
        "target_id": link.target_id,
        "relation_type": link.relation_type,
    }


def _video_export_rows(units):
    """Keep the final video document knowledge-first without duplicating its summary."""
    rows = [_semantic_unit_export_row(unit) for unit in units]
    vision_rows = [
        row for row in rows
        if isinstance(row.get("labels"), dict)
        and row["labels"].get("source") == "video_understanding"
    ]
    latest_successful_windows = {}
    for row in vision_rows:
        labels = row["labels"]
        if labels.get("status") == "success":
            latest_successful_windows[
                (row.get("asset_id"), labels.get("window_index"))
            ] = row.get("id")
    rows = [
        row for row in rows
        if not (
            isinstance(row.get("labels"), dict)
            and row["labels"].get("source") == "video_understanding"
            and latest_successful_windows.get(
                (row.get("asset_id"), row["labels"].get("window_index"))
            ) is not None
            and row.get("id") != latest_successful_windows.get(
                (row.get("asset_id"), row["labels"].get("window_index"))
            )
        )
    ]
    transcript_assets = {
        row.get("asset_id")
        for row in rows
        if row.get("asset_id")
        and isinstance(row.get("labels"), dict)
        and row["labels"].get("source") in {"media_transcript", "media_ocr"}
    }
    if not transcript_assets:
        return rows
    return [
        row
        for row in rows
        if not (
            row.get("asset_id") in transcript_assets
            and isinstance(row.get("labels"), dict)
            and row["labels"].get("source") == "media_asset"
        )
    ]


def _mixed_library_export_units(units):
    grouped = {}
    for unit in units:
        grouped.setdefault(str(unit.asset_id or ""), []).append(unit)
    primary_sources = {
        "media_transcript",
        "media_ocr",
        "video_understanding",
        "image_text",
        "document_text",
        "document_ocr",
        "text_asset",
    }
    selected = []
    for unit in units:
        labels = unit.labels if isinstance(unit.labels, dict) else {}
        source = str(labels.get("source") or "")
        if source == "media_asset":
            sibling_sources = {
                str((sibling.labels or {}).get("source") or "")
                for sibling in grouped.get(str(unit.asset_id or ""), [])
                if isinstance(sibling.labels, dict)
            }
            if sibling_sources & primary_sources:
                continue
        selected.append(unit)
    return selected


@celery_app.task(bind=True, max_retries=2)
def ping_job(self):
    return {"status": "ok"}


@celery_app.task(bind=True, max_retries=2)
def video_to_markdown_job(self, payload: dict):
    db = SessionLocal()
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    try:
        task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
        if task is None:
            raise RuntimeError(f"Video task not found: {task_id}")
        config = dict(task.input_config or {})
        source_url = str(config.get("source_url") or "").strip()
        source_asset_id = str(config.get("source_asset_id") or "").strip()
        if not source_url and not source_asset_id:
            raise RuntimeError("Video source_url or source_asset_id is missing")

        download_checkpoint = read_video_task_checkpoint(config, "download")
        if download_checkpoint and Path(str(download_checkpoint.get("path") or "")).is_file():
            download_info = download_checkpoint
            _safe_update(db, task_id, "running", 12, result_summary="download checkpoint reused")
        elif source_asset_id:
            asset = db.query(Asset).filter(Asset.id == source_asset_id).first()
            if asset is None or asset.project_id != project_id or asset.asset_type != "video":
                raise RuntimeError(f"Uploaded video asset is unavailable or invalid: {source_asset_id}")
            source_path = _resolve_source_path({}, asset)
            source_file = Path(source_path)
            if not source_file.is_file() and storage_client.client is not None:
                source_file = VIDEO_DOWNLOAD_DIR / str(task_id) / Path(asset.file_name).name
                source_file.parent.mkdir(parents=True, exist_ok=True)
                response = storage_client.client.get_object(storage_client.bucket, asset.file_path)
                try:
                    with source_file.open("wb") as output:
                        shutil.copyfileobj(response, output, length=1024 * 1024)
                finally:
                    response.close()
                    response.release_conn()
            if not source_file.is_file():
                raise RuntimeError(f"Uploaded video file is missing: {source_path}")
            metadata = asset.extra_metadata if isinstance(asset.extra_metadata, dict) else {}
            download_info = {
                "path": str(source_file),
                "video_id": source_asset_id,
                "title": str(config.get("title") or asset.file_name),
                "duration_sec": asset.duration_sec,
                "extractor": "local_upload",
                "webpage_url": str(metadata.get("source_url") or ""),
                "asset_id": asset.id,
            }
            merge_video_task_checkpoint(config, "download", {**download_info, "status": "success"})
            task.asset_id = asset.id
            task.input_config = config
            db.commit()
            _safe_update(db, task_id, "running", 15, result_summary="local video ready")
        else:
            _safe_update(db, task_id, "running", 5, result_summary="video download running")
            download_info = download_video(
                source_url,
                VIDEO_DOWNLOAD_DIR / str(task_id),
                auth=config.get("auth"),
            )
            asset = create_asset(
                db,
                AssetCreate(
                    project_id=project_id,
                    asset_type="video",
                    file_name=Path(download_info["path"]).name,
                    file_path=download_info["path"],
                    mime_type="video/mp4",
                    duration_sec=int(download_info["duration_sec"]) if download_info.get("duration_sec") else None,
                    metadata={
                        "source_url": source_url,
                        "webpage_url": download_info.get("webpage_url") or source_url,
                        "video_id": download_info.get("video_id"),
                        "extractor": download_info.get("extractor"),
                        "title": download_info.get("title"),
                    },
                ),
            )
            task.asset_id = asset.id
            config["asset_id"] = asset.id
            download_info = {**download_info, "asset_id": asset.id}
            merge_video_task_checkpoint(config, "download", {**download_info, "status": "success"})
            task.input_config = config
            db.commit()
            db.refresh(task)
            _safe_update(db, task_id, "running", 15, result_summary="video download complete")

        asset_id = str(download_info.get("asset_id") or config.get("asset_id") or task.asset_id or "")
        if not asset_id:
            asset = create_asset(
                db,
                AssetCreate(
                    project_id=project_id,
                    asset_type="video",
                    file_name=Path(download_info["path"]).name,
                    file_path=download_info["path"],
                    mime_type="video/mp4",
                    duration_sec=int(download_info["duration_sec"]) if download_info.get("duration_sec") else None,
                    metadata={"source_url": source_url, "title": download_info.get("title")},
                ),
            )
            asset_id = asset.id
            task.asset_id = asset_id
            config["asset_id"] = asset_id
            merge_video_task_checkpoint(config, "download", {**download_info, "asset_id": asset_id, "status": "success"})
            task.input_config = config
            db.commit()

        preprocess_checkpoint = read_video_task_checkpoint(config, "preprocess")
        child_task_id = str(preprocess_checkpoint.get("semantic_task_id") or "") if preprocess_checkpoint else ""
        child_task = db.query(ProcessingTask).filter(ProcessingTask.id == child_task_id).first() if child_task_id else None
        if child_task is None or child_task.status not in {"success", "partial_success"}:
            child_config = {
                **(config.get("preprocess_config") if isinstance(config.get("preprocess_config"), dict) else {}),
                "source_path": str(download_info["path"]),
                "video_frame_interval_seconds": config.get("video_frame_interval_seconds", 5),
                "video_target_frame_rate": config.get("video_target_frame_rate", 0),
                "video_max_frame_count": config.get("video_max_frame_count", 480),
                "preserve_media_temp_dirs": True,
            }
            child_task = create_task(
                db,
                TaskCreate(
                    project_id=project_id,
                    asset_id=asset_id,
                    task_type="preprocess",
                    input_config=child_config,
                ),
            )
            _safe_update(db, task_id, "running", 20, result_summary="media preprocessing running")
            preprocess_job.run({
                "task_id": child_task.id,
                "project_id": project_id,
                "asset_id": asset_id,
                "input_config": child_config,
                "source_path": str(download_info["path"]),
            })
            db.expire_all()
            child_task = db.query(ProcessingTask).filter(ProcessingTask.id == child_task.id).first()
            if child_task is None or child_task.status not in {"success", "partial_success"}:
                raise RuntimeError(
                    f"video preprocessing failed: {child_task.error_message if child_task else 'child task missing'}"
                )
            config["preprocess_config"] = child_config
            merge_video_task_checkpoint(config, "preprocess", {
                "status": "success",
                "semantic_task_id": child_task.id,
                "asset_id": asset_id,
            })
            task.input_config = config
            db.commit()
        else:
            _safe_update(db, task_id, "running", 65, result_summary="preprocess checkpoint reused")

        understand_checkpoint = read_video_task_checkpoint(config, "understand")
        if not understand_checkpoint or understand_checkpoint.get("status") not in {"success", "skipped"}:
            _safe_update(db, task_id, "running", 68, result_summary="visual understanding running")
            frames, full_transcript, transcript_segments, _frame_interval = _read_video_understanding_inputs(db, asset_id)
            if not frames:
                understanding_checkpoint = {"status": "skipped", "window_count": 0, "error": "no frames available"}
            elif not vision_status().get("enabled"):
                understanding_checkpoint = {"status": "skipped", "window_count": 0, "error": "vision provider is not configured"}
            else:
                windows = _plan_video_frame_windows(
                    frames,
                    VIDEO_UNDERSTANDING_WINDOW_FRAMES,
                    VIDEO_UNDERSTANDING_WINDOW_OVERLAP,
                )
                window_checkpoints = dict(
                    understand_checkpoint.get("windows", {})
                    if isinstance(understand_checkpoint, dict) else {}
                )
                previous_context = ""
                failures = []
                successful_count = 0
                updated_understanding = False
                for window_index, window in enumerate(windows):
                    window_start_sec = int(float(window[0].get("start_sec") or 0))
                    window_end_sec = int(float(window[-1].get("end_sec") or window[-1].get("start_sec") or 0))
                    window_key = str(window_index)
                    prior_checkpoint = window_checkpoints.get(window_key, {})
                    if prior_checkpoint.get("status") == "success":
                        unit_id = prior_checkpoint.get("unit_id")
                        prior_unit = db.query(SemanticUnit).filter(SemanticUnit.id == unit_id).first() if unit_id else None
                        if prior_unit is not None:
                            try:
                                previous_context = str(json.loads(prior_unit.content).get("summary") or "")[:2000]
                            except (TypeError, ValueError):
                                previous_context = ""
                            successful_count += 1
                            continue

                    window_transcript = _transcript_for_video_window(
                        transcript_segments,
                        full_transcript,
                        window_start_sec,
                        window_end_sec,
                    )
                    result = understand_window(
                        "识别这段游戏/教程视频画面，提取可执行步骤、界面状态变化、物品获取、战斗机制和表格/清单中每个可辨识条目。保留不确定项，不要猜测。",
                        window_transcript,
                        [str(frame["path"]) for frame in window],
                        context=previous_context,
                        window_label=f"{window_start_sec}s-{window_end_sec}s",
                    )
                    result["frame_count"] = len(window)
                    unit = _video_understanding_unit(
                        db,
                        project_id,
                        asset_id,
                        task_id,
                        result,
                        window_index,
                        window_start_sec,
                        window_end_sec,
                        frame_paths=[str(frame["path"]) for frame in window],
                    )
                    updated_understanding = True
                    window_checkpoints[window_key] = {
                        "status": result.get("status") or "failed",
                        "unit_id": unit.id,
                        "window_start_sec": window_start_sec,
                        "window_end_sec": window_end_sec,
                        "frame_count": len(window),
                        "error": result.get("error"),
                    }
                    if result.get("status") == "success":
                        successful_count += 1
                        previous_context = str(result.get("text") or "")[:2000]
                    else:
                        failures.append({"window_index": window_index, "error": result.get("error")})
                    merge_video_task_checkpoint(config, "understand", {
                        "status": "partial_success",
                        "window_count": len(windows),
                        "completed_window_count": window_index + 1,
                        "windows": window_checkpoints,
                    })
                    task.input_config = config
                    db.commit()
                    progress = min(71, 68 + int(3 * (window_index + 1) / max(len(windows), 1)))
                    _safe_update(db, task_id, "running", progress, result_summary=f"visual window {window_index + 1}/{len(windows)}")
                overall_status = "success" if not failures else ("partial_success" if successful_count else "failed")
                understanding_checkpoint = {
                    "status": overall_status,
                    "model": vision_status().get("model"),
                    "window_count": len(windows),
                    "completed_window_count": len(windows),
                    "successful_window_count": successful_count,
                    "failed_windows": failures,
                    "windows": window_checkpoints,
                }
                if updated_understanding:
                    checkpoints = config.get("checkpoints")
                    if isinstance(checkpoints, dict):
                        checkpoints.pop("export", None)
            merge_video_task_checkpoint(config, "understand", understanding_checkpoint)
            task.input_config = config
            db.commit()
        else:
            _safe_update(db, task_id, "running", 70, result_summary="visual understanding checkpoint reused")

        export_checkpoint = read_video_task_checkpoint(config, "export")
        export_path = str(export_checkpoint.get("path") or "") if export_checkpoint else ""
        if not export_path or not Path(export_path).is_file():
            _safe_update(db, task_id, "running", 72, result_summary="Markdown export running")
            units = (
                db.query(SemanticUnit)
                .filter(SemanticUnit.asset_id == asset_id)
                .order_by(SemanticUnit.created_at.asc())
                .all()
            )
            rows = _video_export_rows([
                unit
                for unit in units
                if isinstance(unit.labels, dict)
                and unit.labels.get("stage") in {"preprocess", "understand"}
                and unit.labels.get("checkpoint") != "segment_transcript"
            ])
            if not rows:
                raise RuntimeError("video preprocessing produced no exportable semantic units")
            title = str(config.get("title") or download_info.get("title") or "Video Markdown Report")
            source_url_for_export = str(
                config.get("source_url")
                or download_info.get("webpage_url")
                or ""
            )
            evidence_rows = []
            video_evidence = []
            for row in rows:
                labels = dict(row.get("labels") or {})
                labels.update({
                    "video_id": task_id,
                    "video_title": title,
                    "video_url": source_url_for_export,
                })
                row["labels"] = labels
                evidence_rows.append(row)
                video_evidence.append({
                    "id": row.get("id"),
                    "kind": row.get("unit_type") or "knowledge",
                    "content": row.get("content") or "",
                })
            video_row = {
                "video_id": task_id,
                "title": title,
                "source_url": source_url_for_export,
                "status": "success",
                "evidence_count": len(video_evidence),
                "evidence": video_evidence,
            }
            topic_result = synthesize_video_topics([video_row])
            export_result = export_video_library(
                evidence_rows=evidence_rows,
                video_rows=[video_row],
                out_dir=str(EXPORT_DIR),
                library_id=f"single-{task_id}",
                title=title,
                task_id=task_id,
                project_id=project_id,
                topic_result=topic_result,
                failed_videos=[],
                db=db,
                output_formats=config.get("output_formats"),
            )
            export_path = str(export_result["path"])
            artifact = create_artifact(
                db,
                ArtifactCreate(
                    project_id=project_id,
                    artifact_type="video_library",
                    name=Path(export_result.get("archive_path") or export_path).name,
                    file_path=export_result.get("archive_path") or export_path,
                    version="v1",
                ),
            )
            _ensure_trace_link(db, "processing_task", task_id, "artifact", artifact.id, "exported_to")
            merge_video_task_checkpoint(config, "export", {
                "status": "success",
                "path": export_path,
                "artifact_id": artifact.id,
                "manifest_path": export_result.get("manifest_path"),
                "topic_status": topic_result.get("status"),
            })
            task.input_config = config
            db.commit()
        else:
            _safe_update(db, task_id, "running", 90, result_summary="Markdown checkpoint reused")

        _safe_update(db, task_id, "success", 100, result_summary=f"video Markdown ready: {export_path}")
        if config.get("preprocess_config", {}).get("preserve_media_temp_dirs"):
            _cleanup_media_task_temp_dirs([
                SEGMENT_DIR / f"task-{child_task_id}",
                FRAME_DIR / f"task-{child_task_id}",
                AUDIO_DIR / f"task-{child_task_id}",
            ])
        return {
            "status": "video_markdown_ready",
            "path": export_path,
            "asset_id": asset_id,
            "payload": payload,
        }
    except Exception as exc:
        message = str(exc)
        _safe_update(db, task_id, "failed", 100, error_message=message)
        return {"status": "failed", "error": message, "payload": payload}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def mixed_assets_to_markdown_job(self, payload: dict):
    db = SessionLocal()
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    try:
        task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
        if task is None:
            raise RuntimeError(f"Mixed library task not found: {task_id}")
        config = dict(task.input_config or {})
        library_id = str(config.get("library_id") or "").strip()
        asset_ids = [str(asset_id) for asset_id in (config.get("asset_ids") or []) if asset_id]
        if not library_id or not asset_ids:
            raise RuntimeError("Mixed library id or asset list is missing")

        seed_task_ids = [str(seed) for seed in (config.get("seed_task_ids") or []) if seed]
        all_asset_ids = list(dict.fromkeys(asset_ids))
        for seed_id in seed_task_ids:
            seed = db.query(ProcessingTask).filter(ProcessingTask.id == seed_id).first()
            if seed is None:
                continue
            seed_config = seed.input_config if isinstance(seed.input_config, dict) else {}
            if seed.task_type == "mixed_assets_to_markdown":
                all_asset_ids.extend(str(value) for value in seed_config.get("asset_ids", []) if value)
            elif seed.task_type == "video_to_markdown" and seed.asset_id:
                all_asset_ids.append(str(seed.asset_id))
            elif seed.task_type == "video_batch_to_markdown":
                for item in seed_config.get("video_items", []):
                    if not isinstance(item, dict) or not item.get("child_task_id"):
                        continue
                    child = db.query(ProcessingTask).filter(ProcessingTask.id == str(item["child_task_id"])).first()
                    if child is not None and child.asset_id:
                        all_asset_ids.append(str(child.asset_id))
        all_asset_ids = list(dict.fromkeys(all_asset_ids))

        asset_models = {
            asset.id: asset
            for asset in db.query(Asset).filter(
                Asset.project_id == project_id,
                Asset.id.in_(all_asset_ids),
            ).all()
        }
        missing_assets = [asset_id for asset_id in all_asset_ids if asset_id not in asset_models]
        if missing_assets:
            raise RuntimeError(f"Mixed library assets not found: {', '.join(missing_assets)}")

        child_task_ids = dict(config.get("child_task_ids") or {})
        child_specs = []
        external_active = []
        for asset_id in all_asset_ids:
            asset = asset_models[asset_id]
            existing_id = str(child_task_ids.get(asset_id) or "")
            child = db.query(ProcessingTask).filter(ProcessingTask.id == existing_id).first() if existing_id else None
            if child is None:
                child = (
                    db.query(ProcessingTask)
                    .filter(
                        ProcessingTask.project_id == project_id,
                        ProcessingTask.asset_id == asset_id,
                        ProcessingTask.task_type.in_(["preprocess", "video_to_markdown"]),
                        ProcessingTask.status == "success",
                    )
                    .order_by(ProcessingTask.created_at.desc())
                    .first()
                )
            if child is not None and child.status == "success":
                child_task_ids[asset_id] = child.id
                continue
            if child is not None and child.status in {"pending", "running"}:
                child_task_ids[asset_id] = child.id
                external_active.append(child.id)
                continue

            if asset.asset_type == "video":
                child_type = "video_to_markdown"
                child_config = {
                    "source_asset_id": asset.id,
                    "title": asset.file_name,
                    "auth": {"mode": "auto"},
                    "library_id": library_id,
                    "library_title": str(config.get("library_title") or "混合素材知识库"),
                }
                child_worker = video_to_markdown_job
            else:
                child_type = "preprocess"
                child_config = {
                    "source_path": _resolve_source_path({}, asset),
                    "preserve_media_temp_dirs": False,
                }
                child_worker = preprocess_job
            child = create_task(
                db,
                TaskCreate(
                    project_id=project_id,
                    asset_id=asset.id,
                    task_type=child_type,
                    input_config=child_config,
                ),
            )
            child_task_ids[asset_id] = child.id
            child_specs.append({
                "task_id": child.id,
                "project_id": project_id,
                "asset_id": asset.id,
                "input_config": child_config,
                "worker": child_worker,
            })

        config["asset_ids"] = all_asset_ids
        config["child_task_ids"] = child_task_ids
        task.input_config = config
        db.commit()
        _safe_update(db, task_id, "running", 5, result_summary=f"mixed material processing started: {len(all_asset_ids)} asset(s)")

        parallel_limit = min(
            max(int(config.get("max_parallel") or 2), 1),
            8,
            max(len(child_specs), 1),
        )
        future_map = {}
        with ThreadPoolExecutor(max_workers=parallel_limit, thread_name_prefix="envoy-mixed-library") as executor:
            for child_payload in child_specs:
                future = executor.submit(
                    child_payload["worker"].run,
                    {
                        "task_id": child_payload["task_id"],
                        "project_id": child_payload["project_id"],
                        "asset_id": child_payload["asset_id"],
                        "input_config": child_payload["input_config"],
                        **child_payload["input_config"],
                    },
                )
                future_map[future] = child_payload["task_id"]
            for future in as_completed(future_map):
                child_id = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
                    if child is not None and child.status != "success":
                        child.status = "failed"
                        child.error_message = str(exc)
                        db.commit()

        active_ids = set(external_active)
        while active_ids:
            db.expire_all()
            for child_id in list(active_ids):
                child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
                if child is None or child.status not in {"pending", "running"}:
                    active_ids.discard(child_id)
            if active_ids:
                _safe_update(db, task_id, "running", 70, result_summary=f"waiting for existing material task(s): {len(active_ids)}")
                time.sleep(2)

        db.expire_all()
        failed_assets = []
        for asset_id, child_id in child_task_ids.items():
            child = db.query(ProcessingTask).filter(ProcessingTask.id == str(child_id)).first()
            if child is None or child.status != "success":
                failed_assets.append({
                    "asset_id": asset_id,
                    "file_name": asset_models.get(asset_id).file_name if asset_models.get(asset_id) else asset_id,
                    "error": child.error_message if child is not None else "child task not found",
                })

        units = (
            db.query(SemanticUnit)
            .filter(SemanticUnit.project_id == project_id, SemanticUnit.asset_id.in_(all_asset_ids))
            .order_by(SemanticUnit.created_at.asc())
            .all()
        )
        units = _mixed_library_export_units(units)
        rows = []
        for unit in units:
            if not isinstance(unit.labels, dict) or unit.labels.get("stage") not in {"preprocess", "understand"}:
                continue
            if unit.labels.get("checkpoint") == "segment_transcript" or not _semantic_unit_has_export_content(unit):
                continue
            row = _semantic_unit_export_row(unit)
            asset = asset_models.get(str(unit.asset_id))
            labels = dict(row.get("labels") or {})
            labels["source_name"] = asset.file_name if asset is not None else str(unit.asset_id)
            labels["source_type"] = asset.asset_type if asset is not None else "unknown"
            row["labels"] = labels
            rows.append(row)
        if not rows:
            raise RuntimeError("Mixed material preprocessing produced no exportable semantic units")

        _safe_update(db, task_id, "running", 92, result_summary="building mixed material Markdown index")
        export_result = export_markdown_bundle(
            rows,
            str(EXPORT_DIR),
            task_id,
            str(config.get("library_title") or "混合素材知识库"),
            task_name=f"mixed-library-{library_id}",
            db=db,
            project_id=project_id,
            enhance_with_ollama=bool(config.get("markdown_enhance_with_ollama", False)),
            include_video_timeline=False,
            export_root_id=f"mixed-library-{library_id}",
        )
        artifact = create_artifact(
            db,
            ArtifactCreate(
                project_id=project_id,
                artifact_type="mixed_library",
                name=Path(export_result["path"]).name,
                file_path=export_result["path"],
                version="v1",
            ),
        )
        _ensure_trace_link(db, "processing_task", task_id, "artifact", artifact.id, "exported_to")
        config["library_artifact_id"] = artifact.id
        config["library_index_path"] = export_result["path"]
        config["failed_assets"] = failed_assets
        merge_video_task_checkpoint(config, "collection_export", {
            "status": "success",
            "path": export_result["path"],
            "artifact_id": artifact.id,
        })
        task.input_config = config
        db.commit()
        if failed_assets:
            message = f"{len(failed_assets)} asset(s) failed; successful materials are preserved in the mixed library."
            _safe_update(db, task_id, "failed", 100, result_summary=f"mixed library updated: {len(rows)} semantic row(s)", error_message=message)
            return {"status": "partial", "path": export_result["path"], "artifact_id": artifact.id, "payload": payload}
        _safe_update(db, task_id, "success", 100, result_summary=f"mixed library ready: {len(all_asset_ids)} asset(s), {len(rows)} semantic row(s)")
        return {"status": "mixed_library_ready", "path": export_result["path"], "artifact_id": artifact.id, "payload": payload}
    except Exception as exc:
        _safe_update(db, task_id, "failed", 100, error_message=str(exc))
        return {"status": "failed", "error": str(exc), "payload": payload}
    finally:
        db.close()


def _create_public_page_asset(db, project_id: str, file_name: str, data: bytes, mime_type: str, metadata: dict):
    storage_name = f"public-pages/{uuid4().hex}{Path(file_name).suffix.lower()}"
    storage_info = storage_client.upload_bytes(storage_name, data, mime_type)
    if storage_info.get("storage") not in {"local", "minio"}:
        raise RuntimeError(f"Storage backend unavailable: {storage_info.get('reason') or 'unknown'}")
    return create_asset(
        db,
        AssetCreate(
            project_id=project_id,
            asset_type="image" if mime_type.startswith("image/") else "markdown",
            file_name=file_name,
            file_path=storage_info["object_name"],
            mime_type=mime_type,
            metadata={
                **metadata,
                "storage": storage_info.get("storage"),
            },
        ),
    )


def _markdown_text(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _read_asset_bytes_for_export(asset: Asset) -> bytes:
    if storage_client.mode == "local":
        return storage_client.read_bytes(asset.file_path)
    if storage_client.client is None:
        raise RuntimeError("MinIO storage client is unavailable while exporting public page assets")
    response = storage_client.client.get_object(storage_client.bucket, asset.file_path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _attach_public_page_source_to_bundle(
    db,
    task_id: str,
    project_id: str,
    bundle_path: str,
    source_url: str,
    page_info: dict,
    text_asset: Asset,
    image_assets: list[Asset],
):
    from app.services.web_content_service import image_extension, public_page_material_response

    bundle_location = Path(bundle_path)
    if bundle_location.is_dir():
        root = bundle_location
    else:
        root = next(
            (
                parent
                for parent in bundle_location.parents
                if (parent / "manifest.json").is_file() and (parent / "index.md").is_file()
            ),
            bundle_location.parent,
        )
    if not root.is_dir():
        raise RuntimeError(f"Public page export directory is missing: {root}")
    image_dir = root / "assets" / "images"
    source_dir = root / "sources"
    image_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    image_records = []
    for index, asset in enumerate(image_assets, start=1):
        metadata = asset.extra_metadata if isinstance(asset.extra_metadata, dict) else {}
        artifact_name = str(metadata.get("artifact_filename") or "")
        if not artifact_name:
            content_type = str(asset.mime_type or "image/jpeg")
            artifact_name = f"image-{index:02d}{image_extension(content_type)}"
        target = image_dir / Path(artifact_name).name
        image_bytes = _read_asset_bytes_for_export(asset)
        if not target.is_file():
            target.write_bytes(image_bytes)
        image_records.append({
            "id": asset.id,
            "asset_id": asset.id,
            "file_name": target.name,
            "alt": str(metadata.get("alt") or f"配图 {index}"),
            "mime_type": str(asset.mime_type or "application/octet-stream"),
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
        })

    source_text = str(page_info.get("text") or "").strip()
    if not source_text:
        source_text = _read_asset_bytes_for_export(text_asset).decode("utf-8", errors="replace")
    title = str(page_info.get("title") or text_asset.file_name)
    source_slug = _video_library_slug(title) or "source"
    source_markdown_path = source_dir / f"{source_slug}.md"
    markdown_parts = [
        f"# {_markdown_text(title)}",
        f"来源：{source_url}",
    ]
    if page_info.get("author"):
        markdown_parts.append(f"作者：{_markdown_text(page_info['author'])}")
    if page_info.get("published_at"):
        markdown_parts.append(f"发布时间：{page_info['published_at']}")
    markdown_parts.extend(["", source_text.strip()])
    if image_records:
        markdown_parts.extend(["", "## 原始配图"])
        markdown_parts.extend(
            f"![{_markdown_text(record['alt'])}](../assets/images/{record['file_name']})"
            for record in image_records
        )
    source_markdown_path.write_text("\n\n".join(markdown_parts).rstrip() + "\n", encoding="utf-8")

    materials = public_page_material_response(
        page={
            **page_info,
            "title": title,
            "text": source_text,
            "images": image_records,
        },
        source_url=source_url,
        image_assets=image_records,
        request_id=f"web-page-{task_id}",
    )
    materials_path = root / "materials.json"
    materials_path.write_text(
        json.dumps(materials.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    index_path = root / "index.md"
    if index_path.is_file():
        index_text = index_path.read_text(encoding="utf-8")
        source_link = f"[{title}](sources/{source_markdown_path.name})"
        if source_link not in index_text:
            index_path.write_text(
                index_text.rstrip() + f"\n\n## 原始来源\n\n- {source_link}\n",
                encoding="utf-8",
            )

    html_path = root / "index.html"
    if html_path.is_file():
        body = "<p>".join(html_escape(line) for line in source_text.splitlines() if line.strip())
        figures = "".join(
            (
                '<figure><img src="assets/images/'
                + html_escape(record["file_name"], quote=True)
                + '" alt="'
                + html_escape(record["alt"], quote=True)
                + '"><figcaption>'
                + html_escape(record["alt"])
                + "</figcaption></figure>"
            )
            for record in image_records
        )
        source_html = (
            '<section class="envoy-source"><h1>'
            + html_escape(title)
            + '</h1><p><a href="'
            + html_escape(source_url, quote=True)
            + '">原始链接</a></p><div>'
            + body
            + "</div>"
            + figures
            + "</section>"
        )
        html_text = html_path.read_text(encoding="utf-8")
        if "</body>" in html_text and source_html not in html_text:
            html_path.write_text(html_text.replace("</body>", source_html + "</body>", 1), encoding="utf-8")
    elif set(page_info.get("output_formats") or []) & {"html", "both"}:
        body = "".join(
            f"<p>{html_escape(line)}</p>"
            for line in source_text.splitlines()
            if line.strip()
        )
        figures = "".join(
            (
                '<figure><img src="assets/images/'
                + html_escape(record["file_name"], quote=True)
                + '" alt="'
                + html_escape(record["alt"], quote=True)
                + '"><figcaption>'
                + html_escape(record["alt"])
                + "</figcaption></figure>"
            )
            for record in image_records
        )
        html_path.write_text(
            "\n".join([
                "<!doctype html>",
                '<html lang="zh-CN"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                f"<title>{html_escape(title)}</title>",
                "<style>body{margin:0;color:#202124;background:#fff;font:16px/1.8 system-ui,-apple-system,sans-serif}main{max-width:860px;margin:0 auto;padding:32px 22px}h1{font-size:28px;line-height:1.35}a{color:#2563eb}figure{margin:24px 0}img{display:block;width:100%;height:auto}figcaption{color:#667085;font-size:13px}p{white-space:pre-wrap}</style>",
                "</head><body><main>",
                f"<h1>{html_escape(title)}</h1>",
                f'<p><a href="{html_escape(source_url, quote=True)}">原始链接</a></p>',
                body,
                figures,
                "</main></body></html>",
            ]),
            encoding="utf-8",
        )

    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            manifest = {}
        sources = manifest.setdefault("source_pages", [])
        if not isinstance(sources, list):
            sources = []
            manifest["source_pages"] = sources
        source_record = {
            "title": title,
            "source_url": source_url,
            "content_type": page_info.get("content_type"),
            "markdown_path": f"sources/{source_markdown_path.name}",
            "materials_path": "materials.json",
            "image_count": len(image_records),
            "images": image_records,
            "output_formats": page_info.get("output_formats") or ["markdown"],
        }
        sources[:] = [
            item for item in sources
            if not isinstance(item, dict) or item.get("source_url") != source_url
        ]
        sources.append(source_record)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    artifact_id = str(page_info.get("artifact_id") or "")
    if artifact_id:
        archive_path = Path(shutil.make_archive(str(root), "zip", root_dir=str(root)))
        artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if artifact is not None:
            artifact.file_path = str(archive_path)
            artifact.name = archive_path.name
            db.commit()
        _ensure_trace_link(db, "processing_task", task_id, "artifact", artifact_id, "exported_to")
    return {
        "markdown_path": str(source_markdown_path),
        "materials_path": str(materials_path),
        "image_count": len(image_records),
        "archive_path": str(archive_path) if artifact_id else "",
    }


@celery_app.task(bind=True, max_retries=2)
def public_page_to_markdown_job(self, payload: dict):
    from app.services.web_content_service import (
        PublicPageError,
        download_public_image,
        extract_public_page,
        image_extension,
    )

    db = SessionLocal()
    task_id = str(payload.get("task_id") or "")
    project_id = str(payload.get("project_id") or "")
    try:
        task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
        if task is None:
            raise RuntimeError(f"Public page task not found: {task_id}")
        config = dict(task.input_config or {})
        source_url = str(config.get("source_url") or "").strip()
        if not source_url:
            raise RuntimeError("Public page source URL is missing")

        page_info = config.get("page_extraction")
        if not isinstance(page_info, dict):
            _safe_update(db, task_id, "running", 5, result_summary="opening public page")
            page_info = extract_public_page(source_url)
            if not page_info.get("text") and not page_info.get("images"):
                raise PublicPageError("Public page contains no readable text or downloadable images")
            page_info = {
                "title": str(page_info.get("title") or config.get("title") or "Web page"),
                "author": str(page_info.get("author") or ""),
                "published_at": str(page_info.get("published_at") or ""),
                "content_type": str(page_info.get("content_type") or "article"),
                "text": str(page_info.get("text") or ""),
                "images": list(page_info.get("images") or []),
                "video_urls": list(page_info.get("video_urls") or []),
            }
            config["page_extraction"] = page_info
            config["page_image_asset_ids"] = dict(config.get("page_image_asset_ids") or {})
            task.input_config = config
            db.commit()
        if str(page_info.get("author") or "").strip() in {"我的", "关注", "朋友"}:
            page_info["author"] = ""
            config["page_extraction"] = page_info
            task.input_config = config
            db.commit()

        asset_ids = list(config.get("source_asset_ids") or [])
        text_asset_id = str(config.get("source_text_asset_id") or "")
        text_asset = db.query(Asset).filter(Asset.id == text_asset_id).first() if text_asset_id else None
        if text_asset is None:
            page_text = "\n\n".join(filter(None, [
                f"# {_markdown_text(page_info['title'])}",
                f"作者：{_markdown_text(page_info['author'])}" if page_info.get("author") else "",
                f"发布时间：{_markdown_text(page_info['published_at'])}" if page_info.get("published_at") else "",
                f"来源：{source_url}",
                _markdown_text(str(page_info.get("text") or "")),
            ])).strip() + "\n"
            text_asset = _create_public_page_asset(
                db,
                project_id,
                f"{_video_library_slug(str(page_info['title'])) or 'web-page'}-source.md",
                page_text.encode("utf-8"),
                "text/markdown",
                {
                    "source_url": source_url,
                    "source_kind": str(config.get("source_kind") or "public_page"),
                    "content_type": page_info.get("content_type"),
                },
            )
            text_asset_id = text_asset.id
            config["source_text_asset_id"] = text_asset_id
            asset_ids.append(text_asset_id)
            config["source_asset_ids"] = asset_ids
            task.input_config = config
            db.commit()

        image_assets_by_index = dict(config.get("page_image_asset_ids") or {})
        images = page_info.get("images") if isinstance(page_info.get("images"), list) else []
        total_image_bytes = sum(
            int(
                (asset.extra_metadata if isinstance(asset.extra_metadata, dict) else {}).get("size_bytes")
                or 0
            )
            for asset_id in image_assets_by_index.values()
            for asset in [db.query(Asset).filter(Asset.id == str(asset_id)).first()]
            if asset is not None
        )
        for index, image in enumerate(images):
            key = str(index)
            existing_id = str(image_assets_by_index.get(key) or "")
            existing_asset = db.query(Asset).filter(Asset.id == existing_id).first() if existing_id else None
            if existing_asset is not None:
                continue
            _safe_update(
                db,
                task_id,
                "running",
                min(25, 8 + int((index / max(len(images), 1)) * 17)),
                result_summary=f"downloading source images: {index + 1}/{len(images)}",
            )
            image_bytes, content_type = download_public_image(
                str(image.get("url") or ""),
                source_url,
            )
            total_image_bytes += len(image_bytes)
            if total_image_bytes > 200 * 1024 * 1024:
                raise PublicPageError("The page's images exceed the 200 MB total size limit")
            extension = image_extension(content_type)
            file_name = f"image-{index + 1:02d}{extension}"
            image_asset = _create_public_page_asset(
                db,
                project_id,
                file_name,
                image_bytes,
                content_type,
                {
                    "source_url": source_url,
                    "source_kind": str(config.get("source_kind") or "public_page"),
                    "source_image_index": index,
                    "artifact_filename": file_name,
                    "alt": str(image.get("alt") or f"配图 {index + 1}"),
                    "size_bytes": len(image_bytes),
                },
            )
            image_assets_by_index[key] = image_asset.id
            asset_ids.append(image_asset.id)
            config["page_image_asset_ids"] = image_assets_by_index
            config["source_asset_ids"] = asset_ids
            task.input_config = config
            db.commit()

        for index, video_url in enumerate(page_info.get("video_urls") or []):
            key = f"video-{index}"
            video_asset_id = str((config.get("page_video_asset_ids") or {}).get(key) or "")
            if video_asset_id and db.query(Asset).filter(Asset.id == video_asset_id).first() is not None:
                continue
            output_dir = Path(VIDEO_DOWNLOAD_DIR) / f"public-page-{task_id}-{index}"
            download_info = download_video(str(video_url), output_dir, auth={"mode": "public"})
            video_path = Path(download_info["path"])
            file_size = video_path.stat().st_size
            if file_size <= 0 or file_size > 1024 * 1024 * 1024:
                raise PublicPageError("Embedded video is empty or exceeds the 1 GB page-media limit")
            object_name = f"public-pages/{uuid4().hex}{video_path.suffix.lower() or '.mp4'}"
            with video_path.open("rb") as video_file:
                storage_info = storage_client.upload_fileobj(
                    object_name,
                    video_file,
                    file_size,
                    "video/mp4",
                )
            if storage_info.get("storage") not in {"local", "minio"}:
                raise RuntimeError(f"Video storage backend unavailable: {storage_info.get('reason') or 'unknown'}")
            video_asset = create_asset(
                db,
                AssetCreate(
                    project_id=project_id,
                    asset_type="video",
                    file_name=f"embedded-video-{index + 1}{video_path.suffix.lower() or '.mp4'}",
                    file_path=storage_info["object_name"],
                    mime_type="video/mp4",
                    duration_sec=int(download_info.get("duration_sec") or 0) or None,
                    metadata={
                        "source_url": source_url,
                        "media_url": str(video_url),
                        "storage": storage_info.get("storage"),
                    },
                ),
            )
            video_asset_ids = dict(config.get("page_video_asset_ids") or {})
            video_asset_ids[key] = video_asset.id
            config["page_video_asset_ids"] = video_asset_ids
            asset_ids.append(video_asset.id)
            config["source_asset_ids"] = asset_ids
            task.input_config = config
            db.commit()

        asset_ids = list(dict.fromkeys(str(asset_id) for asset_id in config.get("source_asset_ids", []) if asset_id))
        if not asset_ids:
            raise PublicPageError("Public page extraction produced no reusable content assets")

        child_id = str(config.get("library_task_id") or "")
        child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first() if child_id else None
        if child is None or child.status in {"failed", "dispatch_failed", "canceled"}:
            library_id = str(config.get("library_id") or uuid4())
            child_config = {
                "library_id": library_id,
                "library_title": str(page_info.get("title") or "Public page"),
                "asset_ids": asset_ids,
                "max_parallel": 2,
                "output_formats": config.get("output_formats") or ["markdown", "html"],
                "markdown_enhance_with_ollama": False,
                "source_url": source_url,
                "source_kind": str(config.get("source_kind") or "public_page"),
            }
            child = create_task(
                db,
                TaskCreate(
                    project_id=project_id,
                    task_type="mixed_assets_to_markdown",
                    input_config=child_config,
                ),
            )
            config["library_id"] = library_id
            config["library_task_id"] = child.id
            task.input_config = config
            db.commit()

        if child.status != "success":
            _safe_update(db, task_id, "running", 30, result_summary="organizing page text and media")
            mixed_assets_to_markdown_job.run({
                "task_id": child.id,
                "project_id": project_id,
                "input_config": child.input_config or {},
            })
            db.expire_all()
            child = db.query(ProcessingTask).filter(ProcessingTask.id == child.id).first()
        if child is None or child.status != "success":
            child_error = child.error_message if child is not None else "child library task not found"
            raise RuntimeError(f"Page content processing failed: {child_error}")

        db.expire_all()
        child = db.query(ProcessingTask).filter(ProcessingTask.id == child.id).first()
        child_config = dict(child.input_config or {})
        artifact_id = str(child_config.get("library_artifact_id") or "")
        bundle_path = str(child_config.get("library_index_path") or "")
        text_asset = db.query(Asset).filter(Asset.id == str(config["source_text_asset_id"])).first()
        image_assets = [
            db.query(Asset).filter(Asset.id == str(asset_id)).first()
            for _, asset_id in sorted(
                (config.get("page_image_asset_ids") or {}).items(),
                key=lambda entry: int(entry[0]),
            )
        ]
        image_assets = [asset for asset in image_assets if asset is not None]
        if text_asset is None:
            raise RuntimeError("Public page source text asset was not preserved")
        bundle_info = _attach_public_page_source_to_bundle(
            db,
            task_id,
            project_id,
            bundle_path,
            source_url,
            {
                **page_info,
                "artifact_id": artifact_id,
                "output_formats": config.get("output_formats") or ["markdown", "html"],
            },
            text_asset,
            image_assets,
        )
        config["library_artifact_id"] = artifact_id
        config["library_index_path"] = bundle_path
        merge_video_task_checkpoint(config, "public_page_export", {
            "status": "success",
            "path": bundle_info["archive_path"] or bundle_path,
            "artifact_id": artifact_id,
            "source_markdown": bundle_info["markdown_path"],
            "image_count": bundle_info["image_count"],
        })
        task.input_config = config
        db.commit()
        image_count = bundle_info["image_count"]
        text_length = len(str(page_info.get("text") or ""))
        _safe_update(
            db,
            task_id,
            "success",
            100,
            result_summary=(
                f"Public page ready: {text_length} text chars, {image_count} image(s), "
                f"artifact {artifact_id}"
            ),
        )
        return {
            "status": "public_page_ready",
            "path": bundle_path,
            "artifact_id": artifact_id,
            "payload": payload,
        }
    except Exception as exc:
        _safe_update(db, task_id, "failed", 100, error_message=str(exc))
        return {"status": "failed", "error": str(exc), "payload": payload}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def video_batch_to_markdown_job(self, payload: dict):
    db = SessionLocal()
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    try:
        task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
        if task is None:
            raise RuntimeError(f"Video library task not found: {task_id}")
        config = dict(task.input_config or {})
        library_id = str(config.get("library_id") or "").strip()
        items = config.get("video_items")
        if not library_id or not isinstance(items, list) or not items:
            raise RuntimeError("Video library id or selected video list is missing")
        items = items[:VIDEO_BATCH_MAX_ITEMS]
        config["video_items"] = items
        child_specs = []
        external_active = []

        for index, item in enumerate(items):
            item = dict(item)
            child_id = str(item.get("child_task_id") or "")
            child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first() if child_id else None
            if child is not None and child.status == "success":
                item["status"] = "success"
                child_specs.append((index, item, child.id, None))
                continue
            if child is not None and child.status in {"pending", "running"}:
                item["status"] = child.status
                child_specs.append((index, item, child.id, None))
                external_active.append((index, child.id))
                continue

            source_url = str(item.get("source_url") or "").strip()
            source_asset_id = str(item.get("asset_id") or "").strip()
            if not source_url and not source_asset_id:
                item["status"] = "failed"
                item["error"] = "Video source URL or asset id is missing"
                child_specs.append((index, item, "", None))
                continue
            child_config = {
                **({"source_asset_id": source_asset_id} if source_asset_id else {"source_url": source_url}),
                "title": str(item.get("title") or ""),
                "auth": config.get("auth") or {"mode": "auto"},
                "library_id": library_id,
                "library_title": str(config.get("library_title") or "视频知识库"),
            }
            child = create_task(
                db,
                TaskCreate(
                    project_id=project_id,
                    asset_id=source_asset_id or None,
                    task_type="video_to_markdown",
                    input_config=child_config,
                ),
            )
            item["child_task_id"] = child.id
            item["status"] = "pending"
            item.pop("error", None)
            child_specs.append((index, item, child.id, {
                "task_id": child.id,
                "project_id": project_id,
                "asset_id": source_asset_id or None,
                "input_config": child_config,
            }))
            config["video_items"][index] = item

        task.input_config = config
        db.commit()
        _safe_update(db, task_id, "running", 5, result_summary=f"batch video processing started: {len(items)} video(s)")

        parallel_limit = min(
            max(int(config.get("max_parallel") or VIDEO_BATCH_MAX_PARALLEL), 1),
            VIDEO_BATCH_MAX_PARALLEL,
            len(items),
        )
        future_map = {}
        completed_count = sum(1 for _, item, _, child_payload in child_specs if child_payload is None and item.get("status") == "success")
        with ThreadPoolExecutor(max_workers=parallel_limit, thread_name_prefix="envoy-video-batch") as executor:
            for index, item, child_id, child_payload in child_specs:
                if child_payload is not None:
                    future_map[executor.submit(video_to_markdown_job.run, child_payload)] = (index, child_id)

            for future in as_completed(future_map):
                index, child_id = future_map[future]
                try:
                    future.result()
                except Exception as exc:
                    child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
                    if child is not None and child.status not in {"success"}:
                        child.status = "failed"
                        child.error_message = str(exc)
                        db.commit()
                child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
                item = dict(config["video_items"][index])
                item["status"] = child.status if child is not None else "failed"
                if child is not None and child.error_message:
                    item["error"] = child.error_message
                config["video_items"][index] = item
                task.input_config = config
                db.commit()
                completed_count += 1
                progress = min(90, 5 + int(80 * completed_count / max(len(items), 1)))
                _safe_update(db, task_id, "running", progress, result_summary=f"batch video processing: {completed_count}/{len(items)} complete")

        active_ids = {child_id for _, child_id in external_active}
        while active_ids:
            db.expire_all()
            for child_id in list(active_ids):
                child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
                if child is None or child.status not in {"pending", "running"}:
                    active_ids.discard(child_id)
            if active_ids:
                _safe_update(db, task_id, "running", 80, result_summary=f"waiting for existing child task(s): {len(active_ids)}")
                time.sleep(2)

        all_tasks = db.query(ProcessingTask).filter(ProcessingTask.project_id == project_id).all()
        library_batch_tasks = [
            candidate
            for candidate in all_tasks
            if candidate.task_type == "video_batch_to_markdown"
            and isinstance(candidate.input_config, dict)
            and candidate.input_config.get("library_id") == library_id
        ]
        child_ids = set(str(child_id) for child_id in config.get("seed_task_ids", []) if child_id)
        for batch_task in library_batch_tasks:
            batch_config = batch_task.input_config if isinstance(batch_task.input_config, dict) else {}
            for batch_item in batch_config.get("video_items", []):
                if isinstance(batch_item, dict) and batch_item.get("child_task_id"):
                    child_ids.add(str(batch_item["child_task_id"]))

        videos = []
        failed_videos = []
        evidence_rows = []
        for child_id in sorted(child_ids):
            child = db.query(ProcessingTask).filter(ProcessingTask.id == child_id).first()
            if child is None:
                failed_videos.append({"video_id": child_id, "title": child_id, "error": "Child task not found"})
                continue
            child_config = child.input_config if isinstance(child.input_config, dict) else {}
            source_url = str(child_config.get("source_url") or "")
            title = str(child_config.get("title") or source_url or child.id)
            if child.status != "success":
                failed_videos.append({
                    "video_id": child.id,
                    "title": title,
                    "source_url": source_url,
                    "error": child.error_message or f"Child task ended with status {child.status}",
                })
                continue
            asset = db.query(Asset).filter(Asset.id == child.asset_id).first() if child.asset_id else None
            title = str(child_config.get("title") or (asset.file_name if asset else source_url) or child.id)
            units = (
                db.query(SemanticUnit)
                .filter(SemanticUnit.asset_id == child.asset_id)
                .order_by(SemanticUnit.created_at.asc())
                .all()
                if child.asset_id else []
            )
            units = [
                unit for unit in units
                if isinstance(unit.labels, dict)
                and unit.labels.get("stage") in {"preprocess", "understand"}
                and unit.labels.get("checkpoint") != "segment_transcript"
            ]
            rows = _video_export_rows(units)
            video_evidence = []
            for row in rows:
                labels = dict(row.get("labels") or {})
                labels.update({
                    "video_id": child.id,
                    "video_title": title,
                    "video_url": source_url,
                })
                row["labels"] = labels
                evidence_rows.append(row)
                video_evidence.append({
                    "id": row["id"],
                    "kind": row.get("unit_type") or "knowledge",
                    "content": row.get("content") or "",
                })
            videos.append({
                "video_id": child.id,
                "title": title,
                "source_url": source_url,
                "status": child.status,
                "evidence_count": len(video_evidence),
                "evidence": video_evidence,
            })

        _safe_update(db, task_id, "running", 92, result_summary="building theme-first wiki index")
        previous_manifest = {}
        previous_manifest_path = (
            Path(EXPORT_DIR)
            / "video-libraries"
            / _video_library_slug(library_id)
            / "manifest.json"
        )
        if previous_manifest_path.is_file():
            try:
                loaded_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded_manifest, dict):
                    previous_manifest = loaded_manifest
            except (OSError, json.JSONDecodeError):
                previous_manifest = {}
        previous_evidence_ids = {
            str(item.get("id"))
            for item in previous_manifest.get("evidence", [])
            if isinstance(item, dict) and item.get("id")
        }
        new_videos = [
            {
                **video,
                "evidence": [
                    evidence for evidence in video.get("evidence", [])
                    if str(evidence.get("id") or "") not in previous_evidence_ids
                ],
            }
            for video in videos
        ]
        new_videos = [video for video in new_videos if video.get("evidence")]
        topic_sources = new_videos if previous_manifest and new_videos else videos
        topic_result = synthesize_video_topics(
            topic_sources,
            topic_hints=[
                str(topic.get("title") or "")
                for topic in previous_manifest.get("topics", [])
                if isinstance(topic, dict) and topic.get("title")
            ],
        )
        if previous_manifest and new_videos:
            topic_result["incremental"] = True
            topic_result["new_video_ids"] = [str(video.get("video_id")) for video in new_videos]
        library_result = export_video_library(
            evidence_rows=evidence_rows,
            video_rows=videos,
            out_dir=str(EXPORT_DIR),
            library_id=library_id,
            title=str(config.get("library_title") or "视频知识库"),
            task_id=task_id,
            project_id=project_id,
            topic_result=topic_result,
            failed_videos=failed_videos,
            db=db,
            output_formats=config.get("output_formats"),
        )
        artifact = create_artifact(
            db,
            ArtifactCreate(
                project_id=project_id,
                artifact_type="video_library",
                name=Path(library_result.get("archive_path") or library_result["path"]).name,
                file_path=library_result.get("archive_path") or library_result["path"],
                version="v1",
            ),
        )
        _ensure_trace_link(db, "processing_task", task_id, "artifact", artifact.id, "exported_to")
        config["library_artifact_id"] = artifact.id
        config["library_index_path"] = library_result["path"]
        config["failed_video_count"] = len(failed_videos)
        merge_video_task_checkpoint(config, "collection_export", {
            "status": "success",
            "path": library_result["path"],
            "manifest_path": library_result["manifest_path"],
            "artifact_id": artifact.id,
            "evidence_count": len(evidence_rows),
            "topic_status": topic_result.get("status"),
        })
        task.input_config = config
        db.commit()
        if failed_videos:
            error = f"{len(failed_videos)} video(s) failed; successful sources are preserved in the library. Retry to continue failed videos."
            _safe_update(db, task_id, "failed", 100, result_summary=f"video library updated: {len(videos)} succeeded, {len(failed_videos)} failed", error_message=error)
            return {"status": "partial", "path": library_result["path"], "artifact_id": artifact.id, "payload": payload}
        _safe_update(db, task_id, "success", 100, result_summary=f"video library ready: {len(videos)} video(s), {len(evidence_rows)} evidence item(s)")
        return {"status": "video_library_ready", "path": library_result["path"], "artifact_id": artifact.id, "payload": payload}
    except Exception as exc:
        _safe_update(db, task_id, "failed", 100, error_message=str(exc))
        return {"status": "failed", "error": str(exc), "payload": payload}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def preprocess_job(self, payload: dict):
    db = SessionLocal()
    media_temp_dirs = []
    try:
        task_id = payload.get("task_id")
        asset_id = payload.get("asset_id")
        project_id = payload.get("project_id")
        _safe_update(db, task_id, "running", 10)
        task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first() if task_id else None
        asset = db.query(Asset).filter(Asset.id == asset_id).first() if asset_id else None
        source_path = _resolve_source_path(payload, asset)
        has_previous_preprocess = _has_previous_preprocess_units(db, asset_id, task_id)
        if _is_text_asset(asset):
            content = storage_client.read_text(asset.file_path)
            chunks = _text_chunks(content)
            chunk_count = len(chunks)
            semantic_payload = {
                "char_count": len(content),
                "preview": content[:500],
                "stage": "preprocess",
                "source": "text_asset",
                "chunk_count": chunk_count,
            }
            units = []
            for index, chunk in enumerate(chunks):
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
                title = asset.file_name if chunk_count == 1 else f"{asset.file_name} chunk {index + 1}/{chunk_count}"
                labels = {
                    "stage": "preprocess",
                    "source": "text_asset",
                    "task_id": task_id,
                    "chunk_index": index,
                    "chunk_count": chunk_count,
                    "char_start": chunk["start"],
                    "char_end": chunk["end"],
                }
                unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="text", title=title, content=chunk["content"], labels=labels, confidence=0.92)
                db.add(unit)
                db.commit()
                db.refresh(unit)
                units.append(unit)
                db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=unit.id, relation_type="derived_from"))
                db.commit()
                progress = min(95, 15 + int(((index + 1) / max(chunk_count, 1)) * 75))
                _safe_update(db, task_id, "running", progress, result_summary=f"text chunks persisted: {index + 1}/{chunk_count}")
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
            _successful_preprocess_replaces_previous(db, asset_id, task_id)
            _safe_update(db, task_id, "success", 100, result_summary=f"text preprocess done: {semantic_payload['char_count']} chars, {chunk_count} chunks")
            return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
        if _local_source_file_missing(source_path):
            message = f"Source file not found: {source_path or '(empty)'}"
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message}
        if _is_document_asset(asset) or _is_image_asset(asset):
            unit_type = "image" if _is_image_asset(asset) else "document"
            source = f"{unit_type}_asset"
            if unit_type == "document":
                lower_name = asset.file_name.lower() if asset else ""
                if asset.mime_type == "application/pdf" or lower_name.endswith(".pdf"):
                    pdf_page_ocr_temp_dir = str((Path(tempfile.gettempdir()) / "envoy-pdf-page-ocr" / (task_id or str(uuid4()))))
                    page_semantics = _extract_pdf_page_semantics(source_path, pdf_page_ocr_temp_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
                    page_texts = [str(item.get("text") or "").strip() for item in page_semantics if str(item.get("text") or "").strip()]
                    used_pdf_ocr = any(item.get("mode") == "ocr" for item in page_semantics)
                    extracted_text = "\n\n".join(page_texts).strip()
                else:
                    page_semantics = []
                    used_pdf_ocr = False
                    extracted_text = _extract_ooxml_text(source_path)
                if extracted_text:
                    if asset.mime_type == "application/pdf" or lower_name.endswith(".pdf"):
                        pdf_chunking_config = _pdf_input_config(payload, task)
                        pdf_chunks, pdf_effective_strategy = _split_pdf_pages_by_strategy(page_semantics, pdf_chunking_config)
                        chunk_count = max(len(pdf_chunks), 1)
                    else:
                        pdf_chunking_config = {}
                        pdf_effective_strategy = ""
                        pdf_chunks = []
                        chunk_count = len(_text_chunks(extracted_text))
                    if _task_is_canceled(db, task_id):
                        return _canceled_result(payload)
                    if asset.mime_type == "application/pdf" or lower_name.endswith(".pdf"):
                        units = []
                        total_pages = len(page_semantics)
                        total_chunks = len(pdf_chunks)
                        if used_pdf_ocr:
                            _safe_update(db, task_id, "running", 15, result_summary=f"pdf page ocr running: {total_pages} page(s)")
                        for index, chunk_info in enumerate(pdf_chunks):
                            if _task_is_canceled(db, task_id):
                                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
                            chunk_text = str(chunk_info.get("content") or "").strip()
                            if not chunk_text.strip():
                                continue
                            page_range = chunk_info.get("page_range") or [index + 1, index + 1]
                            page_source = str(chunk_info.get("source") or "document_text")
                            title_prefix = "document OCR chunk" if page_source == "document_ocr" else "document chunk"
                            labels = {
                                "stage": "preprocess",
                                "source": page_source,
                                "task_id": task_id,
                                "source_name": asset.file_name if asset else "uploaded document",
                                "page_range": page_range,
                                "section_path": chunk_info.get("section_path") or [str(chunk_info.get("title") or f"第 {page_range[0]}-{page_range[1]} 页")],
                                "pdf_chunking_strategy": str(pdf_chunking_config.get("strategy") or "auto"),
                                "pdf_effective_chunking_strategy": pdf_effective_strategy,
                                "chunk_index": index,
                                "chunk_count": total_chunks,
                                "page_count": int(chunk_info.get("page_count") or 1),
                                "extraction_status": "text_extracted",
                                "extraction_mode": "ocr" if page_source == "document_ocr" else "text",
                            }
                            if chunk_info.get("ocr_pages"):
                                labels["ocr_pages"] = chunk_info.get("ocr_pages")
                                labels["reading_order"] = "top_left"
                            title = f"{asset.file_name if asset else 'uploaded document'} {title_prefix} {index + 1}/{total_chunks}: {chunk_info.get('title')}"
                            unit = _persist_single_semantic_unit(db, project_id, asset_id, "document", title, chunk_text, labels, 0.88)
                            units.append(unit)
                            progress = min(95, 15 + int(((index + 1) / max(total_chunks, 1)) * 75))
                            if page_source == "document_ocr":
                                _safe_update(db, task_id, "running", progress, result_summary=f"pdf page ocr done: {page_range[1]}/{total_pages} page(s); pdf chunks persisted: {index + 1}/{total_chunks}")
                            else:
                                _safe_update(db, task_id, "running", progress, result_summary=f"document chunks persisted: {index + 1}/{total_chunks}")
                    else:
                        units = _persist_text_semantic_units(
                            db,
                            project_id,
                            asset_id,
                            asset.file_name if asset else "uploaded document",
                            "document",
                            extracted_text,
                            "document_text",
                            0.88,
                            task_id=task_id,
                            progress_label="document chunks persisted",
                        )
                    semantic_payload = {
                        "char_count": len(extracted_text),
                        "preview": extracted_text[:500],
                        "stage": "preprocess",
                        "source": "document_ocr" if used_pdf_ocr else "document_text",
                        "chunk_count": chunk_count,
                        "extraction_status": "text_extracted",
                        "used_pdf_ocr": used_pdf_ocr,
                    }
                    if asset.mime_type == "application/pdf" or lower_name.endswith(".pdf"):
                        semantic_payload["pdf_chunking_strategy"] = str(pdf_chunking_config.get("strategy") or "auto")
                        semantic_payload["pdf_effective_chunking_strategy"] = pdf_effective_strategy
                        semantic_payload["page_count"] = len(page_semantics)
                    if _task_is_canceled(db, task_id):
                        return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
                    _successful_preprocess_replaces_previous(db, asset_id, task_id)
                    _safe_update(db, task_id, "success", 100, result_summary=f"document preprocess done: {semantic_payload['char_count']} chars, {len(units)} chunks")
                    return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
            if unit_type == "image":
                if _task_is_canceled(db, task_id):
                    return _canceled_result(payload)
                ocr_results = _run_ocr_for_task(db, task_id, [source_path])
                if _task_is_canceled(db, task_id):
                    return _canceled_result(payload)
                extracted_text = "\n".join(str(item.get("text", "")).strip() for item in ocr_results if isinstance(item, dict) and str(item.get("text", "")).strip()).strip()
                if extracted_text:
                    confidence_values = [
                        float(item.get("confidence", 0.0))
                        for item in ocr_results
                        if isinstance(item, dict) and isinstance(item.get("confidence", 0.0), (int, float))
                    ]
                    confidence = max(confidence_values) if confidence_values else 0.78
                    chunk_count = len(_text_chunks(extracted_text))
                    if _task_is_canceled(db, task_id):
                        return _canceled_result(payload)
                    units = _persist_text_semantic_units(db, project_id, asset_id, asset.file_name if asset else "uploaded image", "image", extracted_text, "image_text", confidence, task_id=task_id, progress_label="image chunks persisted")
                    semantic_payload = {
                        "char_count": len(extracted_text),
                        "preview": extracted_text[:500],
                        "stage": "preprocess",
                        "source": "image_text",
                        "chunk_count": chunk_count,
                        "ocr_result_count": len(ocr_results),
                        "extraction_status": "text_extracted",
                    }
                    if _task_is_canceled(db, task_id):
                        return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
                    _successful_preprocess_replaces_previous(db, asset_id, task_id)
                    _safe_update(db, task_id, "success", 100, result_summary=f"image OCR preprocess done: {semantic_payload['char_count']} chars, {len(units)} chunks")
                    return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
            semantic_payload = _build_file_semantic_payload(asset, source_path, source)
            labels = {
                "stage": "preprocess",
                "source": source,
                "mime_type": semantic_payload["mime_type"],
                "asset_type": semantic_payload["asset_type"],
                "size_bytes": semantic_payload["size_bytes"],
                "extraction_status": semantic_payload["extraction_status"],
                "task_id": task_id,
            }
            _persist_single_semantic_unit(
                db,
                project_id,
                asset_id,
                unit_type,
                asset.file_name if asset else "uploaded file",
                json.dumps(semantic_payload, ensure_ascii=False),
                labels,
                0.7,
            )
            _successful_preprocess_replaces_previous(db, asset_id, task_id)
            _safe_update(db, task_id, "success", 100, result_summary=f"{unit_type} preprocess done: metadata captured")
            return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
        if _is_audio_asset(asset):
            if _task_is_canceled(db, task_id):
                return _canceled_result(payload)
            audio_dir = _audio_task_temp_dir(task_id)
            media_temp_dirs.append(audio_dir)
            _safe_update(db, task_id, "running", 20, result_summary="audio split running")
            audio_segment_paths = _split_audio_for_task(db, task_id, source_path, str(audio_dir))
            _safe_update(db, task_id, "running", 35, result_summary=f"audio split done: {len(audio_segment_paths)} segment(s)")
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
            transcription_parts = []
            transcription_segments = []
            transcription_results = []
            transcription_inputs, segments_truncated, segment_input_count = _limit_media_segments(audio_segment_paths, source_path)
            transcription_count = len(transcription_inputs)
            for index, segment_path in enumerate(transcription_inputs):
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
                running_progress = min(74, 35 + int((index / max(transcription_count, 1)) * 40))
                _safe_update(db, task_id, "running", running_progress, result_summary=f"audio transcription running: {index + 1}/{transcription_count} segment(s)")
                segment_transcription = _transcribe_audio_for_task(db, task_id, segment_path)
                transcription_results.append({"segment": segment_path, "transcript": segment_transcription})
                if isinstance(segment_transcription, dict):
                    text = str(segment_transcription.get("text", "")).strip()
                    if text:
                        transcription_parts.append(text)
                        _upsert_media_transcript_checkpoint(
                            db,
                            project_id,
                            asset_id,
                            task_id,
                            transcription_parts,
                            index,
                            transcription_count,
                            source="audio_transcript",
                            title_prefix="audio transcript",
                        )
                    for item in segment_transcription.get("segments", []) if isinstance(segment_transcription.get("segments", []), list) else []:
                        if isinstance(item, dict):
                            enriched = dict(item)
                            enriched["source_segment"] = segment_path
                            enriched["segment_index"] = index
                            transcription_segments.append(enriched)
                progress = min(75, 35 + int(((index + 1) / max(transcription_count, 1)) * 40))
                _safe_update(db, task_id, "running", progress, result_summary=f"audio transcription done: {index + 1}/{transcription_count} segment(s)")
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
            transcription = {
                "text": "\n".join(transcription_parts).strip(),
                "segments": transcription_segments,
                "segment_results": transcription_results,
            }
            transcript_text = str(transcription.get("text", "") if isinstance(transcription, dict) else "").strip()
            failed_transcriptions = [
                item["transcript"]
                for item in transcription_results
                if isinstance(item.get("transcript"), dict) and item["transcript"].get("status") == "failed"
            ]
            transcription_summary = _transcription_segment_summary(transcription_results)
            if not transcript_text and failed_transcriptions and len(failed_transcriptions) == len(transcription_results):
                error = next((str(item.get("error")) for item in failed_transcriptions if item.get("error")), "unknown transcription error")
                message = f"audio transcription failed: {error}"
                _safe_update(db, task_id, "failed", 100, error_message=message)
                return {"status": "failed", "payload": payload, "error": message, "semantic": {"transcript": transcription, "extraction_status": "failed", **_media_limit_metadata()}}
            if transcript_text:
                segments = transcription.get("segments", []) if isinstance(transcription, dict) else []
                chunk_count = len(_text_chunks(transcript_text))
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
                _clear_media_transcript_checkpoints(db, asset_id, task_id, source="audio_transcript")
                units = _persist_text_semantic_units(db, project_id, asset_id, asset.file_name if asset else "uploaded audio", "audio", transcript_text, "audio_transcript", 0.86, task_id=task_id, progress_label="audio chunks persisted")
                ocr_text = ""
                degraded_capabilities = _media_degraded_capabilities(transcription_summary, segments_truncated, transcript_text, ocr_text)
                semantic_payload = {
                    "segments": transcription_inputs,
                    "segment_source_count": segment_input_count,
                    "segment_truncated": segments_truncated,
                    "char_count": len(transcript_text),
                    "preview": transcript_text[:500],
                    "stage": "preprocess",
                    "source": "audio_transcript",
                    "chunk_count": chunk_count,
                    "segment_count": transcription_count,
                    "transcript": transcription,
                    "transcript_segment_count": len(segments) if isinstance(segments, list) else 0,
                    "extraction_status": "partial_text_extracted" if transcription_summary["failed_count"] else "text_extracted",
                    "degraded_capabilities": degraded_capabilities,
                    "usable_text_sources": ["transcript"],
                    "transcription_success_count": transcription_summary["success_count"],
                    "transcription_failed_count": transcription_summary["failed_count"],
                    "transcription_failed_segments": transcription_summary["failed_segments"],
                    **_media_limit_metadata(),
                }
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
                _successful_preprocess_replaces_previous(db, asset_id, task_id)
                _safe_update(db, task_id, "success", 100, result_summary=f"audio preprocess done: {semantic_payload['char_count']} chars, {len(units)} chunks{_partial_transcription_suffix(transcription_summary)}{_segment_truncation_suffix(segment_input_count, transcription_count)}")
                return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
            semantic_payload = _build_file_semantic_payload(asset, source_path, "audio_asset")
            semantic_payload.update(_media_limit_metadata())
            semantic_payload.update({
                "segments": transcription_inputs,
                "segment_source_count": segment_input_count,
                "segment_count": transcription_count,
                "segment_truncated": segments_truncated,
            })
            labels = {
                "stage": "preprocess",
                "source": "audio_asset",
                "mime_type": semantic_payload["mime_type"],
                "asset_type": semantic_payload["asset_type"],
                "size_bytes": semantic_payload["size_bytes"],
                "extraction_status": semantic_payload["extraction_status"],
                "task_id": task_id,
            }
            _persist_single_semantic_unit(
                db,
                project_id,
                asset_id,
                "audio",
                asset.file_name if asset else "uploaded audio",
                json.dumps(semantic_payload, ensure_ascii=False),
                labels,
                0.7,
            )
            _successful_preprocess_replaces_previous(db, asset_id, task_id)
            _safe_update(db, task_id, "success", 100, result_summary="audio preprocess done: metadata captured")
            return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
        segment_dir, frame_dir = _media_task_temp_dirs(task_id)
        audio_dir = _audio_task_temp_dir(task_id)
        media_temp_dirs.extend([segment_dir, frame_dir, audio_dir])
        _safe_update(db, task_id, "running", 20, result_summary="media split running")
        segment_paths = _split_video_for_task(db, task_id, source_path, str(segment_dir))
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        _safe_update(db, task_id, "running", 35, result_summary=f"media split done: {len(segment_paths)} segment(s)")
        audio_extraction_error = None
        try:
            _safe_update(db, task_id, "running", 36, result_summary="audio extraction running")
            audio_segment_paths = _split_audio_for_task(db, task_id, source_path, str(audio_dir))
            _safe_update(db, task_id, "running", 38, result_summary=f"audio extraction done: {len(audio_segment_paths)} segment(s)")
        except MediaProcessingError as exc:
            audio_segment_paths = []
            audio_extraction_error = _compact_error_message(exc)
            _safe_update(db, task_id, "running", 38, result_summary=f"audio extraction skipped: {audio_extraction_error}")
        frame_extraction_error = None
        if MEDIA_ENABLE_FRAME_EXTRACTION:
            _safe_update(db, task_id, "running", 40, result_summary="frame extraction running")
            frame_config = _video_frame_config(payload, task)
            try:
                frame_paths = _extract_frames_for_task_compat(db, task_id, source_path, str(frame_dir), frame_config)
                frame_extraction_skipped = False
                _safe_update(db, task_id, "running", 55, result_summary=f"frame extraction done: {len(frame_paths)} frame pattern(s)")
            except MediaProcessingError as exc:
                frame_paths = []
                frame_extraction_skipped = True
                frame_extraction_error = _compact_error_message(exc)
                _safe_update(db, task_id, "running", 55, result_summary=f"frame extraction skipped: {frame_extraction_error}")
        else:
            frame_config = _video_frame_config(payload, task)
            frame_paths = []
            frame_extraction_skipped = True
            _safe_update(db, task_id, "running", 55, result_summary="frame extraction skipped: disabled")
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        transcription_parts = []
        transcription_segments = []
        transcription_results = []
        # Preserve the legacy ffmpeg fallback for the original video splitter,
        # while keeping explicitly reported audio-extraction failures fail-closed.
        legacy_video_fallback = (
            not audio_segment_paths
            and bool(audio_extraction_error)
            and getattr(_split_video_for_task, "__module__", None) == __name__
        )
        transcription_source_paths = audio_segment_paths or (segment_paths if legacy_video_fallback else [])
        transcription_inputs, segments_truncated, segment_input_count = _limit_media_segments(
            transcription_source_paths,
            source_path,
            allow_source_fallback=legacy_video_fallback,
        )
        transcription_count = len(transcription_inputs)
        for index, segment_path in enumerate(transcription_inputs):
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
            running_progress = min(74, 55 + int((index / max(transcription_count, 1)) * 20))
            _safe_update(db, task_id, "running", running_progress, result_summary=f"audio transcription running: {index + 1}/{transcription_count} segment(s)")
            segment_transcription = _transcribe_audio_for_task(db, task_id, segment_path)
            transcription_results.append({"segment": segment_path, "transcript": segment_transcription})
            if isinstance(segment_transcription, dict):
                text = str(segment_transcription.get("text", "")).strip()
                if text:
                    transcription_parts.append(text)
                    _upsert_media_transcript_checkpoint(
                        db,
                        project_id,
                        asset_id,
                        task_id,
                        transcription_parts,
                        index,
                        transcription_count,
                    )
                for item in segment_transcription.get("segments", []) if isinstance(segment_transcription.get("segments", []), list) else []:
                    if isinstance(item, dict):
                        enriched = dict(item)
                        enriched["source_segment"] = segment_path
                        enriched["segment_index"] = index
                        enriched["start_sec"] = index * MEDIA_SEGMENT_SECONDS + float(item.get("start") or 0)
                        enriched["end_sec"] = index * MEDIA_SEGMENT_SECONDS + float(item.get("end") or item.get("start") or 0)
                        transcription_segments.append(enriched)
            progress = min(75, 55 + int(((index + 1) / max(transcription_count, 1)) * 20))
            _safe_update(db, task_id, "running", progress, result_summary=f"audio transcription done: {index + 1}/{transcription_count} segment(s)")
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        transcription = {
            "text": "\n".join(transcription_parts).strip(),
            "segments": transcription_segments,
            "segment_results": transcription_results,
        }
        if frame_extraction_skipped:
            ocr_results = []
            ocr_skipped = True
            _safe_update(db, task_id, "running", 90, result_summary="ocr skipped: frame extraction disabled")
        elif MEDIA_ENABLE_OCR:
            _safe_update(db, task_id, "running", 88, result_summary=f"ocr running: {len(frame_paths)} frame(s)")
            ocr_results = compress_consecutive_ocr_results(_run_ocr_for_task(db, task_id, frame_paths))
            ocr_skipped = False
            _safe_update(db, task_id, "running", 90, result_summary=f"ocr done: {len(ocr_results)} frame result(s)")
        else:
            ocr_results = []
            ocr_skipped = True
            _safe_update(db, task_id, "running", 90, result_summary="ocr skipped: disabled")
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        transcript_text = str(transcription.get("text", "") if isinstance(transcription, dict) else "").strip()
        ocr_text = "\n".join(str(item.get("text", "")).strip() for item in ocr_results if isinstance(item, dict) and str(item.get("text", "")).strip()).strip()
        transcription_summary = _transcription_segment_summary(transcription_results)
        degraded_capabilities = _media_degraded_capabilities(transcription_summary, segments_truncated, transcript_text, ocr_text)
        usable_text_sources = []
        if transcript_text:
            usable_text_sources.append("transcript")
        if ocr_text:
            usable_text_sources.append("ocr")
        semantic_payload = {
            "segments": transcription_inputs,
            "video_segments": segment_paths,
            "audio_segments": audio_segment_paths,
            "segment_source_count": segment_input_count,
            "segment_count": transcription_count,
            "segment_truncated": segments_truncated,
            "frames": frame_paths,
            "frame_config": frame_config,
            "transcript": transcription,
            "ocr": ocr_results,
            "frame_extraction_skipped": frame_extraction_skipped,
            "frame_extraction_error": frame_extraction_error,
            "audio_extraction_error": audio_extraction_error,
            "ocr_skipped": ocr_skipped,
            "stage": "preprocess",
            "source_path": source_path,
            "degraded_capabilities": degraded_capabilities,
            "usable_text_sources": usable_text_sources,
            **_media_limit_metadata(),
            "target_frame_rate": frame_config["target_frame_rate"],
            "frame_interval_seconds": frame_config["frame_interval_seconds"],
            "max_frame_count": frame_config["max_frame_count"],
            "frame_sampling_mode": frame_config["mode"],
        }
        failed_transcriptions = [
            item["transcript"]
            for item in transcription_results
            if isinstance(item.get("transcript"), dict) and item["transcript"].get("status") == "failed"
        ]
        semantic_payload.update({
            "extraction_status": _media_extraction_status(transcription_summary, segments_truncated, transcript_text, ocr_text),
            "transcription_success_count": transcription_summary["success_count"],
            "transcription_failed_count": transcription_summary["failed_count"],
            "transcription_failed_segments": transcription_summary["failed_segments"],
        })
        if transcript_text:
            _clear_media_transcript_checkpoints(db, asset_id, task_id)
            _persist_text_semantic_units(
                db,
                project_id,
                asset_id,
                "media transcript",
                "transcript",
                transcript_text,
                "media_transcript",
                0.86,
                task_id=task_id,
                progress_label="media transcript chunks persisted",
                progress_start=75,
                progress_end=85,
            )
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
        if not transcript_text and not ocr_text and failed_transcriptions and len(failed_transcriptions) == len(transcription_results):
            error = next((str(item.get("error")) for item in failed_transcriptions if item.get("error")), "unknown transcription error")
            message = _compact_error_message(f"media transcription failed: {error}", max_chars=1500)
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message, "semantic": {**semantic_payload, "extraction_status": "failed"}}
        if not transcript_text and not ocr_text and (audio_extraction_error or frame_extraction_error):
            error_parts = [part for part in (audio_extraction_error, frame_extraction_error) if part]
            message = _compact_error_message(f"media text extraction unavailable: {'; '.join(error_parts)}", max_chars=1500)
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message, "semantic": {**semantic_payload, "extraction_status": "failed"}}
        if ocr_text:
            confidence_values = [
                float(item.get("confidence", 0.0))
                for item in ocr_results
                if isinstance(item, dict) and isinstance(item.get("confidence", 0.0), (int, float))
            ]
            _persist_text_semantic_units(
                db,
                project_id,
                asset_id,
                "media OCR",
                "ocr",
                ocr_text,
                "media_ocr",
                max(confidence_values) if confidence_values else 0.78,
                task_id=task_id,
                progress_label="media ocr chunks persisted",
                progress_start=90,
                progress_end=95,
            )
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
        summary_labels = {
            "stage": "preprocess",
            "source": "media_asset",
            "task_id": task_id,
            "extraction_status": semantic_payload.get("extraction_status"),
            "degraded_capabilities": semantic_payload.get("degraded_capabilities", []),
                "usable_text_sources": semantic_payload.get("usable_text_sources", []),
                "transcription_success_count": semantic_payload.get("transcription_success_count", 0),
                "transcription_failed_count": semantic_payload.get("transcription_failed_count", 0),
                "target_frame_rate": semantic_payload.get("target_frame_rate", 0),
                "frame_interval_seconds": semantic_payload.get("frame_interval_seconds"),
                "max_frame_count": semantic_payload.get("max_frame_count"),
                "frame_sampling_mode": semantic_payload.get("frame_sampling_mode"),
                **_media_limit_metadata(),
            }
        su = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="scene", title="preprocess summary", content=json.dumps(semantic_payload, ensure_ascii=False), labels=summary_labels, confidence=0.8)
        db.add(su)
        db.commit()
        db.refresh(su)
        db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=su.id, relation_type="derived_from"))
        db.commit()
        _successful_preprocess_replaces_previous(db, asset_id, task_id)
        _safe_update(
            db,
            task_id,
            "success",
            100,
            result_summary=f"media preprocess done: {transcription_count} segment(s), {len(frame_paths)} frame(s), max {semantic_payload['max_input_seconds']} sec{_partial_transcription_suffix(transcription_summary)}{_segment_truncation_suffix(segment_input_count, transcription_count)}",
        )
        return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
    except MediaProcessingError as exc:
        if str(exc).endswith(" canceled") and _task_is_canceled(db, payload.get("task_id")):
            return _canceled_preprocess_result(db, payload.get("asset_id"), payload.get("task_id"), has_previous_preprocess, payload)
        _clear_preprocess_semantic_units(db, payload.get("asset_id"), only_task_id=payload.get("task_id"))
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        return {"status": "failed", "payload": payload, "error": str(exc)}
    except Exception as exc:
        _clear_preprocess_semantic_units(db, payload.get("asset_id"), only_task_id=payload.get("task_id"))
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        if not (isinstance(payload.get("input_config"), dict) and payload["input_config"].get("preserve_media_temp_dirs")):
            _cleanup_media_task_temp_dirs(media_temp_dirs)
        db.close()


@celery_app.task(bind=True, max_retries=2)
def analyze_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        project_id = payload.get("project_id")
        asset_id = payload.get("asset_id")
        _safe_update(db, task_id, "running", 20)
        structured = build_analysis_payload(payload.get("input_config") or payload, payload.get("asset_type"))
        scene_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="scene", title="scene split", content=json.dumps(structured, ensure_ascii=False), labels={"stage": "analyze", "kind": "scene"}, confidence=0.78)
        rule_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="rule", title="rule extraction", content=json.dumps({"rules": structured["rules"]}, ensure_ascii=False), labels={"stage": "analyze", "kind": "rule"}, confidence=0.76)
        concept_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="concept", title="concept extraction", content=json.dumps({"concepts": structured["concepts"]}, ensure_ascii=False), labels={"stage": "analyze", "kind": "concept"}, confidence=0.75)
        db.add_all([scene_unit, rule_unit, concept_unit])
        db.commit()
        db.refresh(scene_unit)
        db.refresh(rule_unit)
        db.refresh(concept_unit)
        db.add(TraceLink(id=str(uuid4()), source_type="semantic_unit", source_id=scene_unit.id, target_type="semantic_unit", target_id=concept_unit.id, relation_type="supports"))
        db.add(TraceLink(id=str(uuid4()), source_type="semantic_unit", source_id=rule_unit.id, target_type="semantic_unit", target_id=concept_unit.id, relation_type="supports"))
        db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="analysis done")
        return {"status": "analyzed", "payload": payload}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def sample_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        project_id = payload.get("project_id")
        input_config = payload.get("input_config") or {}
        source_unit_id = payload.get("source_unit_id") or input_config.get("source_unit_id")
        _safe_update(db, task_id, "running", 30)
        if source_unit_id:
            unit = db.query(SemanticUnit).filter(SemanticUnit.id == source_unit_id, SemanticUnit.project_id == project_id).first()
            if unit is None:
                message = "Source semantic unit not found for project"
                _safe_update(db, task_id, "failed", 100, error_message=message)
                return {"status": "failed", "payload": payload, "error": message}
            source_task_id = unit.labels.get("task_id") if isinstance(unit.labels, dict) else None
            tasks_by_id = {}
            if source_task_id:
                source_task = db.query(ProcessingTask).filter(ProcessingTask.id == source_task_id, ProcessingTask.project_id == project_id).first()
                if source_task is not None:
                    tasks_by_id[source_task.id] = source_task
            if not _semantic_unit_is_sample_source(unit, tasks_by_id):
                message = "Source semantic unit is not exportable for sample generation"
                _safe_update(db, task_id, "failed", 100, error_message=message)
                return {"status": "failed", "payload": payload, "error": message}
            if not _semantic_unit_has_sample_content(unit):
                message = "Source semantic unit has no usable content for sample generation"
                _safe_update(db, task_id, "failed", 100, error_message=message)
                return {"status": "failed", "payload": payload, "error": message}
            sample = _persist_generated_sample(db, project_id, unit, payload)
            _safe_update(db, task_id, "success", 100, result_summary="1 sample generated")
            return {"status": "samples_generated", "payload": payload, "sample_id": sample.id, "sample_count": 1}

        batch_limit = max(int(input_config.get("limit") or SAMPLE_GENERATION_BATCH_LIMIT), 1)
        project_tasks = db.query(ProcessingTask).filter(ProcessingTask.project_id == project_id).all() if project_id else []
        tasks_by_id = {task.id: task for task in project_tasks}
        units = (
            db.query(SemanticUnit)
            .filter(SemanticUnit.project_id == project_id)
            .order_by(SemanticUnit.created_at.asc())
            .all()
            if project_id
            else []
        )
        units = [
            unit
            for unit in units
            if _semantic_unit_is_sample_source(unit, tasks_by_id)
            and _semantic_unit_has_sample_content(unit)
        ][:batch_limit]
        samples = [_persist_generated_sample(db, project_id, unit, payload) for unit in units]
        if not samples:
            message = "No semantic units available for sample generation"
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message}
        sample_count = len(samples)
        _safe_update(db, task_id, "success", 100, result_summary=f"{sample_count} sample{'s' if sample_count != 1 else ''} generated")
        return {"status": "samples_generated", "payload": payload, "sample_ids": [sample.id for sample in samples], "sample_count": sample_count}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def export_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        project_id = payload.get("project_id")
        _safe_update(db, task_id, "running", 50)
        out_dir = EXPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = out_dir / f"{task_id}.jsonl"
        md_path = out_dir / f"{task_id}.md"
        project_tasks = db.query(ProcessingTask).filter(ProcessingTask.project_id == project_id).all() if project_id else []
        tasks_by_id = {task.id: task for task in project_tasks}
        all_semantic_unit_models = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).order_by(SemanticUnit.created_at.asc()).all() if project_id else []
        semantic_unit_models = [
            unit
            for unit in all_semantic_unit_models
            if _semantic_unit_is_sample_source(unit, tasks_by_id)
            and _semantic_unit_has_export_content(unit)
        ]
        project_semantic_ids = {unit.id for unit in all_semantic_unit_models}
        exportable_semantic_ids = {unit.id for unit in semantic_unit_models}
        all_sample_models = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).order_by(GeneratedSample.created_at.asc()).all() if project_id else []
        sample_models = [
            sample
            for sample in all_sample_models
            if _generated_sample_is_export_source(sample, exportable_semantic_ids, project_semantic_ids)
            and _generated_sample_has_export_content(sample)
        ]
        if not semantic_unit_models and not sample_models:
            message = "No exportable rows found for project"
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message}
        semantic_unit_ids = {unit.id for unit in semantic_unit_models}
        sample_ids = {sample.id for sample in sample_models}
        trace_link_models = (
            db.query(TraceLink)
            .filter(
                TraceLink.source_id.in_(list(semantic_unit_ids | {task_id})),
                TraceLink.target_id.in_(list(sample_ids | semantic_unit_ids | {task_id})),
            )
            .order_by(TraceLink.created_at.asc())
            .all()
            if semantic_unit_ids or sample_ids or task_id
            else []
        )
        semantic_units = [_semantic_unit_export_row(unit) for unit in semantic_unit_models]
        samples = [_generated_sample_export_row(sample) for sample in sample_models]
        trace_links = [_trace_link_export_row(link) for link in trace_link_models]
        rows = semantic_units + samples
        build_dataset_jsonl(rows, str(jsonl_path))
        build_rich_export_report("Export Report", {"task_id": task_id, "project_id": project_id, "row_count": len(rows), "trace_link_count": len(trace_links)}, semantic_units, samples, trace_links, str(md_path))
        dataset_artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="dataset", name=jsonl_path.name, file_path=str(jsonl_path), version="v1"))
        report_artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="report", name=md_path.name, file_path=str(md_path), version="v1"))
        _ensure_trace_link(db, "processing_task", task_id, "artifact", dataset_artifact.id, "exported_to")
        _ensure_trace_link(db, "processing_task", task_id, "artifact", report_artifact.id, "exported_to")
        db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="export done")
        return {"status": "exported", "payload": payload, "row_count": len(rows), "paths": {"dataset": str(jsonl_path), "report": str(md_path)}}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def export_skill_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        project_id = payload.get("project_id")
        title = payload.get("title") or "Skill Package"
        _safe_update(db, task_id, "running", 50)
        out_dir = EXPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        export_result = export_skill_markdown(payload.get("payload") or [], str(out_dir), task_id, title)
        skill_paths = [Path(path) for path in (export_result.get("paths") or {"markdown": export_result["path"]}).values()]
        artifacts = [
            create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="skill", name=path.name, file_path=str(path), version="v1"))
            for path in skill_paths
        ]
        if payload.get("source_asset_id"):
            for artifact in artifacts:
                _ensure_trace_link(db, "asset", payload["source_asset_id"], "artifact", artifact.id, "exported_to")
            db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="skill exported")
        return {"status": "skill_exported", "path": export_result["path"], "paths": export_result.get("paths", {"markdown": export_result["path"]})}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def export_markdown_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        project_id = payload.get("project_id")
        title = payload.get("title") or "Video Markdown Report"
        _safe_update(db, task_id, "running", 50)
        out_dir = EXPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        export_result = export_markdown_from_video_summary(payload.get("payload") or [], str(out_dir), task_id, title)
        md_path = Path(export_result["path"])
        artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="report", name=md_path.name, file_path=str(md_path), version="v1"))
        if payload.get("source_asset_id"):
            _ensure_trace_link(db, "asset", payload["source_asset_id"], "artifact", artifact.id, "exported_to")
            db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="markdown exported")
        return {"status": "markdown_exported", "path": str(md_path)}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()
