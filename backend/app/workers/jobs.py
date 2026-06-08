import json
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.services.crud_service import update_task_status, create_artifact
from app.core.storage import storage_client
from app.services.media_service import MediaProcessingError, split_video, extract_frames, transcribe_audio, run_ocr_on_frames
from app.services.export_service import build_dataset_jsonl, build_rich_export_report
from app.services.train_export_service import export_skill_markdown, export_markdown_from_video_summary
from app.services.analysis_service import build_analysis_payload
from app.services.ollama_service import generate_with_ollama
from app.core.paths import SEGMENT_DIR, FRAME_DIR, EXPORT_DIR
from app.core.config import TEXT_PREPROCESS_CHUNK_CHARS, TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS
from app.models.semantic_unit import SemanticUnit
from app.models.asset import Asset
from app.models.generated_sample import GeneratedSample
from app.models.trace_link import TraceLink
from app.schemas.artifact import ArtifactCreate


def _safe_update(db, task_id, status, progress=None, result_summary=None, error_message=None):
    if task_id:
        update_task_status(db, task_id, status, progress, result_summary, error_message)


def _is_text_asset(asset):
    if asset is None:
        return False
    mime_type = asset.mime_type or ""
    return asset.asset_type in {"text", "markdown", "document"} or mime_type.startswith("text/") or asset.file_name.lower().endswith((".txt", ".md", ".markdown"))


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


def _sample_fallback(unit: SemanticUnit | None, payload: dict):
    title = (unit.title if unit else None) or payload.get("title") or "source material"
    content = (unit.content if unit else None) or payload.get("content") or payload.get("text") or ""
    content = str(content).strip()
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
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_generated_sample(unit: SemanticUnit | None, payload: dict):
    fallback = _sample_fallback(unit, payload)
    source_text = unit.content if unit else fallback["output_text"]
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


@celery_app.task(bind=True, max_retries=2)
def ping_job(self):
    return {"status": "ok"}


@celery_app.task(bind=True, max_retries=2)
def preprocess_job(self, payload: dict):
    db = SessionLocal()
    try:
        task_id = payload.get("task_id")
        asset_id = payload.get("asset_id")
        project_id = payload.get("project_id")
        _safe_update(db, task_id, "running", 10)
        asset = db.query(Asset).filter(Asset.id == asset_id).first() if asset_id else None
        source_path = _resolve_source_path(payload, asset)
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
                title = asset.file_name if chunk_count == 1 else f"{asset.file_name} chunk {index + 1}/{chunk_count}"
                labels = {
                    "stage": "preprocess",
                    "source": "text_asset",
                    "chunk_index": index,
                    "chunk_count": chunk_count,
                    "char_start": chunk["start"],
                    "char_end": chunk["end"],
                }
                units.append(SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="text", title=title, content=chunk["content"], labels=labels, confidence=0.92))
            db.add_all(units)
            db.commit()
            for unit in units:
                db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=unit.id, relation_type="derived_from"))
            db.commit()
            _safe_update(db, task_id, "success", 100, result_summary=f"text preprocess done: {semantic_payload['char_count']} chars, {chunk_count} chunks")
            return {"status": "preprocessed", "payload": payload, "semantic": semantic_payload}
        if _local_source_file_missing(source_path):
            message = f"Source file not found: {source_path or '(empty)'}"
            _safe_update(db, task_id, "failed", 100, error_message=message)
            return {"status": "failed", "payload": payload, "error": message}
        segment_paths = split_video(source_path, str(SEGMENT_DIR))
        frame_paths = extract_frames(source_path, str(FRAME_DIR))
        transcription = transcribe_audio(source_path)
        ocr_results = run_ocr_on_frames(frame_paths)
        semantic_payload = {"segments": segment_paths, "transcript": transcription, "ocr": ocr_results, "stage": "preprocess", "source_path": source_path}
        su = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="scene", title="preprocess summary", content=str(semantic_payload), labels={"stage": "preprocess", "source": "media_asset"}, confidence=0.8)
        db.add(su)
        db.commit()
        db.refresh(su)
        db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=asset_id or "", target_type="semantic_unit", target_id=su.id, relation_type="derived_from"))
        db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="preprocess done")
        return {"status": "preprocessed", "payload": payload}
    except MediaProcessingError as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        return {"status": "failed", "payload": payload, "error": str(exc)}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
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
        scene_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="scene", title="scene split", content=str(structured), labels={"stage": "analyze", "kind": "scene"}, confidence=0.78)
        rule_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="rule", title="rule extraction", content=str({"rules": structured["rules"]}), labels={"stage": "analyze", "kind": "rule"}, confidence=0.76)
        concept_unit = SemanticUnit(id=str(uuid4()), project_id=project_id, asset_id=asset_id, segment_id=None, unit_type="concept", title="concept extraction", content=str({"concepts": structured["concepts"]}), labels={"stage": "analyze", "kind": "concept"}, confidence=0.75)
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
        unit = db.query(SemanticUnit).filter(SemanticUnit.id == source_unit_id).first() if source_unit_id else None
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
        _safe_update(db, task_id, "success", 100, result_summary="1 sample generated")
        return {"status": "samples_generated", "payload": payload, "sample_id": sample.id}
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
        rows = [{"project_id": project_id, "task_id": task_id, "status": "exported"}]
        semantic_units = []
        samples = []
        trace_links = []
        build_dataset_jsonl(rows, str(jsonl_path))
        build_rich_export_report("Export Report", {"task_id": task_id, "project_id": project_id, "row_count": len(rows)}, semantic_units, samples, trace_links, str(md_path))
        dataset_artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="dataset", name=jsonl_path.name, file_path=str(jsonl_path), version="v1"))
        report_artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="report", name=md_path.name, file_path=str(md_path), version="v1"))
        db.add(TraceLink(id=str(uuid4()), source_type="processing_task", source_id=task_id, target_type="artifact", target_id=dataset_artifact.id, relation_type="exported_to"))
        db.add(TraceLink(id=str(uuid4()), source_type="processing_task", source_id=task_id, target_type="artifact", target_id=report_artifact.id, relation_type="exported_to"))
        db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="export done")
        return {"status": "exported", "payload": payload}
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
        skill_path = Path(export_result["path"])
        artifact = create_artifact(db, ArtifactCreate(project_id=project_id, artifact_type="skill", name=skill_path.name, file_path=str(skill_path), version="v1"))
        if payload.get("source_asset_id"):
            db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=payload["source_asset_id"], target_type="artifact", target_id=artifact.id, relation_type="exported_to"))
            db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="skill exported")
        return {"status": "skill_exported", "path": str(skill_path)}
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
            db.add(TraceLink(id=str(uuid4()), source_type="asset", source_id=payload["source_asset_id"], target_type="artifact", target_id=artifact.id, relation_type="exported_to"))
            db.commit()
        _safe_update(db, task_id, "success", 100, result_summary="markdown exported")
        return {"status": "markdown_exported", "path": str(md_path)}
    except Exception as exc:
        _safe_update(db, payload.get("task_id"), "failed", 100, error_message=str(exc))
        raise self.retry(exc=exc, countdown=2)
    finally:
        db.close()
