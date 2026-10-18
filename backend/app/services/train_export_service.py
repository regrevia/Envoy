from __future__ import annotations

from pathlib import Path
import json
import shutil
from html import escape
from uuid import uuid4

from app.core.config import EXPORT_DIR, MARKDOWN_CHUNK_MAX_CHARS, MARKDOWN_CHUNK_TARGET_CHARS
from app.models.generated_sample import GeneratedSample
from app.models.artifact import Artifact
from app.models.asset import Asset
from app.models.processing_task import ProcessingTask
from app.models.semantic_unit import SemanticUnit
from app.models.trace_link import TraceLink
from app.schemas.artifact import ArtifactCreate
from app.services.crud_service import create_artifact, create_export_record
from app.services.export_paths import build_export_root, build_subdir, slugify_name
from app.services.export_service import build_dataset_jsonl, build_markdown_report
from app.services.ollama_service import generate_with_ollama, parse_ollama_json_object_with_status


def _dedupe_rows(rows: list[dict]):
    seen = set()
    cleaned = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    return cleaned


def _split_rows(rows: list[dict]):
    total = len(rows)
    train_end = max(int(total * 0.8), 1) if total else 0
    val_end = max(int(total * 0.9), train_end) if total else 0
    return {"train": rows[:train_end], "val": rows[train_end:val_end], "test": rows[val_end:]}


def _positive_int_or_none(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _semantic_source_metadata(rows: list[dict]):
    source_counts = {}
    unit_type_counts = {}
    media_limit_counts = {}
    for row in rows:
        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source = labels.get("source") or metadata.get("source")
        unit_type = row.get("unit_type") or metadata.get("unit_type")
        if source:
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1
        if unit_type:
            unit_type_counts[str(unit_type)] = unit_type_counts.get(str(unit_type), 0) + 1
        segment_seconds = labels.get("segment_seconds") or metadata.get("segment_seconds")
        max_segment_count = labels.get("max_segment_count") or metadata.get("max_segment_count")
        max_input_seconds = labels.get("max_input_seconds") or metadata.get("max_input_seconds")
        segment_seconds = _positive_int_or_none(segment_seconds)
        max_segment_count = _positive_int_or_none(max_segment_count)
        max_input_seconds = _positive_int_or_none(max_input_seconds)
        if segment_seconds and max_segment_count and max_input_seconds:
            key = (segment_seconds, max_segment_count, max_input_seconds)
            media_limit_counts[key] = media_limit_counts.get(key, 0) + 1
    metadata = {"source_counts": source_counts, "unit_type_counts": unit_type_counts}
    if media_limit_counts:
        metadata["media_processing_limits"] = [
            {
                "segment_seconds": segment_seconds,
                "max_segment_count": max_segment_count,
                "max_input_seconds": max_input_seconds,
                "unit_count": count,
            }
            for (segment_seconds, max_segment_count, max_input_seconds), count in sorted(media_limit_counts.items())
        ]
    return metadata


def _source_label(value):
    labels = {
        "text_asset": "文本素材",
        "document_text": "文档文本",
        "image_text": "图片 OCR",
        "media_transcript": "视频转写",
        "media_ocr": "视频 OCR",
        "audio_transcript": "音频转写",
        "media_asset": "媒体素材",
    }
    return labels.get(str(value or ""), str(value or "未知来源"))


def _unit_type_label(value):
    labels = {
        "text": "文本",
        "document": "文档",
        "image": "图片",
        "audio": "音频",
        "transcript": "转写",
        "ocr": "OCR",
        "scene": "场景",
        "rule": "规则",
        "concept": "概念",
    }
    return labels.get(str(value or ""), str(value or "未分类"))


def _compact_text(value, max_chars: int = 1400):
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n（内容已截断，完整结构保存在 manifest 或 RAG 语料中。）"


def _parse_json_content(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _content_format(value):
    return "json" if _parse_json_content(value) is not None else "text"


def _append_structured_list_parts(parts: list[str], value):
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
        elif isinstance(item, dict):
            for key in ("summary", "text", "content", "name", "rule", "title"):
                text = item.get(key)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                    break


def _readable_structured_content(value: str):
    parsed = _parse_json_content(value)
    if parsed is None:
        return str(value or "").strip()

    parts = []
    if isinstance(parsed, list):
        _append_structured_list_parts(parts, parsed)
    else:
        transcript = parsed.get("transcript")
        if isinstance(transcript, dict):
            text = transcript.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

        _append_structured_list_parts(parts, parsed.get("ocr"))
        for key in ("scenes", "rules", "concepts", "steps", "actions", "keywords"):
            _append_structured_list_parts(parts, parsed.get(key))
        for key in ("summary", "preview", "content", "text", "title"):
            text = parsed.get(key)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    seen = set()
    deduped = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped.append(part)
    if deduped:
        return "\n".join(deduped)
    return ""


def _semantic_unit_export_content(row: dict):
    content = _readable_structured_content(row.get("content") or "")
    return content.strip()


def _semantic_unit_has_export_content(row: dict):
    return bool(_semantic_unit_export_content(row))


def _row_labels(row: dict):
    return row.get("labels") if isinstance(row.get("labels"), dict) else {}


def _rag_failed_segment_count(row: dict):
    parsed = _parse_json_content(row.get("content") or "")
    if not isinstance(parsed, dict):
        labels = _row_labels(row)
        return _positive_int_or_none(labels.get("transcription_failed_count")) or 0
    failed_segments = parsed.get("transcription_failed_segments")
    if isinstance(failed_segments, list):
        return len(failed_segments)
    labels = _row_labels(row)
    return _positive_int_or_none(labels.get("transcription_failed_count")) or 0


def _rag_extraction_status(row: dict):
    parsed = _parse_json_content(row.get("content") or "")
    if isinstance(parsed, dict) and isinstance(parsed.get("extraction_status"), str):
        return parsed["extraction_status"].strip()
    labels = _row_labels(row)
    status = labels.get("extraction_status")
    return str(status).strip() if status else ""


def _rag_degraded_capabilities(row: dict):
    parsed = _parse_json_content(row.get("content") or "")
    if isinstance(parsed, dict) and isinstance(parsed.get("degraded_capabilities"), list):
        return [str(item).strip() for item in parsed["degraded_capabilities"] if str(item).strip()]
    labels = _row_labels(row)
    degraded = labels.get("degraded_capabilities")
    if isinstance(degraded, list):
        return [str(item).strip() for item in degraded if str(item).strip()]
    return []


def _rag_document_row(row: dict):
    prebuilt_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    text = str(row.get("text") or "").strip() if row.get("text") is not None else ""
    if not text:
        text = _semantic_unit_export_content(row)
    if not text:
        return None
    labels = _row_labels(row)
    metadata = dict(prebuilt_metadata)
    if row.get("confidence") is not None:
        metadata["confidence"] = row.get("confidence")
    extraction_status = _rag_extraction_status(row)
    if extraction_status:
        metadata["extraction_status"] = extraction_status
    degraded_capabilities = _rag_degraded_capabilities(row)
    if degraded_capabilities:
        metadata["degraded_capabilities"] = degraded_capabilities
    failed_segment_count = _rag_failed_segment_count(row)
    if failed_segment_count:
        metadata["transcription_failed_count"] = failed_segment_count
    return {
        "doc_id": str(row.get("id") or row.get("source_unit_id") or row.get("record_type") or uuid4()),
        "project_id": row.get("project_id") or metadata.get("project_id"),
        "asset_id": row.get("asset_id") or metadata.get("asset_id"),
        "task_id": labels.get("task_id"),
        "title": _row_title(row, 0),
        "text": text,
        "source": _row_source(row),
        "unit_type": row.get("unit_type") or metadata.get("unit_type"),
        "content_format": metadata.get("content_format") or _content_format(row.get("content") or ""),
        "metadata": metadata,
    }


def _rag_manifest_quality(clean_rows: list[dict], source_rows: list[dict]):
    partial_count = 0
    failed_segments = 0
    for row in source_rows:
        status = _rag_extraction_status(row)
        if status == "partial_success":
            partial_count += 1
        failed_segments += _rag_failed_segment_count(row)
    return {
        "clean_row_count": len(clean_rows),
        "partial_row_count": partial_count,
        "failure_breakdown": {
            "transcription_failed_segments": failed_segments,
        },
    }


def _row_source_name(row: dict):
    labels = _row_labels(row)
    return str(labels.get("source_name") or row.get("title") or row.get("asset_id") or "source").strip()


def _row_reference_text(row: dict):
    labels = _row_labels(row)
    page_range = labels.get("page_range")
    if isinstance(page_range, (list, tuple)) and len(page_range) >= 2:
        return f"第 {page_range[0]}-{page_range[1]} 页"
    time_range = labels.get("time_range")
    if isinstance(time_range, (list, tuple)) and len(time_range) >= 2:
        return f"{time_range[0]} - {time_range[1]}"
    section_path = labels.get("section_path")
    if isinstance(section_path, list) and section_path:
        return " / ".join(str(item).strip() for item in section_path if str(item).strip())
    return "未标注"


def _row_time_range(row: dict):
    labels = _row_labels(row)
    time_range = labels.get("time_range")
    if isinstance(time_range, (list, tuple)) and len(time_range) >= 2:
        start = str(time_range[0]).strip()
        end = str(time_range[1]).strip()
        if start and end:
            return [start, end]
    return None


def _is_video_source_type(source_type):
    return str(source_type or "") in {"media_asset", "media_transcript", "media_ocr", "video"}


def _is_video_row(row: dict):
    return _is_video_source_type(_row_source(row)) or _row_time_range(row) is not None


def _time_range_slug(reference_text: str):
    if not reference_text or reference_text == "未标注":
        return ""
    return slugify_name(reference_text.replace(":", "-").replace(" - ", "-"))


def _source_dir_name(order_index: int, row: dict):
    source = str(_row_source(row) or "text")
    source_name = slugify_name(_row_source_name(row))
    return f"{order_index:02d}-{source}-{source_name}"


def _markdown_chunk_summary(text: str):
    compact = " ".join(str(text or "").split())
    if len(compact) <= 80:
        return compact
    return compact[:80].rstrip() + "…"


def _markdown_chunk_body(row: dict):
    body = _semantic_unit_export_content(row)
    if body:
        return body
    return _row_body(row)


def _group_rows_by_markdown_source(rows: list[dict]):
    grouped = {}
    order = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_key = (
            str(_row_source(row) or "unknown"),
            _row_source_name(row),
        )
        if source_key not in grouped:
            grouped[source_key] = []
            order.append(source_key)
        grouped[source_key].append(row)
    return order, grouped


def _section_path(row: dict):
    labels = _row_labels(row)
    section_path = labels.get("section_path")
    if isinstance(section_path, list):
        cleaned = [str(item).strip() for item in section_path if str(item).strip()]
        if cleaned:
            return cleaned
    title = str(row.get("title") or "").strip()
    return [title] if title else ["未命名片段"]


def _markdown_natural_segments(text: str):
    normalized = str(text or "").strip()
    if not normalized:
        return []
    blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    return blocks or [normalized]


def _split_long_markdown_block(block: str, target_chars: int):
    text = str(block or "").strip()
    if len(text) <= target_chars:
        return [text] if text else []

    split_tokens = ["\n## ", "\n### ", "\n- ", "\n* ", "\n1. ", "。", "！", "？", ".", "!", "?", "\n"]
    pieces = [text]
    for token in split_tokens:
        next_pieces = []
        for piece in pieces:
            if len(piece) <= target_chars:
                next_pieces.append(piece)
                continue
            if token.startswith("\n"):
                parts = [part.strip() for part in piece.split(token) if part.strip()]
                if len(parts) > 1:
                    next_pieces.extend(parts)
                    continue
            if token and token in piece:
                parts = [part.strip() for part in piece.split(token) if part.strip()]
                if len(parts) > 1:
                    next_pieces.extend([parts[0]] + [f"{token.strip()}{part}" if token.strip().startswith("#") else part for part in parts[1:]])
                    continue
            next_pieces.append(piece)
        pieces = next_pieces

    final = []
    for piece in pieces:
        if len(piece) <= target_chars:
            final.append(piece)
            continue
        start = 0
        while start < len(piece):
            end = min(start + target_chars, len(piece))
            final.append(piece[start:end].strip())
            start = end
    return [piece for piece in final if piece]


def _markdown_chunk_lines(
    source_name: str,
    reference_text: str,
    chunk_id: str,
    title: str,
    summary: str,
    body: str,
    section_path: list[str],
):
    return [
        f"# {title}",
        "",
        f"> 摘要：{summary}",
        f"> 来源：{source_name}",
        f"> 引用：{reference_text}",
        f"> 分片ID：{chunk_id}",
        "",
        "## 正文",
        body,
        "",
        "## 来源说明",
        f"- 原始来源：{source_name}",
        f"- 章节路径：{' / '.join(section_path) if section_path else '未标注'}",
        f"- 引用：{reference_text}",
    ]


def _video_timeline_entry(chunk: dict, relative_path: str | None = None):
    time_range = None
    labels = chunk.get("labels") if isinstance(chunk.get("labels"), dict) else {}
    raw_time_range = labels.get("time_range")
    if isinstance(raw_time_range, (list, tuple)) and len(raw_time_range) >= 2:
        time_range = [str(raw_time_range[0]), str(raw_time_range[1])]
    entry = {
        "title": chunk["title"],
        "summary": chunk["summary"],
        "reference_text": chunk["reference_text"],
        "time_range": time_range,
        "chunk_id": chunk["chunk_id"],
        "source_name": chunk["source_name"],
        "status": chunk.get("status") or "success",
        "degraded_capabilities": chunk.get("degraded_capabilities") or [],
        "failed_segment_count": chunk.get("failed_segment_count") or 0,
    }
    if relative_path:
        entry["relative_path"] = relative_path
    return entry


def _chunk_file_name(chunk_index: int, chunk: dict):
    title_slug = slugify_name(chunk["title"])
    if _is_video_source_type(chunk.get("source_type")):
        time_slug = _time_range_slug(chunk.get("reference_text") or "")
        if time_slug:
            return f"{chunk_index:03d}-{time_slug}-{title_slug}.md"
    return f"{chunk_index:03d}-{title_slug}.md"


def _chunk_title_from_section(section_path: list[str]):
    if section_path:
        return str(section_path[-1]).strip() or "未命名分片"
    return "未命名分片"


def _new_markdown_chunk(row: dict, body: str, split_part: int = 1, split_total: int = 1):
    section_path = _section_path(row)
    title = _chunk_title_from_section(section_path)
    if split_total > 1:
        title = f"{title}（第 {split_part}/{split_total} 部分）"
    return {
        "chunk_id": str(row.get("id") or f"chunk-{uuid4().hex[:8]}"),
        "source_name": _row_source_name(row),
        "source_type": _row_source(row),
        "title": title,
        "summary": _markdown_chunk_summary(body),
        "body": body.strip(),
        "reference_text": _row_reference_text(row),
        "section_path": section_path,
        "source_row_id": str(row.get("id") or ""),
        "split_part": split_part,
        "split_total": split_total,
        "labels": _row_labels(row),
        "asset_id": row.get("asset_id"),
        "project_id": row.get("project_id"),
        "unit_type": row.get("unit_type"),
        "status": _rag_extraction_status(row) or "success",
        "degraded_capabilities": _rag_degraded_capabilities(row),
        "failed_segment_count": _rag_failed_segment_count(row),
    }


def _finalize_markdown_chunk_parts(row: dict, parts: list[str]):
    total = len(parts)
    return [_new_markdown_chunk(row, part, index + 1, total) for index, part in enumerate(parts)]


def _split_row_to_markdown_chunks(row: dict, target_chars: int, max_chars: int):
    body = _markdown_chunk_body(row).strip()
    if not body:
        return []
    segments = _markdown_natural_segments(body)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_len = 0

    for segment in segments:
        segment_len = len(segment)
        if segment_len > target_chars:
            flush()
            chunks.extend(_split_long_markdown_block(segment, target_chars))
            continue
        projected = current_len + (2 if current else 0) + segment_len
        if current and projected > target_chars:
            flush()
        current.append(segment)
        current_len += (2 if current_len else 0) + segment_len
        if current_len >= max_chars:
            flush()
    flush()
    return _finalize_markdown_chunk_parts(row, [part for part in chunks if part.strip()] or [body])


def build_markdown_chunks(rows: list[dict], target_chars: int = MARKDOWN_CHUNK_TARGET_CHARS, max_chars: int = MARKDOWN_CHUNK_MAX_CHARS):
    clean_rows = [row for row in _dedupe_rows(rows) if _markdown_chunk_body(row)]
    order, grouped = _group_rows_by_markdown_source(clean_rows)
    final_chunks = []
    for source_key in order:
        for row in grouped[source_key]:
            final_chunks.extend(_split_row_to_markdown_chunks(row, target_chars, max_chars))
    return final_chunks


def enhance_markdown_chunk(chunk: dict, enabled: bool):
    if not enabled:
        return chunk, {"status": "skipped", "error": None}
    prompt = "\n".join(_markdown_chunk_lines(
        source_name=chunk["source_name"],
        reference_text=chunk["reference_text"],
        chunk_id=chunk["chunk_id"],
        title=chunk["title"],
        summary=chunk["summary"],
        body=chunk["body"],
        section_path=chunk["section_path"],
    ))
    result = generate_with_ollama(
        "Polish the following Markdown chunk metadata. Return JSON with title and summary only.",
        prompt,
    )
    metadata = _ollama_request_metadata(result)
    if result.get("status") != "success":
        return chunk, {"status": "failed", **metadata}
    parsed, parse_metadata = _ollama_parse_metadata(result.get("text", ""), ("title", "summary"))
    status = "success" if parse_metadata.get("ollama_parse_status") == "parsed" else "failed"
    updated_chunk = dict(chunk)
    if status == "success":
        title = parsed.get("title")
        summary = parsed.get("summary")
        if isinstance(title, str) and title.strip():
            updated_chunk["title"] = title.strip()
        if isinstance(summary, str) and summary.strip():
            updated_chunk["summary"] = summary.strip()
    return updated_chunk, {"status": status, **metadata, **parse_metadata}


def _write_utf8_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _markdown_manifest_quality(rows: list[dict]):
    partial_count = 0
    failed_segments = 0
    for row in rows:
        if _rag_extraction_status(row) == "partial_success":
            partial_count += 1
        failed_segments += _rag_failed_segment_count(row)
    return {
        "partial_chunk_count": partial_count,
        "failure_breakdown": {
            "transcription_failed_segments": failed_segments,
        },
    }


def _row_title(row: dict, index: int):
    return str(row.get("title") or row.get("sample_type") or row.get("relation_type") or f"片段 {index}").strip()


def _row_source(row: dict):
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return labels.get("source") or metadata.get("source") or row.get("source_type") or row.get("record_type")


def _row_body(row: dict):
    content_keys = ("content", "text", "summary", "output_text", "input_text")
    skip_keys = {"id", "project_id", "asset_id", "segment_id", "labels", "metadata", *content_keys}
    for key in content_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            readable = _readable_structured_content(value)
            if readable.strip():
                return readable
    useful = []
    for key, value in row.items():
        if key in skip_keys:
            continue
        if isinstance(value, (str, int, float)) and str(value).strip():
            useful.append(f"{key}: {value}")
    return "；".join(useful)


def _source_overview_lines(source_counts: dict):
    if not source_counts:
        return ["- 尚未记录明确来源。"]
    return [f"- {_source_label(source)}：{count} 条" for source, count in source_counts.items()]


def _semantic_markdown_lines(rows: list[dict]):
    lines = []
    trace_rows = []
    for index, row in enumerate(rows, 1):
        if row.get("source_type") and row.get("target_type"):
            trace_rows.append(row)
            continue
        title = _row_title(row, index)
        body = _compact_text(_row_body(row))
        source = _source_label(_row_source(row))
        unit_type = _unit_type_label(row.get("unit_type"))
        lines.extend([f"### {index}. {title}", ""])
        lines.append(f"- 来源：{source}")
        if row.get("unit_type"):
            lines.append(f"- 类型：{unit_type}")
        if row.get("confidence") is not None:
            lines.append(f"- 置信度：{row.get('confidence')}")
        if row.get("asset_id"):
            lines.append(f"- 素材 ID：{row.get('asset_id')}")
        if row.get("id"):
            lines.append(f"- 语义单元 ID：{row.get('id')}")
        if body:
            lines.extend(["", body])
        lines.append("")
    if trace_rows:
        lines.extend(["## 追溯信息", ""])
        for row in trace_rows:
            trace_id = row.get("id") or "trace"
            lines.append(f"- {trace_id}：{row.get('source_type')}:{row.get('source_id')} -> {row.get('target_type')}:{row.get('target_id')}（{row.get('relation_type')}）")
        lines.append("")
    return lines


def _resolve_root(out_dir: str | None, task_type: str, task_id: str, task_name: str | None = None):
    base = out_dir or str(EXPORT_DIR)
    return build_export_root(base, task_type, task_id, task_name)


def _write_record(db, project_id: str, task_id: str, export_type: str, task_name: str | None, root: Path, file_list: list[str]):
    if db is None:
        return None
    return create_export_record(db, project_id, task_id, export_type, task_name, str(root), json.dumps(file_list, ensure_ascii=False))


def _write_artifacts(db, project_id: str | None, artifact_type: str, paths: list[Path]):
    if db is None or not project_id:
        return []
    return [
        create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type=artifact_type, name=path.name, file_path=str(path), version="v1"))
        for path in paths
    ]


def _semantic_unit_source_ids(rows: list[dict]):
    source_ids = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source_id = metadata.get("semantic_unit_id")
        if not source_id and row.get("id") and ("content" in row or "unit_type" in row):
            source_id = row.get("id")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        source_ids.append(str(source_id))
    return source_ids


def _generated_sample_source_ids(rows: list[dict]):
    source_ids = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = row.get("id")
        is_sample_row = row.get("record_type") == "generated_sample" or (
            "sample_type" in row and "input_text" in row and "output_text" in row
        )
        if not is_sample_row or not source_id or source_id in seen:
            continue
        seen.add(source_id)
        source_ids.append(str(source_id))
    return source_ids


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


def _write_artifact_trace_links(db, project_id: str, artifacts, rows: list[dict]):
    semantic_source_ids = _semantic_unit_source_ids(rows)
    sample_source_ids = _generated_sample_source_ids(rows)
    if not db or (not semantic_source_ids and not sample_source_ids):
        return []
    valid_semantic_ids = {
        row.id
        for row in db.query(SemanticUnit.id)
        .filter(SemanticUnit.project_id == project_id, SemanticUnit.id.in_(semantic_source_ids))
        .all()
    } if semantic_source_ids else set()
    valid_sample_ids = {
        row.id
        for row in db.query(GeneratedSample.id)
        .filter(GeneratedSample.project_id == project_id, GeneratedSample.id.in_(sample_source_ids))
        .all()
    } if sample_source_ids else set()
    if not valid_semantic_ids and not valid_sample_ids:
        return []
    links = []
    for artifact in artifacts:
        for source_id in semantic_source_ids:
            if source_id not in valid_semantic_ids:
                continue
            links.append(_ensure_trace_link(db, "semantic_unit", source_id, "artifact", artifact.id, "exported_to"))
        for source_id in sample_source_ids:
            if source_id not in valid_sample_ids:
                continue
            links.append(_ensure_trace_link(db, "generated_sample", source_id, "artifact", artifact.id, "exported_to"))
    db.commit()
    return links


def _semantic_unit_is_exportable(row: SemanticUnit, tasks_by_id: dict[str, ProcessingTask]):
    labels = row.labels if isinstance(row.labels, dict) else {}
    if labels.get("checkpoint") == "segment_transcript":
        return False
    task_id = labels.get("task_id")
    if not task_id:
        return True
    task = tasks_by_id.get(task_id)
    if task is None:
        return True
    return task.status == "success"


def _generated_sample_is_exportable(row: GeneratedSample, exportable_semantic_ids: set[str], project_semantic_ids: set[str]):
    if not row.source_unit_id:
        return True
    if row.source_unit_id not in project_semantic_ids:
        return True
    return row.source_unit_id in exportable_semantic_ids


def _generated_sample_has_export_content(row: GeneratedSample):
    input_text = str(row.input_text or "").strip()
    output_text = str(row.output_text or "").strip()
    if not input_text or not output_text:
        return False
    return output_text != "No source content was available."


def _trace_link_is_exportable(row: dict, visible_ids: set[str]):
    if not isinstance(row, dict):
        return False
    source_id = row.get("source_id")
    target_id = row.get("target_id")
    return str(source_id or "") in visible_ids and str(target_id or "") in visible_ids


def _auto_rows_from_assets(db, project_id: str, task_name: str | None = None):
    all_semantic_units = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).order_by(SemanticUnit.created_at.desc()).all()
    all_samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).order_by(GeneratedSample.created_at.desc()).all()
    assets = db.query(Asset).filter(Asset.project_id == project_id).all()
    tasks = db.query(ProcessingTask).filter(ProcessingTask.project_id == project_id).all()
    artifacts = db.query(Artifact).filter(Artifact.project_id == project_id).all()
    tasks_by_id = {task.id: task for task in tasks}
    semantic_units = [row for row in all_semantic_units if _semantic_unit_is_exportable(row, tasks_by_id)]
    project_semantic_ids = {row.id for row in all_semantic_units}
    exportable_semantic_ids = {row.id for row in semantic_units}
    samples = [
        row
        for row in all_samples
        if _generated_sample_is_exportable(row, exportable_semantic_ids, project_semantic_ids)
        and _generated_sample_has_export_content(row)
    ]
    project_entity_ids = {
        *(row.id for row in semantic_units),
        *(row.id for row in samples),
        *(row.id for row in assets),
        *(row.id for row in tasks),
        *(row.id for row in artifacts),
        *(row.asset_id for row in semantic_units if row.asset_id),
    }
    trace_links = (
        db.query(TraceLink)
        .filter(
            (TraceLink.source_id.in_(project_entity_ids))
            | (TraceLink.target_id.in_(project_entity_ids))
        )
        .order_by(TraceLink.created_at.desc())
        .all()
        if project_entity_ids
        else []
    )
    return {
        "semantic_units": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "asset_id": row.asset_id,
                "segment_id": row.segment_id,
                "unit_type": row.unit_type,
                "title": row.title,
                "content": row.content,
                "labels": row.labels,
                "confidence": row.confidence,
            }
            for row in semantic_units
        ],
        "samples": [
            {
                "record_type": "generated_sample",
                "id": row.id,
                "project_id": row.project_id,
                "source_unit_id": row.source_unit_id,
                "sample_type": row.sample_type,
                "input_text": row.input_text,
                "output_text": row.output_text,
                "quality_score": row.quality_score,
            }
            for row in samples
        ],
        "trace_links": [
            {
                "id": row.id,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "relation_type": row.relation_type,
            }
            for row in trace_links
        ] if trace_links else [],
        "task_name": task_name,
    }


def _finalize_export(db, project_id: str | None, task_id: str, export_type: str, task_name: str | None, root: Path, paths: list[Path], rows: list[dict] | None = None):
    if db and project_id:
        _write_record(db, project_id, task_id, export_type, task_name, root, [str(path) for path in paths])
        artifacts = _write_artifacts(db, project_id, export_type, paths)
        _write_artifact_trace_links(db, project_id, artifacts, rows or [])


def _ollama_request_metadata(result: dict):
    metadata = {"ollama_status": result.get("status")}
    mappings = {
        "truncated": "ollama_truncated",
        "original_prompt_chars": "ollama_original_prompt_chars",
        "sent_prompt_chars": "ollama_sent_prompt_chars",
        "max_prompt_chars": "ollama_max_prompt_chars",
    }
    for source_key, target_key in mappings.items():
        if source_key in result:
            metadata[target_key] = result[source_key]
    if result.get("status") != "success" and result.get("error"):
        metadata["ollama_error"] = result["error"]
    return metadata


def _ollama_parse_metadata(text: str, required_keys: tuple[str, ...]):
    parsed_result = parse_ollama_json_object_with_status(text, required_keys=required_keys)
    metadata = {"ollama_parse_status": parsed_result["parse_status"]}
    if parsed_result.get("parse_error"):
        metadata["ollama_parse_error"] = parsed_result["parse_error"]
    return parsed_result["parsed"], metadata


def _ollama_rag_metadata(text: str):
    result = generate_with_ollama(
        "Extract RAG metadata from the source. Return JSON with summary, keywords, and usage.",
        text or "",
    )
    metadata = _ollama_request_metadata(result)
    if result.get("status") != "success":
        return metadata

    parsed, parse_metadata = _ollama_parse_metadata(result.get("text", ""), ("summary",))
    metadata.update(parse_metadata)
    if parse_metadata["ollama_parse_status"] != "parsed":
        return metadata
    if isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
        metadata["llm_summary"] = parsed["summary"].strip()
    keywords = parsed.get("keywords")
    if isinstance(keywords, list):
        metadata["llm_keywords"] = [str(item).strip() for item in keywords if str(item).strip()]
    if isinstance(parsed.get("usage"), str) and parsed["usage"].strip():
        metadata["llm_usage"] = parsed["usage"].strip()
    return metadata


def _ollama_markdown_summary(rows: list[dict]):
    source_text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    result = generate_with_ollama(
        "Summarize this Markdown report source. Return JSON with summary.",
        source_text,
    )
    metadata = _ollama_request_metadata(result)
    if result.get("status") != "success":
        return metadata

    parsed, parse_metadata = _ollama_parse_metadata(result.get("text", ""), ("summary",))
    metadata.update(parse_metadata)
    if parse_metadata["ollama_parse_status"] != "parsed":
        return metadata
    if isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
        metadata["llm_summary"] = parsed["summary"].strip()
    return metadata


def _ollama_skill_review(rows: list[dict]):
    source_text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    result = generate_with_ollama(
        "Review this skill package. Return JSON with summary, usage, and risks.",
        source_text,
    )
    metadata = _ollama_request_metadata(result)
    if result.get("status") != "success":
        return metadata

    parsed, parse_metadata = _ollama_parse_metadata(result.get("text", ""), ("summary",))
    metadata.update(parse_metadata)
    if parse_metadata["ollama_parse_status"] != "parsed":
        return metadata
    if isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
        metadata["llm_summary"] = parsed["summary"].strip()
    if isinstance(parsed.get("usage"), str) and parsed["usage"].strip():
        metadata["llm_usage"] = parsed["usage"].strip()
    risks = parsed.get("risks")
    if isinstance(risks, list):
        metadata["llm_risks"] = [str(item).strip() for item in risks if str(item).strip()]
    return metadata


def export_sft_dataset(rows: list[dict], out_dir: str, task_id: str, task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    parts = _split_rows(clean_rows)
    root = _resolve_root(out_dir, "sft", task_id, task_name)
    data_dir = build_subdir(root, "dataset")
    train_path = data_dir / "sft_train.jsonl"
    val_path = data_dir / "sft_val.jsonl"
    test_path = data_dir / "sft_test.jsonl"
    build_dataset_jsonl(parts["train"], str(train_path))
    build_dataset_jsonl(parts["val"], str(val_path))
    build_dataset_jsonl(parts["test"], str(test_path))
    _finalize_export(db, project_id, task_id, "sft", task_name, root, [train_path, val_path, test_path], clean_rows)
    return {"export_type": "sft", "root": str(root), "paths": {"train": str(train_path), "val": str(val_path), "test": str(test_path)}, "count": len(clean_rows)}


def export_instruction_dataset(rows: list[dict], out_dir: str, task_id: str, task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    parts = _split_rows(clean_rows)
    root = _resolve_root(out_dir, "instruction", task_id, task_name)
    data_dir = build_subdir(root, "dataset")
    train_path = data_dir / "instruction_train.jsonl"
    val_path = data_dir / "instruction_val.jsonl"
    test_path = data_dir / "instruction_test.jsonl"
    build_dataset_jsonl(parts["train"], str(train_path))
    build_dataset_jsonl(parts["val"], str(val_path))
    build_dataset_jsonl(parts["test"], str(test_path))
    _finalize_export(db, project_id, task_id, "instruction", task_name, root, [train_path, val_path, test_path], clean_rows)
    return {"export_type": "instruction", "root": str(root), "paths": {"train": str(train_path), "val": str(val_path), "test": str(test_path)}, "count": len(clean_rows)}


def export_preference_dataset(rows: list[dict], out_dir: str, task_id: str, task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    parts = _split_rows(clean_rows)
    root = _resolve_root(out_dir, "preference", task_id, task_name)
    data_dir = build_subdir(root, "dataset")
    train_path = data_dir / "preference_train.jsonl"
    val_path = data_dir / "preference_val.jsonl"
    test_path = data_dir / "preference_test.jsonl"
    build_dataset_jsonl(parts["train"], str(train_path))
    build_dataset_jsonl(parts["val"], str(val_path))
    build_dataset_jsonl(parts["test"], str(test_path))
    _finalize_export(db, project_id, task_id, "preference", task_name, root, [train_path, val_path, test_path], clean_rows)
    return {"export_type": "preference", "root": str(root), "paths": {"train": str(train_path), "val": str(val_path), "test": str(test_path)}, "count": len(clean_rows)}


def export_rag_corpus(rows: list[dict], out_dir: str, task_id: str, task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    rag_rows = [row for row in (_rag_document_row(row) for row in clean_rows) if row is not None]
    semantic_metadata = _semantic_source_metadata(clean_rows)
    quality_metadata = _rag_manifest_quality(rag_rows, clean_rows)
    root = _resolve_root(out_dir, "rag", task_id, task_name)
    data_dir = build_subdir(root, "rag")
    out_path = data_dir / "rag_corpus.jsonl"
    manifest_path = data_dir / "rag_manifest.json"
    build_dataset_jsonl(rag_rows, str(out_path))
    manifest = {
        "schema_version": "envoy.rag.v1",
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "row_count": len(clean_rows),
        **quality_metadata,
        **semantic_metadata,
        "files": {
            "corpus": str(out_path),
            "manifest": str(manifest_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _finalize_export(db, project_id, task_id, "rag", task_name, root, [out_path, manifest_path], clean_rows)
    return {
        "root": str(root),
        "path": str(out_path),
        "task_id": task_id,
        "count": len(rag_rows),
        "paths": {
            "corpus": str(out_path),
            "manifest": str(manifest_path),
        },
    }


def export_skill_markdown(rows: list[dict], out_dir: str, task_id: str, title: str = "Skill Package", task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    semantic_metadata = _semantic_source_metadata(clean_rows)
    root = _resolve_root(out_dir, "skill", task_id, task_name)
    skill_dir = build_subdir(root, "skill")
    out_path = skill_dir / "skill.md"
    manifest_path = skill_dir / "skill.json"
    llm_metadata = _ollama_skill_review(clean_rows)
    content_lines = ["## 输入摘要", ""]
    content_lines.append(f"- 素材条目：{len(clean_rows)} 条")
    for line in _source_overview_lines(semantic_metadata.get("source_counts", {})):
        content_lines.append(line)
    content_lines.extend(["", "## 能力用途", ""])
    content_lines.append(llm_metadata.get("llm_summary") or "将输入素材沉淀为可复用的领域知识、执行步骤和训练素材，供 RAG、Skill 或后续数据集构建使用。")
    content_lines.extend(["", "## 输入素材", ""])
    for idx, row in enumerate(clean_rows, 1):
        title_text = _row_title(row, idx)
        source_text = _source_label(_row_source(row))
        body = _compact_text(_row_body(row), 360)
        content_lines.append(f"- 样本 {idx}：{title_text}；来源：{source_text}")
        if body:
            content_lines.append(f"  摘要：{body}")
    content_lines.extend([
        "",
        "## 执行步骤",
        "",
        "1. 读取输入素材，优先使用已抽取的文本、转写和 OCR 内容。",
        "2. 按来源和语义类型合并知识片段，保留素材 ID 与语义单元 ID 便于追溯。",
        "3. 将核心内容整理为可检索语料、问答样本或可执行 Skill 说明。",
        "4. 对生成结果做人工复核，必要时回到原始素材和追溯链修订。",
        "",
        "## 知识片段",
        "",
        *_semantic_markdown_lines(clean_rows),
    ])
    if llm_metadata.get("llm_summary"):
        content_lines.extend(["", "## AI 质检", "", str(llm_metadata["llm_summary"])])
        if llm_metadata.get("llm_usage"):
            content_lines.extend(["", f"用途：{llm_metadata['llm_usage']}"])
        if llm_metadata.get("llm_risks"):
            content_lines.extend(["", "风险：", *[f"- {item}" for item in llm_metadata["llm_risks"]]])
    build_markdown_report(title, "\n".join(content_lines), str(out_path))
    manifest = {
        "schema_version": "envoy.skill.v1",
        "title": title,
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "materials": clean_rows,
        **semantic_metadata,
        "files": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
        **llm_metadata,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _finalize_export(db, project_id, task_id, "skill", task_name, root, [out_path, manifest_path], clean_rows)
    return {
        "root": str(root),
        "path": str(out_path),
        "paths": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
    }


def export_markdown_bundle(
    rows: list[dict],
    out_dir: str,
    task_id: str,
    title: str = "Markdown Bundle",
    task_name: str | None = None,
    db=None,
    project_id: str | None = None,
    enhance_with_ollama: bool = False,
    include_video_timeline: bool = True,
    full_content_in_overview: bool = False,
    include_video_summary_heading: bool = True,
    export_root_id: str | None = None,
):
    clean_rows = _dedupe_rows(rows)
    semantic_rows = [row for row in clean_rows if _markdown_chunk_body(row)]
    semantic_metadata = _semantic_source_metadata(semantic_rows)
    quality_metadata = _markdown_manifest_quality(semantic_rows)
    llm_metadata = _ollama_markdown_summary(semantic_rows)
    built_chunks = build_markdown_chunks(semantic_rows)
    order, _grouped = _group_rows_by_markdown_source(semantic_rows)

    root = _resolve_root(out_dir, "markdown", export_root_id or task_id, task_name)
    overview_dir = build_subdir(root, "overview")
    sources_dir = build_subdir(root, "sources")
    index_path = root / "index.md"
    overview_path = overview_dir / "00-project-overview.md"
    manifest_path = root / "manifest.json"

    chunk_paths: list[str] = []
    source_paths: list[str] = []
    file_tree_sources: list[dict] = []
    timeline_index: list[dict] = []
    enhanced_chunks = []
    ollama_stats = {"enabled": bool(enhance_with_ollama), "succeeded_chunks": 0, "failed_chunks": 0, "skipped_chunks": 0, "errors": []}
    for chunk in built_chunks:
        updated_chunk, enhancement = enhance_markdown_chunk(chunk, enhance_with_ollama)
        enhanced_chunks.append(updated_chunk)
        if enhancement["status"] == "success":
            ollama_stats["succeeded_chunks"] += 1
        elif enhancement["status"] == "failed":
            ollama_stats["failed_chunks"] += 1
            if enhancement.get("error"):
                ollama_stats["errors"].append(str(enhancement["error"]))
        else:
            ollama_stats["skipped_chunks"] += 1

    chunk_groups: dict[tuple[str, str], list[dict]] = {}
    for chunk in enhanced_chunks:
        source_key = (str(chunk["source_type"] or "unknown"), chunk["source_name"])
        chunk_groups.setdefault(source_key, []).append(chunk)

    index_lines = [
        "# 导出索引",
        "",
        f"- 标题：{title}",
        f"- 任务：{task_name or task_id}",
        f"- 来源数：{len(order)}",
        f"- 分片数：{len(enhanced_chunks)}",
        "",
        "## 来源目录",
        "",
    ]
    overview_lines = [
        f"# {title}",
        "",
    ]
    if include_video_summary_heading:
        overview_lines.extend(["## 视频转化摘要", ""])
    if llm_metadata.get("llm_summary"):
        overview_lines.extend(["## AI 摘要", "", str(llm_metadata["llm_summary"]), ""])
    overview_lines.extend([
        "## 导航目录",
        "",
        "- 素材概览",
        "- 分来源总览",
        "- 核心内容",
        "- 分片索引",
        "- 异常与降级说明",
        "",
        "## 素材概览",
        "",
        f"- 总来源数：{len(order)}",
        f"- 总分片数：{len(enhanced_chunks)}",
        f"- 编码：UTF-8",
        "",
        "## 分来源总览",
        "",
    ])

    for source_index, source_key in enumerate(order, 1):
        source_name = source_key[1]
        source_rows = [row for row in semantic_rows if (_row_source(row), _row_source_name(row)) == source_key]
        source_chunks = chunk_groups.get(source_key, [])
        is_video_source = any(_is_video_row(row) for row in source_rows) or _is_video_source_type(source_key[0])
        if not source_rows:
            continue
        source_dir = build_subdir(sources_dir, _source_dir_name(source_index, source_rows[0]))
        chunks_dir = build_subdir(source_dir, "chunks")
        source_path = source_dir / "source.md"
        source_rel_path = source_path.relative_to(root).as_posix()
        index_lines.extend([
            f"### {source_name}",
            "",
            f"- 来源文档：[{source_name}]({source_rel_path})",
            f"- 来源类型：{_source_label(_row_source(source_rows[0]))}",
            f"- 分片数：{len(source_chunks)}",
            "",
        ])
        overview_lines.extend([f"### {source_name}", "", f"- 来源类型：{_source_label(_row_source(source_rows[0]))}", f"- 分片数：{len(source_chunks)}", ""])

        source_lines = [
            f"# {source_name}",
            "",
            f"> 来源类型：{_source_label(_row_source(source_rows[0]))}",
            f"> 原始文件：{source_name}",
            f"> 来源ID：{source_rows[0].get('asset_id') or source_rows[0].get('id') or source_dir.name}",
            "",
            "## 内容摘要",
            "",
        ]
        for row in source_rows:
            source_lines.append(f"- {' / '.join(_section_path(row))}：{_markdown_chunk_summary(_markdown_chunk_body(row))}")
        source_lines.extend(["", "## 分片索引", ""])

        if is_video_source and include_video_timeline:
            index_lines.extend(["## 视频时间线", ""])
            source_lines.extend(["", "## 视频时间线", ""])

        file_tree_chunks = []
        source_timeline = []
        for chunk_index, chunk in enumerate(source_chunks, 1):
            chunk_path = chunks_dir / _chunk_file_name(chunk_index, chunk)
            chunk_content = "\n".join(
                _markdown_chunk_lines(
                    source_name=source_name,
                    reference_text=chunk["reference_text"],
                    chunk_id=chunk["chunk_id"],
                    title=chunk["title"],
                    summary=chunk["summary"],
                    body=chunk["body"],
                    section_path=chunk["section_path"],
                )
            )
            _write_utf8_text(chunk_path, chunk_content)
            chunk_paths.append(str(chunk_path))
            rel_chunk_path = chunk_path.relative_to(root).as_posix()
            source_lines.append(f"- [{chunk['title']}](chunks/{chunk_path.name})")
            index_lines.append(f"- [{chunk['title']}]({rel_chunk_path})：{chunk['reference_text']}")
            overview_lines.append(f"- [{chunk['title']}]({rel_chunk_path})")
            if is_video_source and include_video_timeline:
                timeline_entry = _video_timeline_entry(chunk, rel_chunk_path)
                source_timeline.append(timeline_entry)
                timeline_index.append(timeline_entry)
                timeline_line = f"- [{chunk['reference_text']}]({rel_chunk_path})：{chunk['title']}；{chunk['summary']}"
                index_lines.append(timeline_line)
                source_lines.append(timeline_line)
            file_tree_chunks.append({
                "title": chunk["title"],
                "summary": chunk["summary"],
                "reference_text": chunk["reference_text"],
                "path": str(chunk_path),
                "relative_path": rel_chunk_path,
                "split_part": chunk["split_part"],
                "split_total": chunk["split_total"],
                "chunk_id": chunk["chunk_id"],
                "status": chunk["status"],
                "degraded_capabilities": chunk["degraded_capabilities"],
                "failed_segment_count": chunk["failed_segment_count"],
            })
        index_lines.append("")
        source_lines.extend(["", "## 异常与降级说明", ""])
        degraded_rows = [row for row in source_rows if _rag_extraction_status(row) == "partial_success"]
        if degraded_rows:
            for row in degraded_rows:
                source_lines.append(f"- {_row_title(row, 0)}：{', '.join(_rag_degraded_capabilities(row)) or 'partial_success'}")
        else:
            source_lines.append("- 无")
        if is_video_source:
            source_lines.append(f"- 转写失败片段数：{sum(chunk.get('failed_segment_count') or 0 for chunk in source_chunks)}")
        _write_utf8_text(source_path, "\n".join(source_lines))
        source_paths.append(str(source_path))
        source_tree = {
            "source_type": source_key[0],
            "source_name": source_name,
            "source_path": str(source_path),
            "relative_path": source_rel_path,
            "chunks_dir": chunks_dir.relative_to(root).as_posix(),
            "chunks": file_tree_chunks,
        }
        if is_video_source and include_video_timeline:
            source_tree["timeline"] = source_timeline
        file_tree_sources.append(source_tree)
        overview_lines.append("")

    overview_lines.extend(["## 核心内容", ""])
    for index, row in enumerate(semantic_rows, 1):
        overview_lines.extend([
            f"### {index}. {_row_title(row, index)}",
            "",
            f"- 来源：{_source_label(_row_source(row))}",
        ])
        if row.get("unit_type"):
            overview_lines.append(f"- 类型：{_unit_type_label(row.get('unit_type'))}")
        overview_lines.extend([
            "",
            _markdown_chunk_body(row) if full_content_in_overview else _compact_text(_markdown_chunk_body(row), 600),
            "",
        ])

    overview_lines.extend([
        "## 分片索引",
        "",
        *[f"- [{chunk['title']}]({Path(path).relative_to(root).as_posix()})" for chunk, path in zip(enhanced_chunks, chunk_paths)],
        "",
        "## 异常与降级说明",
        "",
        f"- partial_success 分片数：{quality_metadata['partial_chunk_count']}",
        f"- 转写失败片段数：{quality_metadata['failure_breakdown']['transcription_failed_segments']}",
    ])

    ollama_status = "skipped"
    if enhance_with_ollama:
        ollama_status = "success" if ollama_stats["failed_chunks"] == 0 else "failed"

    manifest = {
        "schema_version": "envoy.markdown.bundle.v1",
        "title": title,
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "row_count": len(semantic_rows),
        "source_count": len(order),
        "chunk_count": len(chunk_paths),
        "video_source_count": sum(1 for source in file_tree_sources if "timeline" in source),
        "timeline_index": timeline_index,
        **semantic_metadata,
        **quality_metadata,
        **llm_metadata,
        "encoding": "utf-8",
        "sources": [
            {
                "source_type": source_key[0],
                "source_name": source_key[1],
                "source_path": source_paths[index],
            }
            for index, source_key in enumerate(order)
            if index < len(source_paths)
        ],
        "chunks": [
            {
                "chunk_id": chunk["chunk_id"],
                "title": chunk["title"],
                "summary": chunk["summary"],
                "source_name": chunk["source_name"],
                "section_path": chunk["section_path"],
                "reference_text": chunk["reference_text"],
                "path": chunk_paths[index],
                "split_part": chunk["split_part"],
                "split_total": chunk["split_total"],
            }
            for index, chunk in enumerate(enhanced_chunks)
            if index < len(chunk_paths)
        ],
        "ollama": {
            "enabled": ollama_stats["enabled"],
            "chunk_enhancement_status": ollama_status,
            "succeeded_chunks": ollama_stats["succeeded_chunks"],
            "failed_chunks": ollama_stats["failed_chunks"],
            "skipped_chunks": ollama_stats["skipped_chunks"],
            "errors": ollama_stats["errors"][:10],
        },
        "files": {
            "index": str(index_path),
            "overview": str(overview_path),
            "manifest": str(manifest_path),
            "sources": source_paths,
            "chunks": chunk_paths,
        },
        "file_tree": {
            "root": str(root),
            "index": str(index_path),
            "overview": str(overview_path),
            "manifest": str(manifest_path),
            "sources_dir": str(sources_dir),
            "sources": file_tree_sources,
        },
    }

    _write_utf8_text(index_path, "\n".join(index_lines))
    _write_utf8_text(overview_path, "\n".join(overview_lines))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    export_paths = [index_path, overview_path, manifest_path, *(Path(path) for path in source_paths), *(Path(path) for path in chunk_paths)]
    _finalize_export(db, project_id, task_id, "markdown", task_name, root, export_paths, semantic_rows)
    return {
        "root": str(root),
        "path": str(overview_path),
        "task_id": task_id,
        "count": len(chunk_paths),
        "paths": {
            "index": str(index_path),
            "overview": str(overview_path),
            "manifest": str(manifest_path),
            "sources": source_paths,
            "chunks": chunk_paths,
        },
    }


def export_markdown_from_video_summary(
    summary_rows: list[dict],
    out_dir: str,
    task_id: str,
    title: str = "Video Markdown Report",
    task_name: str | None = None,
    db=None,
    project_id: str | None = None,
    include_video_timeline: bool = True,
    include_video_summary_heading: bool = False,
):
    return export_markdown_bundle(
        summary_rows,
        out_dir,
        task_id,
        title=title,
        task_name=task_name,
        db=db,
        project_id=project_id,
        enhance_with_ollama=False,
        include_video_timeline=include_video_timeline,
        full_content_in_overview=True,
        include_video_summary_heading=include_video_summary_heading,
    )


def _video_library_slug(value: str) -> str:
    slug = slugify_name(value)
    return slug or "video-library"


def export_video_library(
    evidence_rows: list[dict],
    video_rows: list[dict],
    out_dir: str,
    library_id: str,
    title: str,
    task_id: str,
    project_id: str,
    topic_result: dict | None = None,
    failed_videos: list[dict] | None = None,
    db=None,
    output_formats: list[str] | None = None,
) -> dict:
    """Write a theme-first, progressively disclosed wiki source package."""
    library_root = Path(out_dir).resolve() / "video-libraries" / _video_library_slug(library_id)
    previous_manifest_path = library_root / "manifest.json"
    previous_manifest = {}
    if previous_manifest_path.is_file():
        try:
            loaded_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded_manifest, dict):
                previous_manifest = loaded_manifest
        except (OSError, json.JSONDecodeError):
            previous_manifest = {}
    previous_version = int(previous_manifest.get("library_version") or 0)
    library_version = previous_version + 1
    parent_version = previous_version or None
    if library_version > 1:
        first_version_root = library_root / "versions" / "v001"
        if not first_version_root.exists():
            first_version_root.mkdir(parents=True, exist_ok=True)
            for name in ("index.md", "videos.md", "evidence.jsonl", "manifest.json"):
                source = library_root / name
                if source.is_file():
                    shutil.copy2(source, first_version_root / name)
            for directory_name in ("themes", "evidence"):
                source_dir = library_root / directory_name
                if source_dir.is_dir():
                    shutil.copytree(source_dir, first_version_root / directory_name)
    root = library_root if library_version == 1 else library_root / "versions" / f"v{library_version:03d}"
    themes_dir = root / "themes"
    evidence_dir = root / "evidence"
    frames_dir = root / "assets" / "frames"
    root.mkdir(parents=True, exist_ok=True)
    themes_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.md"
    video_index_path = root / "videos.md"
    evidence_jsonl_path = root / "evidence.jsonl"
    manifest_path = root / "manifest.json"

    normalized_evidence = []
    evidence_by_id = {}
    video_by_id = {str(video["video_id"]): video for video in video_rows if video.get("video_id")}
    for row in evidence_rows:
        evidence_id = str(row.get("id") or "")
        content = _semantic_unit_export_content(row)
        if not evidence_id or not content or evidence_id in evidence_by_id:
            continue
        labels = _row_labels(row)
        video_id = str(labels.get("video_id") or labels.get("source_task_id") or row.get("asset_id") or "unknown-video")
        record = {
            "id": evidence_id,
            "video_id": video_id,
            "video_title": str(labels.get("video_title") or video_by_id.get(video_id, {}).get("title") or "Video"),
            "unit_type": str(row.get("unit_type") or "knowledge"),
            "title": _row_title(row, len(normalized_evidence) + 1),
            "source": str(labels.get("source") or "video"),
            "content": content,
            "frame_paths": [
                str(path)
                for path in (labels.get("frame_paths") or [])
                if str(path).strip()
            ],
        }
        frame_rel_paths = []
        for frame_index, frame_path in enumerate(record["frame_paths"]):
            source_frame = Path(frame_path)
            if not source_frame.is_file():
                continue
            target_name = f"{_video_library_slug(record['video_title'])}-{_video_library_slug(evidence_id)}-{frame_index + 1:02d}{source_frame.suffix.lower() or '.jpg'}"
            target_frame = frames_dir / target_name
            try:
                shutil.copy2(source_frame, target_frame)
            except OSError:
                continue
            frame_rel_paths.append(str(Path("assets") / "frames" / target_name))
        record["frame_rel_paths"] = frame_rel_paths
        normalized_evidence.append(record)
        evidence_by_id[evidence_id] = record

    generated_topics = (topic_result or {}).get("topics") or []
    previous_topic_titles = {
        str(item.get("title") or "").strip()
        for item in previous_manifest.get("topics", [])
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    }
    topics = []
    generated_topic_titles = {
        str(topic.get("title") or "").strip()
        for topic in generated_topics
        if isinstance(topic, dict) and str(topic.get("title") or "").strip()
    }
    if library_version > 1:
        for previous_topic in previous_manifest.get("topics", []):
            if not isinstance(previous_topic, dict):
                continue
            previous_title = str(previous_topic.get("title") or "").strip()
            previous_points = previous_topic.get("points")
            if previous_title and previous_title not in generated_topic_titles and isinstance(previous_points, list):
                topics.append({
                    "title": previous_title,
                    "summary": str(previous_topic.get("summary") or "").strip(),
                    "points": [
                        point for point in previous_points
                        if isinstance(point, dict)
                    ],
                })
    used_evidence = set()
    for topic in generated_topics:
        if not isinstance(topic, dict):
            continue
        previous_topic = next(
            (
                item for item in previous_manifest.get("topics", [])
                if isinstance(item, dict)
                and str(item.get("title") or "").strip() == str(topic.get("title") or "").strip()
            ),
            None,
        )
        points = [
            point for point in (previous_topic or {}).get("points", [])
            if isinstance(point, dict)
            and str(point.get("text") or "").strip()
            and any(str(ref) in evidence_by_id for ref in point.get("evidence_ids", []))
        ]
        for point in topic.get("points", []):
            if not isinstance(point, dict):
                continue
            refs = [str(ref) for ref in point.get("evidence_ids", []) if str(ref) in evidence_by_id]
            body = str(point.get("text") or "").strip()
            if body and refs:
                point_key = (body, tuple(refs))
                if point_key in {(item["text"], tuple(item["evidence_ids"])) for item in points}:
                    continue
                points.append({"text": body, "evidence_ids": refs})
                used_evidence.update(refs)
        topic_title = str(topic.get("title") or "").strip()
        if topic_title and points:
            merged_topic = {
                "title": topic_title,
                "summary": str(topic.get("summary") or (previous_topic or {}).get("summary") or "").strip(),
                "points": points,
            }
            topics = [
                item for item in topics
                if item.get("title") != topic_title
            ]
            topics.append(merged_topic)

    topic_status = (topic_result or {}).get("status") or "skipped"
    if not topics and normalized_evidence:
        by_type: dict[str, list[dict]] = {}
        for evidence in normalized_evidence:
            by_type.setdefault(evidence["unit_type"], []).append(evidence)
        if len(by_type) > 1:
            by_type.pop("transcript", None)
        topics = [
            {
                "title": _unit_type_label(unit_type),
                "summary": (
                    f"按内容类型自动归档，共 {len(items)} 条证据；尚未生成 AI 跨视频归纳。"
                    f" {_compact_text(_semantic_unit_export_content(items[0]), 600)}"
                    if items else
                    f"按内容类型自动归档，共 {len(items)} 条证据；尚未生成 AI 跨视频归纳。"
                ),
                "points": [
                    {
                        "text": item["title"],
                        "evidence_ids": [item["id"]],
                    }
                    for item in items
                ],
            }
            for unit_type, items in sorted(by_type.items())
        ]
        topic_status = "fallback"

    topic_manifest = []
    topic_files: list[Path] = []
    for topic in topics:
        slug = _video_library_slug(topic["title"])
        topic_path = themes_dir / f"{slug}.md"
        lines = [f"# {topic['title']}", ""]
        if topic.get("summary"):
            lines.extend([topic["summary"], ""])
        lines.extend(["## 知识点", ""])
        referenced_videos: dict[str, dict] = {}
        for point in topic["points"]:
            lines.extend([f"### {point['text']}", "", "来源证据："])
            for evidence_id in point["evidence_ids"]:
                evidence = evidence_by_id[evidence_id]
                evidence_rel = Path("..") / "evidence" / f"{_video_library_slug(evidence['video_title'])}-{_video_library_slug(evidence_id)}.md"
                lines.append(
                    f"- [{evidence['video_title']} · {evidence['title']}]({evidence_rel.as_posix()}) "
                    f"(evidence_id: `{evidence_id}`)"
                )
                for frame_rel_path in evidence.get("frame_rel_paths", []):
                    lines.append(f"![关键帧](../{frame_rel_path})")
                referenced_videos[evidence["video_id"]] = video_by_id.get(
                    evidence["video_id"],
                    {"video_id": evidence["video_id"], "title": evidence["video_title"], "source_url": ""},
                )
            lines.append("")
        lines.extend(["## 涉及视频", ""])
        for video in referenced_videos.values():
            lines.append(f"- [{video.get('title') or video.get('video_id')}]({video.get('source_url') or '#'})")
        _write_utf8_text(topic_path, "\n".join(lines))
        topic_files.append(topic_path)
        topic_summary = topic.get("summary", "")
        topic_evidence = [
            evidence_by_id[evidence_id]
            for point in topic["points"]
            for evidence_id in point["evidence_ids"]
            if evidence_id in evidence_by_id
        ]
        if topic_evidence and all(item["unit_type"] == "transcript" for item in topic_evidence):
            topic_summary = f"{topic_summary} {_compact_text(topic_evidence[0]['content'], 600)}".strip()
        topic_manifest.append({
            "title": topic["title"],
            "summary": topic_summary,
            "path": str(topic_path),
            "relative_path": topic_path.relative_to(root).as_posix(),
            "video_ids": list(referenced_videos.keys()),
            "evidence_ids": sorted({
                evidence_id
                for point in topic["points"]
                for evidence_id in point["evidence_ids"]
            }),
            "points": topic["points"],
        })

    evidence_files: list[Path] = []
    evidence_lines = []
    for evidence in normalized_evidence:
        video_slug = _video_library_slug(evidence["video_title"])
        evidence_slug = _video_library_slug(evidence["id"])
        evidence_path = evidence_dir / f"{video_slug}-{evidence_slug}.md"
        video = video_by_id.get(evidence["video_id"], {})
        _write_utf8_text(evidence_path, "\n".join([
            f"# {evidence['title']}",
            "",
            f"- Video: {evidence['video_title']}",
            f"- Source: {video.get('source_url') or 'unknown'}",
            f"- Evidence ID: `{evidence['id']}`",
            f"- Type: {evidence['unit_type']}",
            "",
            "## 内容",
            "",
            evidence["content"],
            "",
            *[
                f"![关键帧](../{frame_rel_path})"
                for frame_rel_path in evidence.get("frame_rel_paths", [])
            ],
            "",
        ]))
        evidence_files.append(evidence_path)
        evidence_lines.append(json.dumps(evidence, ensure_ascii=False))
    _write_utf8_text(evidence_jsonl_path, "\n".join(evidence_lines))

    video_lines = ["# 视频来源目录", "", f"共 {len(video_rows)} 条视频来源。", ""]
    for video in video_rows:
        video_lines.extend([
            f"## {video.get('title') or video.get('video_id')}",
            "",
            f"- 来源：{video.get('source_url') or 'unknown'}",
            f"- 状态：{video.get('status') or 'success'}",
            f"- 证据数：{video.get('evidence_count', 0)}",
            "",
        ])
    for failed in failed_videos or []:
        video_lines.extend([
            f"## 处理失败：{failed.get('title') or failed.get('source_url')}",
            "",
            f"- 来源：{failed.get('source_url')}",
            f"- 错误：{failed.get('error') or 'unknown error'}",
            "",
        ])
    _write_utf8_text(video_index_path, "\n".join(video_lines))

    index_lines = [
        f"# {title}",
        "",
        "> 这是渐进式披露入口。先按主题查找；仅打开相关主题页和证据页。完整证据集见 `evidence.jsonl`。",
        "",
        f"- 视频数：{len(video_rows) + len(failed_videos or [])}",
        f"- 已处理视频：{len(video_rows)}",
        f"- 失败视频：{len(failed_videos or [])}",
        f"- 知识证据：{len(normalized_evidence)}",
        f"- 主题整理：{topic_status}",
        "",
        "## 主题索引",
        "",
    ]
    if topics:
        for item in topic_manifest:
            index_lines.append(
                f"- [{item['title']}]({item['relative_path']})：{item['summary']} "
                f"(视频 {len(item['video_ids'])} 条，证据 {len(item['evidence_ids'])} 条)"
            )
    else:
        index_lines.append("- 暂无可归类知识内容。")
    index_lines.extend([
        "",
        "## 来源与机器检索",
        "",
        f"- [视频来源目录]({video_index_path.name})",
        f"- [完整证据 JSONL]({evidence_jsonl_path.name})",
        f"- 结构清单：`{manifest_path.name}`",
    ])
    _write_utf8_text(index_path, "\n".join(index_lines))

    formats = {
        str(value).strip().lower()
        for value in (output_formats or ["markdown"])
        if str(value).strip()
    }
    html_path = None
    if "html" in formats or "both" in formats:
        html_sections = []
        for topic in topics:
            html_sections.extend([
                f"<section><h2>{escape(topic['title'])}</h2>",
                f"<p class=\"summary\">{escape(topic.get('summary') or '')}</p>",
            ])
            for point in topic["points"]:
                html_sections.append(f"<article><h3>{escape(point['text'])}</h3>")
                for evidence_id in point["evidence_ids"]:
                    evidence = evidence_by_id[evidence_id]
                    html_sections.append(
                        f"<p class=\"source\">{escape(evidence['video_title'])} · "
                        f"{escape(evidence['title'])}</p>"
                    )
                    for frame_rel_path in evidence.get("frame_rel_paths", []):
                        html_sections.append(
                            f'<img src="{escape(frame_rel_path)}" alt="关键帧" loading="lazy">'
                        )
                html_sections.append("</article>")
            html_sections.append("</section>")
        html_path = root / "index.html"
        _write_utf8_text(html_path, "\n".join([
            "<!doctype html>",
            '<html lang="zh-CN"><head><meta charset="utf-8">',
            f"<title>{escape(title)}</title>",
            "<style>body{margin:0;color:#202124;background:#fff;font:15px/1.8 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}main{max-width:920px;margin:0 auto;padding:48px 28px}h1{font-size:30px;margin:0 0 8px}h2{margin-top:42px;border-bottom:1px solid #e5e7eb;padding-bottom:8px}h3{font-size:18px;margin-bottom:4px}.summary,.source{color:#667085}article{margin:22px 0 30px}img{display:block;max-width:100%;max-height:420px;margin:12px 0;border:1px solid #e5e7eb;border-radius:6px}</style>",
            "</head><body><main>",
            f"<h1>{escape(title)}</h1>",
            "<p class=\"summary\">主题优先的视觉知识库预览</p>",
            *html_sections,
            "</main></body></html>",
        ]))

    manifest = {
        "schema_version": "envoy.video.library.v1",
        "library_id": library_id,
        "library_version": library_version,
        "parent_version": parent_version,
        "append_mode": "incremental" if parent_version else "initial",
        "title": title,
        "project_id": project_id,
        "source_task_id": task_id,
        "video_count": len(video_rows),
        "failed_video_count": len(failed_videos or []),
        "evidence_count": len(normalized_evidence),
        "topic_status": topic_status,
        "changed_topics": [
            topic["title"] for topic in topics
            if topic["title"] not in previous_topic_titles
            or any(
                evidence_id not in {
                    old_id
                    for old_topic in previous_manifest.get("topics", [])
                    if isinstance(old_topic, dict)
                    for old_id in (old_topic.get("evidence_ids") or [])
                }
                for point in topic.get("points", [])
                for evidence_id in point.get("evidence_ids", [])
            )
        ],
        "new_evidence_ids": [
            evidence["id"]
            for evidence in normalized_evidence
            if evidence["id"] not in {
                old_evidence.get("id")
                for old_evidence in previous_manifest.get("evidence", [])
                if isinstance(old_evidence, dict)
            }
        ],
        "conflicts": [],
        "topics": topic_manifest,
        "videos": video_rows,
        "failed_videos": failed_videos or [],
        "evidence": normalized_evidence,
        "output_formats": sorted(formats),
        "files": {
            "index": str(index_path),
            "videos": str(video_index_path),
            "evidence_jsonl": str(evidence_jsonl_path),
            "themes": [str(path) for path in topic_files],
            "evidence_pages": [str(path) for path in evidence_files],
            "html": str(html_path) if html_path else None,
            "frames": [str(path) for path in frames_dir.glob("*") if path.is_file()],
        },
    }
    _write_utf8_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    files = [index_path, video_index_path, evidence_jsonl_path, manifest_path, *topic_files, *evidence_files]
    if html_path:
        files.append(html_path)
    files.extend(path for path in frames_dir.glob("*") if path.is_file())

    latest_index_path = library_root / "index.md"
    latest_manifest_path = library_root / "manifest.json"
    latest_evidence_jsonl_path = library_root / "evidence.jsonl"
    latest_video_index_path = library_root / "videos.md"
    if library_version > 1:
        version_relative_index = index_path.relative_to(library_root).as_posix()
        history_lines = [
            f"# {title}",
            "",
            "> 这是知识库最新版本入口。追加视频、图片或文件后，旧版本仍保留在 `versions/`。",
            "",
            f"- 当前版本：v{library_version:03d}",
            f"- [打开当前版本]({version_relative_index})",
            "",
            "## 版本历史",
            "",
        ]
        for version in range(library_version, 0, -1):
            version_path = (
                library_root / "index.md"
                if version == 1
                else library_root / "versions" / f"v{version:03d}" / "index.md"
            )
            if version_path.is_file():
                history_lines.append(
                    f"- [v{version:03d}]({version_path.relative_to(library_root).as_posix()})"
                )
        _write_utf8_text(latest_index_path, "\n".join(history_lines))
        latest_manifest = dict(manifest)
        latest_manifest["latest_version"] = library_version
        latest_manifest["latest_version_path"] = version_relative_index
        latest_manifest["version_history"] = [
            f"v{version:03d}" for version in range(1, library_version + 1)
        ]
        _write_utf8_text(latest_manifest_path, json.dumps(latest_manifest, ensure_ascii=False, indent=2))
        _write_utf8_text(latest_evidence_jsonl_path, evidence_jsonl_path.read_text(encoding="utf-8"))
        _write_utf8_text(latest_video_index_path, video_index_path.read_text(encoding="utf-8"))
        export_root = library_root
        export_paths = [
            latest_index_path,
            latest_manifest_path,
            latest_evidence_jsonl_path,
            latest_video_index_path,
            *files,
        ]
    else:
        export_root = root
        export_paths = files

    _finalize_export(
        db,
        project_id,
        task_id,
        "video_library",
        f"video-library-{library_id}",
        export_root,
        export_paths,
        normalized_evidence,
    )
    archive_path = Path(
        shutil.make_archive(
            str(library_root),
            "zip",
            root_dir=str(library_root),
        )
    )
    return {
        "root": str(library_root),
        "path": str(latest_index_path),
        "manifest_path": str(latest_manifest_path),
        "library_version": library_version,
        "files": [str(path) for path in export_paths],
        "html_path": str(html_path) if html_path else None,
        "archive_path": str(archive_path),
    }


def _relative_file(path: Path, root: Path):
    return path.relative_to(root).as_posix()


def _delivery_instruction_rows(rows: list[dict]):
    instruction_rows = []
    for row in rows:
        content = _semantic_unit_export_content(row)
        if not content:
            continue
        instruction_rows.append({
            "instruction": row.get("title") or "总结该素材内容",
            "input": content,
            "output": json.dumps(row.get("labels") or {}, ensure_ascii=False),
        })
    return instruction_rows


def _delivery_content_rows(rows: list[dict]):
    derived_media_asset_ids = {
        row.get("asset_id")
        for row in rows
        if row.get("asset_id") and _row_source(row) in {"media_transcript", "media_ocr"}
    }
    if not derived_media_asset_ids:
        return rows
    return [
        row
        for row in rows
        if not (row.get("asset_id") in derived_media_asset_ids and _row_source(row) == "media_asset")
    ]


def _write_delivery_readme(path: Path, title: str, manifest_name: str):
    lines = [
        f"# {title}",
        "",
        "该目录是 Envoy 长视频/长文件商业交付包，可直接用于知识库导入、agent 初始化、训练数据整理和人工阅读。",
        "",
        "## 推荐使用顺序",
        "",
        "1. 将 `knowledge_base/rag_corpus.jsonl` 导入 RAG 或知识库系统。",
        "2. 将 `readable/index.md` 和 `readable/sources/*.md` 作为可读素材导入长上下文工具。",
        "3. 将 `agent/import_manifest.json` 提供给 agent 编排器，按清单读取能力描述和语料。",
        "4. 将 `training/instruction_train.jsonl` 作为训练或样本生成的起点。",
        "",
        "## 关键索引",
        "",
        f"- `{manifest_name}`：交付包总清单",
        "- `indexes/chunk_index.json`：分片索引",
        "- `indexes/source_index.json`：来源索引",
        "- `indexes/timeline_index.json`：视频时间线索引",
        "- `indexes/quality_report.json`：降级与质量报告",
    ]
    _write_utf8_text(path, "\n".join(lines))


def export_delivery_package(
    rows: list[dict],
    out_dir: str,
    task_id: str,
    title: str = "Envoy Delivery Package",
    task_name: str | None = None,
    db=None,
    project_id: str | None = None,
):
    clean_rows = _dedupe_rows(rows)
    quality_rows = [row for row in clean_rows if _semantic_unit_has_export_content(row)]
    semantic_rows = _delivery_content_rows(quality_rows)
    semantic_metadata = _semantic_source_metadata(quality_rows)
    quality_metadata = _markdown_manifest_quality(quality_rows)
    chunks = build_markdown_chunks(semantic_rows)
    rag_rows = [row for row in (_rag_document_row(row) for row in semantic_rows) if row is not None]
    instruction_rows = _delivery_instruction_rows(semantic_rows)

    root = _resolve_root(out_dir, "delivery", task_id, task_name)
    readable_dir = build_subdir(root, "readable")
    readable_sources_dir = build_subdir(readable_dir, "sources")
    knowledge_dir = build_subdir(root, "knowledge_base")
    knowledge_chunks_dir = build_subdir(knowledge_dir, "chunks")
    training_dir = build_subdir(root, "training")
    agent_dir = build_subdir(root, "agent")
    indexes_dir = build_subdir(root, "indexes")

    readme_path = root / "README.md"
    manifest_path = root / "delivery_manifest.json"
    readable_index_path = readable_dir / "index.md"
    rag_corpus_path = knowledge_dir / "rag_corpus.jsonl"
    documents_path = knowledge_dir / "documents.jsonl"
    train_path = training_dir / "instruction_train.jsonl"
    dataset_card_path = training_dir / "dataset_card.md"
    skill_path = agent_dir / "skill.md"
    skill_manifest_path = agent_dir / "skill.json"
    import_manifest_path = agent_dir / "import_manifest.json"
    chunk_index_path = indexes_dir / "chunk_index.json"
    source_index_path = indexes_dir / "source_index.json"
    timeline_index_path = indexes_dir / "timeline_index.json"
    quality_report_path = indexes_dir / "quality_report.json"

    _write_delivery_readme(readme_path, title, manifest_path.name)
    build_dataset_jsonl(rag_rows, str(rag_corpus_path))
    build_dataset_jsonl(rag_rows, str(documents_path))
    build_dataset_jsonl(instruction_rows, str(train_path))

    readable_lines = [
        f"# {title}",
        "",
        "## 导入入口",
        "",
        "- RAG 语料：`../knowledge_base/rag_corpus.jsonl`",
        "- Agent 导入清单：`../agent/import_manifest.json`",
        "- 分片索引：`../indexes/chunk_index.json`",
        "",
        "## 分片目录",
        "",
    ]
    chunk_index_items = []
    timeline_items = []
    chunk_paths = []
    for index, chunk in enumerate(chunks, 1):
        chunk_path = knowledge_chunks_dir / _chunk_file_name(index, chunk)
        chunk_content = "\n".join(_markdown_chunk_lines(
            source_name=chunk["source_name"],
            reference_text=chunk["reference_text"],
            chunk_id=chunk["chunk_id"],
            title=chunk["title"],
            summary=chunk["summary"],
            body=chunk["body"],
            section_path=chunk["section_path"],
        ))
        _write_utf8_text(chunk_path, chunk_content)
        chunk_paths.append(chunk_path)
        relative_chunk_path = _relative_file(chunk_path, root)
        readable_lines.append(f"- [{chunk['title']}](../{relative_chunk_path})：{chunk['reference_text']}")
        chunk_item = {
            "chunk_id": chunk["chunk_id"],
            "title": chunk["title"],
            "summary": chunk["summary"],
            "source_name": chunk["source_name"],
            "source_type": chunk["source_type"],
            "asset_id": chunk.get("asset_id"),
            "project_id": chunk.get("project_id"),
            "unit_type": chunk.get("unit_type"),
            "section_path": chunk["section_path"],
            "reference_text": chunk["reference_text"],
            "path": relative_chunk_path,
            "status": chunk["status"],
            "degraded_capabilities": chunk["degraded_capabilities"],
            "failed_segment_count": chunk["failed_segment_count"],
            "split_part": chunk["split_part"],
            "split_total": chunk["split_total"],
        }
        chunk_index_items.append(chunk_item)
        if _is_video_source_type(chunk.get("source_type")) or _row_time_range({"labels": chunk.get("labels") or {}}):
            timeline_items.append(_video_timeline_entry(chunk, relative_chunk_path))
    _write_utf8_text(readable_index_path, "\n".join(readable_lines))

    order, grouped = _group_rows_by_markdown_source(semantic_rows)
    source_index_items = []
    source_paths = []
    for source_number, source_key in enumerate(order, 1):
        source_rows = grouped.get(source_key, [])
        if not source_rows:
            continue
        source_name = source_key[1]
        source_path = readable_sources_dir / f"{source_number:02d}-{slugify_name(source_name)}.md"
        lines = [
            f"# {source_name}",
            "",
            f"- 来源类型：{_source_label(source_key[0])}",
            f"- 素材 ID：{source_rows[0].get('asset_id') or '未标注'}",
            "",
            "## 内容片段",
            "",
        ]
        for row in source_rows:
            lines.append(f"- {' / '.join(_section_path(row))}：{_markdown_chunk_summary(_markdown_chunk_body(row))}")
        _write_utf8_text(source_path, "\n".join(lines))
        source_paths.append(source_path)
        source_index_items.append({
            "source_type": source_key[0],
            "source_name": source_name,
            "asset_id": source_rows[0].get("asset_id"),
            "path": _relative_file(source_path, root),
            "chunk_count": sum(1 for item in chunk_index_items if item["source_name"] == source_name),
        })

    dataset_card_lines = [
        f"# {title} Dataset Card",
        "",
        f"- Project ID: {project_id or 'unknown'}",
        f"- Source task ID: {task_id}",
        f"- RAG rows: {len(rag_rows)}",
        f"- Instruction rows: {len(instruction_rows)}",
        f"- Partial chunks: {quality_metadata['partial_chunk_count']}",
    ]
    _write_utf8_text(dataset_card_path, "\n".join(dataset_card_lines))

    skill_lines = [
        f"# {title} Agent Skill",
        "",
        "## Purpose",
        "",
        "Use this package to answer questions over the processed long video or long document corpus with traceable source references.",
        "",
        "## Inputs",
        "",
        "- `knowledge_base/rag_corpus.jsonl` for retrieval",
        "- `indexes/chunk_index.json` for source references",
        "- `indexes/quality_report.json` for degraded segments",
    ]
    _write_utf8_text(skill_path, "\n".join(skill_lines))
    skill_manifest = {
        "schema_version": "envoy.skill.v1",
        "title": title,
        "source_task_id": task_id,
        "project_id": project_id,
        "files": {
            "markdown": _relative_file(skill_path, root),
            "import_manifest": _relative_file(import_manifest_path, root),
            "rag_corpus": _relative_file(rag_corpus_path, root),
        },
    }
    skill_manifest_path.write_text(json.dumps(skill_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    quality_report = {
        "schema_version": "envoy.quality.v1",
        "source_task_id": task_id,
        "project_id": project_id,
        "row_count": len(semantic_rows),
        "chunk_count": len(chunk_index_items),
        **quality_metadata,
        **semantic_metadata,
    }
    quality_report_path.write_text(json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8")
    chunk_index = {
        "schema_version": "envoy.chunk_index.v1",
        "chunk_count": len(chunk_index_items),
        "chunks": chunk_index_items,
    }
    chunk_index_path.write_text(json.dumps(chunk_index, ensure_ascii=False, indent=2), encoding="utf-8")
    source_index = {
        "schema_version": "envoy.source_index.v1",
        "source_count": len(source_index_items),
        "sources": source_index_items,
    }
    source_index_path.write_text(json.dumps(source_index, ensure_ascii=False, indent=2), encoding="utf-8")
    timeline_index = {
        "schema_version": "envoy.timeline_index.v1",
        "timeline_count": len(timeline_items),
        "items": timeline_items,
    }
    timeline_index_path.write_text(json.dumps(timeline_index, ensure_ascii=False, indent=2), encoding="utf-8")

    import_manifest = {
        "schema_version": "envoy.agent.import.v1",
        "title": title,
        "source_task_id": task_id,
        "project_id": project_id,
        "compatible_targets": [
            "generic_rag_jsonl",
            "markdown_knowledge_base",
            "agent_skill_context",
            "instruction_tuning_seed",
        ],
        "target_profiles": {
            "generic_rag_jsonl": {
                "primary_file": _relative_file(rag_corpus_path, root),
                "format": "jsonl",
                "required_fields": ["doc_id", "title", "text", "metadata"],
                "metadata_index": _relative_file(chunk_index_path, root),
                "quality_index": _relative_file(quality_report_path, root),
            },
            "markdown_knowledge_base": {
                "format": "markdown",
                "file_globs": [
                    _relative_file(readable_index_path, root),
                    "readable/sources/*.md",
                    "knowledge_base/chunks/*.md",
                ],
                "source_index": _relative_file(source_index_path, root),
                "chunk_index": _relative_file(chunk_index_path, root),
            },
            "agent_skill_context": {
                "primary_file": _relative_file(skill_path, root),
                "format": "markdown",
                "supporting_files": [
                    _relative_file(import_manifest_path, root),
                    _relative_file(quality_report_path, root),
                    _relative_file(chunk_index_path, root),
                ],
            },
            "instruction_tuning_seed": {
                "primary_file": _relative_file(train_path, root),
                "format": "jsonl",
                "required_fields": ["instruction", "input", "output"],
                "dataset_card": _relative_file(dataset_card_path, root),
            },
        },
        "recommended_import_order": [
            {
                "path": _relative_file(rag_corpus_path, root),
                "format": "jsonl",
                "use": "rag_knowledge_base",
                "index": _relative_file(chunk_index_path, root),
            },
            {
                "path": _relative_file(readable_index_path, root),
                "format": "markdown",
                "use": "readable_context",
                "index": _relative_file(source_index_path, root),
            },
            {
                "path": _relative_file(skill_path, root),
                "format": "markdown",
                "use": "agent_instruction",
                "index": _relative_file(quality_report_path, root),
            },
            {
                "path": _relative_file(train_path, root),
                "format": "jsonl",
                "use": "training_seed",
                "index": _relative_file(quality_report_path, root),
            },
        ],
    }
    import_manifest_path.write_text(json.dumps(import_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "schema_version": "envoy.delivery.v1",
        "title": title,
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "encoding": "utf-8",
        "directories": {
            "readable": "readable",
            "knowledge_base": "knowledge_base",
            "training": "training",
            "agent": "agent",
            "indexes": "indexes",
        },
        "counts": {
            "sources": len(source_index_items),
            "chunks": len(chunk_index_items),
            "rag_rows": len(rag_rows),
            "instruction_rows": len(instruction_rows),
        },
        "files": {
            "readme": _relative_file(readme_path, root),
            "readable_index": _relative_file(readable_index_path, root),
            "rag_corpus": _relative_file(rag_corpus_path, root),
            "documents": _relative_file(documents_path, root),
            "instruction_train": _relative_file(train_path, root),
            "skill": _relative_file(skill_path, root),
            "import_manifest": _relative_file(import_manifest_path, root),
            "chunk_index": _relative_file(chunk_index_path, root),
            "source_index": _relative_file(source_index_path, root),
            "timeline_index": _relative_file(timeline_index_path, root),
            "quality_report": _relative_file(quality_report_path, root),
        },
        **quality_metadata,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    export_paths = [
        readme_path,
        manifest_path,
        readable_index_path,
        rag_corpus_path,
        documents_path,
        train_path,
        dataset_card_path,
        skill_path,
        skill_manifest_path,
        import_manifest_path,
        chunk_index_path,
        source_index_path,
        timeline_index_path,
        quality_report_path,
        *chunk_paths,
        *source_paths,
    ]
    _finalize_export(db, project_id, task_id, "delivery", task_name, root, export_paths, semantic_rows)
    return {
        "export_type": "delivery",
        "root": str(root),
        "path": str(manifest_path),
        "task_id": task_id,
        "count": len(chunk_index_items),
        "paths": {
            "manifest": str(manifest_path),
            "readme": str(readme_path),
            "readable_index": str(readable_index_path),
            "rag_corpus": str(rag_corpus_path),
            "documents": str(documents_path),
            "instruction_train": str(train_path),
            "skill": str(skill_path),
            "skill_manifest": str(skill_manifest_path),
            "import_manifest": str(import_manifest_path),
            "chunk_index": str(chunk_index_path),
            "source_index": str(source_index_path),
            "timeline_index": str(timeline_index_path),
            "quality_report": str(quality_report_path),
            "chunks": [str(path) for path in chunk_paths],
            "sources": [str(path) for path in source_paths],
        },
    }


def build_auto_payload_from_project(db, project_id: str, export_type: str):
    data = _auto_rows_from_assets(db, project_id)
    if export_type in ("sft", "instruction", "preference"):
        rows = []
        for su in data["semantic_units"]:
            content = _semantic_unit_export_content(su)
            if not content:
                continue
            rows.append({
                "instruction": su.get("title") or "总结该内容",
                "input": content,
                "output": json.dumps(su.get("labels") or {}, ensure_ascii=False),
            })
        for sample in data["samples"]:
            rows.append({
                "instruction": sample.get("sample_type", "qa"),
                "input": sample.get("input_text") or "",
                "output": sample.get("output_text") or "",
            })
        return rows
    if export_type == "rag":
        rows = []
        for su in data["semantic_units"]:
            content = _semantic_unit_export_content(su)
            if not content:
                continue
            raw_content = su.get("content") or ""
            metadata = {
                **(su.get("labels") or {}),
                "semantic_unit_id": su.get("id"),
                "project_id": su.get("project_id"),
                "asset_id": su.get("asset_id"),
                "segment_id": su.get("segment_id"),
                "title": su.get("title"),
                "unit_type": su.get("unit_type"),
                "confidence": su.get("confidence"),
                "content_format": "json" if _parse_json_content(raw_content) is not None else "text",
            }
            metadata.update(_ollama_rag_metadata(content))
            rows.append({"text": content, "metadata": metadata})
        return rows
    if export_type == "skill":
        semantic_units = [row for row in data["semantic_units"] if _semantic_unit_has_export_content(row)]
        visible_ids = {
            *(row.get("id") for row in semantic_units if row.get("id")),
            *(row.get("id") for row in data["samples"] if row.get("id")),
        }
        trace_links = [row for row in data["trace_links"] if _trace_link_is_exportable(row, visible_ids)]
        return semantic_units + data["samples"] + trace_links
    if export_type in ("markdown", "delivery"):
        semantic_units = [row for row in data["semantic_units"] if _semantic_unit_has_export_content(row)]
        visible_ids = {
            *(row.get("id") for row in semantic_units if row.get("id")),
            *(row.get("id") for row in data["samples"] if row.get("id")),
        }
        trace_links = [row for row in data["trace_links"] if _trace_link_is_exportable(row, visible_ids)]
        return semantic_units + data["samples"] + trace_links
    return []
