from pathlib import Path
import json
from uuid import uuid4

from app.core.config import EXPORT_DIR
from app.models.generated_sample import GeneratedSample
from app.models.artifact import Artifact
from app.models.asset import Asset
from app.models.processing_task import ProcessingTask
from app.models.semantic_unit import SemanticUnit
from app.models.trace_link import TraceLink
from app.schemas.artifact import ArtifactCreate
from app.services.crud_service import create_artifact, create_export_record
from app.services.export_paths import build_export_root, build_subdir
from app.services.export_service import build_dataset_jsonl, build_markdown_report
from app.services.ollama_service import generate_with_ollama, parse_ollama_json_object


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
    return json.dumps(parsed, ensure_ascii=False)


def _row_title(row: dict, index: int):
    return str(row.get("title") or row.get("sample_type") or row.get("relation_type") or f"片段 {index}").strip()


def _row_source(row: dict):
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return labels.get("source") or metadata.get("source") or row.get("source_type") or row.get("record_type")


def _row_body(row: dict):
    for key in ("content", "text", "summary", "output_text", "input_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _readable_structured_content(value)
    useful = []
    for key, value in row.items():
        if key in {"id", "project_id", "asset_id", "segment_id", "labels", "metadata"}:
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


def _auto_rows_from_assets(db, project_id: str, task_name: str | None = None):
    semantic_units = db.query(SemanticUnit).filter(SemanticUnit.project_id == project_id).order_by(SemanticUnit.created_at.desc()).all()
    samples = db.query(GeneratedSample).filter(GeneratedSample.project_id == project_id).order_by(GeneratedSample.created_at.desc()).all()
    assets = db.query(Asset).filter(Asset.project_id == project_id).all()
    tasks = db.query(ProcessingTask).filter(ProcessingTask.project_id == project_id).all()
    artifacts = db.query(Artifact).filter(Artifact.project_id == project_id).all()
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


def _ollama_rag_metadata(text: str):
    result = generate_with_ollama(
        "Extract RAG metadata from the source. Return JSON with summary, keywords, and usage.",
        text or "",
    )
    metadata = _ollama_request_metadata(result)
    if result.get("status") != "success":
        return metadata

    parsed = parse_ollama_json_object(result.get("text", ""))
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

    parsed = parse_ollama_json_object(result.get("text", ""))
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

    parsed = parse_ollama_json_object(result.get("text", ""))
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
    semantic_metadata = _semantic_source_metadata(clean_rows)
    root = _resolve_root(out_dir, "rag", task_id, task_name)
    data_dir = build_subdir(root, "rag")
    out_path = data_dir / "rag_corpus.jsonl"
    manifest_path = data_dir / "rag_manifest.json"
    build_dataset_jsonl(clean_rows, str(out_path))
    manifest = {
        "schema_version": "envoy.rag.v1",
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "row_count": len(clean_rows),
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


def export_markdown_from_video_summary(summary_rows: list[dict], out_dir: str, task_id: str, title: str = "Video Markdown Report", task_name: str | None = None, db=None, project_id: str | None = None):
    semantic_metadata = _semantic_source_metadata(summary_rows)
    root = _resolve_root(out_dir, "markdown", task_id, task_name)
    md_dir = build_subdir(root, "markdown")
    out_path = md_dir / "video_summary.md"
    manifest_path = md_dir / "markdown_manifest.json"
    llm_metadata = _ollama_markdown_summary(summary_rows)
    content = []
    if llm_metadata.get("llm_summary"):
        content.extend(["## AI 摘要", "", str(llm_metadata["llm_summary"]), ""])
    content.extend([
        "## 视频转化摘要",
        "",
        "本报告由 Envoy 根据项目内的文本、转写、OCR、样本和追溯信息自动生成，用于快速审查素材是否已经形成可复用 AI 素材。",
        "",
        "## 素材概览",
        "",
        *_source_overview_lines(semantic_metadata.get("source_counts", {})),
        "",
        "## 核心内容",
        "",
        *_semantic_markdown_lines(summary_rows),
    ])
    build_markdown_report(title, "\n".join(content), str(out_path))
    manifest = {
        "schema_version": "envoy.markdown.v1",
        "title": title,
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "row_count": len(summary_rows),
        **semantic_metadata,
        "files": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
        **llm_metadata,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _finalize_export(db, project_id, task_id, "markdown", task_name, root, [out_path, manifest_path], summary_rows)
    return {
        "root": str(root),
        "path": str(out_path),
        "task_id": task_id,
        "paths": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
    }


def build_auto_payload_from_project(db, project_id: str, export_type: str):
    data = _auto_rows_from_assets(db, project_id)
    if export_type in ("sft", "instruction", "preference"):
        rows = []
        for su in data["semantic_units"]:
            rows.append({
                "instruction": su.get("title") or "总结该内容",
                "input": su.get("content") or "",
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
            raw_content = su.get("content") or ""
            content = _readable_structured_content(raw_content)
            if not content:
                continue
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
        return data["semantic_units"] + data["samples"] + data["trace_links"]
    if export_type == "markdown":
        return data["semantic_units"] + data["samples"] + data["trace_links"]
    return []
