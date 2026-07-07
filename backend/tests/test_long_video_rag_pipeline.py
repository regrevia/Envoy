import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.asset import Asset
from app.models.processing_task import ProcessingTask
from app.models.semantic_unit import SemanticUnit
from app.services.train_export_service import export_rag_corpus
from app.workers import jobs


class DummyTask:
    def retry(self, exc, countdown):
        raise AssertionError(f"unexpected retry: {exc} (countdown={countdown})")


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def media_project(db_session, tmp_path):
    project_id = str(uuid4())
    task_id = str(uuid4())
    asset_id = str(uuid4())
    source_path = tmp_path / "demo.mp4"
    source_path.write_bytes(b"video")

    asset = Asset(
        id=asset_id,
        project_id=project_id,
        asset_type="video",
        file_name="demo.mp4",
        file_path=str(source_path),
        mime_type="video/mp4",
    )
    task = ProcessingTask(
        id=task_id,
        project_id=project_id,
        asset_id=asset_id,
        task_type="preprocess",
        status="pending",
        progress=0,
        input_config={},
    )
    db_session.add_all([asset, task])
    db_session.commit()
    return SimpleNamespace(project_id=project_id, task_id=task_id, asset_id=asset_id, source_path=source_path)


def test_preprocess_video_marks_partial_success_and_persists_clean_semantics(
    monkeypatch, db_session, media_project
):
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(jobs, "_task_is_canceled", lambda db, task_id: False)
    monkeypatch.setattr(jobs, "_cleanup_media_task_temp_dirs", lambda temp_dirs: None)
    monkeypatch.setattr(jobs, "_split_video_for_task", lambda db, task_id, source_path, output_dir: ["seg-1.mp4", "seg-2.mp4"])
    monkeypatch.setattr(jobs, "_extract_frames_for_task", lambda db, task_id, source_path, output_dir: ["frame-1.jpg"])

    transcription_calls = iter(
        [
            {"status": "success", "text": "alpha transcript", "segments": [{"text": "alpha"}], "error": None},
            {"status": "failed", "text": "", "segments": [], "error": "timeout after 900 sec"},
        ]
    )
    monkeypatch.setattr(jobs, "_transcribe_audio_for_task", lambda db, task_id, segment_path: next(transcription_calls))
    monkeypatch.setattr(
        jobs,
        "_run_ocr_for_task",
        lambda db, task_id, frame_paths: [{"frame": "frame-1.jpg", "text": "ocr detail", "confidence": 0.81}],
    )

    payload = {"task_id": media_project.task_id, "project_id": media_project.project_id, "asset_id": media_project.asset_id}
    result = jobs.preprocess_job.run(payload)

    assert result["status"] == "preprocessed"
    semantic = result["semantic"]
    assert semantic["extraction_status"] == "partial_success"
    assert semantic["degraded_capabilities"] == ["partial_transcript"]
    assert semantic["transcription_success_count"] == 1
    assert semantic["transcription_failed_count"] == 1
    assert semantic["usable_text_sources"] == ["transcript", "ocr"]

    units = db_session.query(SemanticUnit).filter(SemanticUnit.project_id == media_project.project_id).all()
    by_source = {row.labels.get("source"): row for row in units if isinstance(row.labels, dict)}
    assert "media_transcript" in by_source
    assert by_source["media_transcript"].content == "alpha transcript"
    assert "media_ocr" in by_source
    summary = by_source["media_asset"]
    summary_payload = json.loads(summary.content)
    assert summary_payload["extraction_status"] == "partial_success"
    assert summary_payload["degraded_capabilities"] == ["partial_transcript"]
    assert summary.labels["extraction_status"] == "partial_success"
    assert summary.labels["degraded_capabilities"] == ["partial_transcript"]
    assert summary.labels["transcription_failed_count"] == 1


def test_export_rag_corpus_writes_clean_text_rows_and_manifest(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "unit_type": "scene",
            "title": "summary",
            "content": json.dumps(
                {
                    "transcript": {"text": "alpha transcript"},
                    "ocr": [{"text": "ocr detail"}],
                    "transcription_failed_segments": [{"segment": "seg-2.mp4", "error": "timeout"}],
                    "degraded_capabilities": ["partial_transcript"],
                    "extraction_status": "partial_success",
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-1",
                "segment_seconds": 600,
                "max_segment_count": 12,
                "max_input_seconds": 7200,
            },
            "confidence": 0.8,
        }
    ]

    export_result = export_rag_corpus(rows, str(tmp_path), "task-1", "video-rag", project_id="project-1")

    corpus_path = Path(export_result["paths"]["corpus"])
    manifest_path = Path(export_result["paths"]["manifest"])
    corpus_rows = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert corpus_rows == [
        {
            "doc_id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "task_id": "task-1",
            "title": "summary",
            "text": "alpha transcript\nocr detail",
            "source": "media_asset",
            "unit_type": "scene",
            "content_format": "json",
            "metadata": {
                "confidence": 0.8,
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
        }
    ]
    assert manifest["clean_row_count"] == 1
    assert manifest["row_count"] == 1
    assert manifest["partial_row_count"] == 1
    assert manifest["failure_breakdown"]["transcription_failed_segments"] == 1
