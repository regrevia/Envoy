from pathlib import Path
import json

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


def _finalize_export(db, project_id: str | None, task_id: str, export_type: str, task_name: str | None, root: Path, paths: list[Path]):
    if db and project_id:
        _write_record(db, project_id, task_id, export_type, task_name, root, [str(path) for path in paths])
        _write_artifacts(db, project_id, export_type, paths)


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
    _finalize_export(db, project_id, task_id, "sft", task_name, root, [train_path, val_path, test_path])
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
    _finalize_export(db, project_id, task_id, "instruction", task_name, root, [train_path, val_path, test_path])
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
    _finalize_export(db, project_id, task_id, "preference", task_name, root, [train_path, val_path, test_path])
    return {"export_type": "preference", "root": str(root), "paths": {"train": str(train_path), "val": str(val_path), "test": str(test_path)}, "count": len(clean_rows)}


def export_rag_corpus(rows: list[dict], out_dir: str, task_id: str, task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    root = _resolve_root(out_dir, "rag", task_id, task_name)
    data_dir = build_subdir(root, "rag")
    out_path = data_dir / "rag_corpus.jsonl"
    build_dataset_jsonl(clean_rows, str(out_path))
    _finalize_export(db, project_id, task_id, "rag", task_name, root, [out_path])
    return {"root": str(root), "path": str(out_path)}


def export_skill_markdown(rows: list[dict], out_dir: str, task_id: str, title: str = "Skill Package", task_name: str | None = None, db=None, project_id: str | None = None):
    clean_rows = _dedupe_rows(rows)
    root = _resolve_root(out_dir, "skill", task_id, task_name)
    skill_dir = build_subdir(root, "skill")
    out_path = skill_dir / "skill.md"
    manifest_path = skill_dir / "skill.json"
    content_lines = ["## 输入摘要", ""]
    for idx, row in enumerate(clean_rows, 1):
        content_lines.append(f"- 样本 {idx}: {json.dumps(row, ensure_ascii=False)}")
    build_markdown_report(title, "\n".join(content_lines), str(out_path))
    manifest = {
        "schema_version": "envoy.skill.v1",
        "title": title,
        "source_task_id": task_id,
        "task_name": task_name,
        "project_id": project_id,
        "materials": clean_rows,
        "files": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _finalize_export(db, project_id, task_id, "skill", task_name, root, [out_path, manifest_path])
    return {
        "root": str(root),
        "path": str(out_path),
        "paths": {
            "markdown": str(out_path),
            "manifest": str(manifest_path),
        },
    }


def export_markdown_from_video_summary(summary_rows: list[dict], out_dir: str, task_id: str, title: str = "Video Markdown Report", task_name: str | None = None, db=None, project_id: str | None = None):
    root = _resolve_root(out_dir, "markdown", task_id, task_name)
    md_dir = build_subdir(root, "markdown")
    out_path = md_dir / "video_summary.md"
    content = ["## 视频转化摘要", ""]
    for row in summary_rows:
        content.append(f"- {json.dumps(row, ensure_ascii=False)}")
    build_markdown_report(title, "\n".join(content), str(out_path))
    _finalize_export(db, project_id, task_id, "markdown", task_name, root, [out_path])
    return {"root": str(root), "path": str(out_path)}


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
            rows.append({"text": su.get("content") or "", "metadata": su.get("labels") or {}})
        return rows
    if export_type == "skill":
        return data["semantic_units"] + data["samples"] + data["trace_links"]
    if export_type == "markdown":
        return data["semantic_units"] + data["samples"] + data["trace_links"]
    return []
