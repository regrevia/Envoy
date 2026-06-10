import json
import base64
import inspect
import re
import shutil
import zipfile
import zlib
from xml.etree import ElementTree
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.services.crud_service import update_task_status, create_artifact
from app.core.storage import storage_client
from app.services.media_service import MediaProcessingError, split_video, split_audio, extract_frames, transcribe_audio, run_ocr_on_frames
from app.services.export_service import build_dataset_jsonl, build_rich_export_report
from app.services.train_export_service import export_skill_markdown, export_markdown_from_video_summary
from app.services.analysis_service import build_analysis_payload
from app.services.ollama_service import generate_with_ollama, parse_ollama_json_object
from app.core.paths import SEGMENT_DIR, FRAME_DIR, AUDIO_DIR, EXPORT_DIR
from app.core.config import MEDIA_ENABLE_FRAME_EXTRACTION, MEDIA_ENABLE_OCR, MEDIA_MAX_SEGMENT_COUNT, MEDIA_SEGMENT_SECONDS, SAMPLE_GENERATION_BATCH_LIMIT, TEXT_PREPROCESS_CHUNK_CHARS, TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS
from app.models.semantic_unit import SemanticUnit
from app.models.asset import Asset
from app.models.generated_sample import GeneratedSample
from app.models.processing_task import ProcessingTask
from app.models.trace_link import TraceLink
from app.schemas.artifact import ArtifactCreate


def _safe_update(db, task_id, status, progress=None, result_summary=None, error_message=None):
    if task_id:
        update_task_status(db, task_id, status, progress, result_summary, error_message)


def _task_is_canceled(db, task_id):
    if not task_id:
        return False
    db.expire_all()
    task = db.query(ProcessingTask).filter(ProcessingTask.id == task_id).first()
    return task is not None and task.status == "canceled"


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


def _split_audio_for_task(db, task_id: str | None, source_path: str, output_dir: str):
    if _supports_should_cancel(split_audio):
        return split_audio(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
    return split_audio(source_path, output_dir)


def _split_video_for_task(db, task_id: str | None, source_path: str, output_dir: str):
    if _supports_should_cancel(split_video):
        return split_video(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
    return split_video(source_path, output_dir)


def _extract_frames_for_task(db, task_id: str | None, source_path: str, output_dir: str):
    if _supports_should_cancel(extract_frames):
        return extract_frames(source_path, output_dir, should_cancel=lambda: _task_is_canceled(db, task_id))
    return extract_frames(source_path, output_dir)


def _run_ocr_for_task(db, task_id: str | None, frame_paths: list[str]):
    if _supports_should_cancel(run_ocr_on_frames):
        return run_ocr_on_frames(frame_paths, should_cancel=lambda: _task_is_canceled(db, task_id))
    return run_ocr_on_frames(frame_paths)


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
    return "\n".join(deduped).strip() or text


def _sample_fallback(unit: SemanticUnit | None, payload: dict):
    title = (unit.title if unit else None) or payload.get("title") or "source material"
    content = (unit.content if unit else None) or payload.get("content") or payload.get("text") or ""
    content = _readable_source_text(content)
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
    source_text = _readable_source_text(unit.content if unit else fallback["output_text"])
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
    return {
        "record_type": "semantic_unit",
        "id": unit.id,
        "project_id": unit.project_id,
        "asset_id": unit.asset_id,
        "segment_id": unit.segment_id,
        "unit_type": unit.unit_type,
        "title": unit.title,
        "content": unit.content,
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


@celery_app.task(bind=True, max_retries=2)
def ping_job(self):
    return {"status": "ok"}


@celery_app.task(bind=True, max_retries=2)
def preprocess_job(self, payload: dict):
    db = SessionLocal()
    media_temp_dirs = []
    try:
        task_id = payload.get("task_id")
        asset_id = payload.get("asset_id")
        project_id = payload.get("project_id")
        _safe_update(db, task_id, "running", 10)
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
                    extracted_text = _extract_pdf_text(source_path)
                else:
                    extracted_text = _extract_ooxml_text(source_path)
                if extracted_text:
                    chunk_count = len(_text_chunks(extracted_text))
                    if _task_is_canceled(db, task_id):
                        return _canceled_result(payload)
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
                        "source": "document_text",
                        "chunk_count": chunk_count,
                        "extraction_status": "text_extracted",
                    }
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
            audio_segment_paths = _split_audio_for_task(db, task_id, source_path, str(audio_dir))
            _safe_update(db, task_id, "running", 35, result_summary=f"audio split done: {len(audio_segment_paths)} segment(s)")
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
            transcription_parts = []
            transcription_segments = []
            transcription_results = []
            transcription_inputs = audio_segment_paths or [source_path]
            transcription_count = len(transcription_inputs)
            for index, segment_path in enumerate(transcription_inputs):
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
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
                semantic_payload = {
                    "segments": audio_segment_paths,
                    "char_count": len(transcript_text),
                    "preview": transcript_text[:500],
                    "stage": "preprocess",
                    "source": "audio_transcript",
                    "chunk_count": chunk_count,
                    "segment_count": transcription_count,
                    "transcript": transcription,
                    "transcript_segment_count": len(segments) if isinstance(segments, list) else 0,
                    "extraction_status": transcription_summary["extraction_status"],
                    "transcription_success_count": transcription_summary["success_count"],
                    "transcription_failed_count": transcription_summary["failed_count"],
                    "transcription_failed_segments": transcription_summary["failed_segments"],
                    **_media_limit_metadata(),
                }
                if _task_is_canceled(db, task_id):
                    return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload, semantic_payload)
                _successful_preprocess_replaces_previous(db, asset_id, task_id)
                _safe_update(db, task_id, "success", 100, result_summary=f"audio preprocess done: {semantic_payload['char_count']} chars, {len(units)} chunks{_partial_transcription_suffix(transcription_summary)}")
                return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
            semantic_payload = _build_file_semantic_payload(asset, source_path, "audio_asset")
            semantic_payload.update(_media_limit_metadata())
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
        media_temp_dirs.extend([segment_dir, frame_dir])
        segment_paths = _split_video_for_task(db, task_id, source_path, str(segment_dir))
        _safe_update(db, task_id, "running", 35, result_summary=f"media split done: {len(segment_paths)} segment(s)")
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        if MEDIA_ENABLE_FRAME_EXTRACTION:
            frame_paths = _extract_frames_for_task(db, task_id, source_path, str(frame_dir))
            frame_extraction_skipped = False
            _safe_update(db, task_id, "running", 55, result_summary=f"frame extraction done: {len(frame_paths)} frame pattern(s)")
        else:
            frame_paths = []
            frame_extraction_skipped = True
            _safe_update(db, task_id, "running", 55, result_summary="frame extraction skipped: disabled")
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        transcription_parts = []
        transcription_segments = []
        transcription_results = []
        transcription_inputs = segment_paths or [source_path]
        transcription_count = len(transcription_inputs)
        for index, segment_path in enumerate(transcription_inputs):
            if _task_is_canceled(db, task_id):
                return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
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
            ocr_results = _run_ocr_for_task(db, task_id, frame_paths)
            ocr_skipped = False
            _safe_update(db, task_id, "running", 90, result_summary=f"ocr done: {len(ocr_results)} frame result(s)")
        else:
            ocr_results = []
            ocr_skipped = True
            _safe_update(db, task_id, "running", 90, result_summary="ocr skipped: disabled")
        if _task_is_canceled(db, task_id):
            return _canceled_preprocess_result(db, asset_id, task_id, has_previous_preprocess, payload)
        semantic_payload = {
            "segments": segment_paths,
            "frames": frame_paths,
            "transcript": transcription,
            "ocr": ocr_results,
            "frame_extraction_skipped": frame_extraction_skipped,
            "ocr_skipped": ocr_skipped,
            "stage": "preprocess",
            "source_path": source_path,
            **_media_limit_metadata(),
        }
        transcript_text = str(transcription.get("text", "") if isinstance(transcription, dict) else "").strip()
        failed_transcriptions = [
            item["transcript"]
            for item in transcription_results
            if isinstance(item.get("transcript"), dict) and item["transcript"].get("status") == "failed"
        ]
        transcription_summary = _transcription_segment_summary(transcription_results)
        semantic_payload.update({
            "extraction_status": transcription_summary["extraction_status"],
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
        ocr_text = "\n".join(str(item.get("text", "")).strip() for item in ocr_results if isinstance(item, dict) and str(item.get("text", "")).strip()).strip()
        if not transcript_text and not ocr_text and failed_transcriptions and len(failed_transcriptions) == len(transcription_results):
            error = next((str(item.get("error")) for item in failed_transcriptions if item.get("error")), "unknown transcription error")
            message = f"media transcription failed: {error}"
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
        su = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="scene", title="preprocess summary", content=json.dumps(semantic_payload, ensure_ascii=False), labels={"stage": "preprocess", "source": "media_asset", "task_id": task_id}, confidence=0.8)
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
            result_summary=f"media preprocess done: {len(segment_paths)} segment(s), {len(frame_paths)} frame(s), max {semantic_payload['max_input_seconds']} sec{_partial_transcription_suffix(transcription_summary)}",
        )
        return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
    except MediaProcessingError as exc:
        if str(exc).endswith(" canceled") and _task_is_canceled(db, payload.get("task_id")):
            return _canceled_preprocess_result(db, payload.get("asset_id"), payload.get("task_id"), has_previous_preprocess, payload)
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        return {"status": "failed", "payload": payload, "error": str(exc)}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
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
            sample = _persist_generated_sample(db, project_id, unit, payload)
            _safe_update(db, task_id, "success", 100, result_summary="1 sample generated")
            return {"status": "samples_generated", "payload": payload, "sample_id": sample.id, "sample_count": 1}

        batch_limit = max(int(input_config.get("limit") or SAMPLE_GENERATION_BATCH_LIMIT), 1)
        units = (
            db.query(SemanticUnit)
            .filter(SemanticUnit.project_id == project_id)
            .order_by(SemanticUnit.created_at.asc())
            .limit(batch_limit)
            .all()
            if project_id
            else []
        )
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
        semantic_unit_models = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).order_by(SemanticUnit.created_at.asc()).all() if project_id else []
        sample_models = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).order_by(GeneratedSample.created_at.asc()).all() if project_id else []
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
