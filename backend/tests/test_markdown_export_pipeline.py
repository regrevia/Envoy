import json
from pathlib import Path

from app.services import train_export_service
from app.services.train_export_service import build_markdown_chunks, export_markdown_bundle


def test_markdown_export_writes_overview_index_and_chunk_tree(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "unit_type": "document",
            "title": "第一章 项目背景",
            "content": "# 第一章 项目背景\n\n这是正文。",
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "项目说明书.pdf",
                "section_path": ["第一章 项目背景"],
                "page_range": [1, 3],
            },
            "confidence": 0.91,
        }
    ]

    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-1",
        title="project-brief",
        task_name="project-brief",
        project_id="project-1",
    )

    assert Path(result["paths"]["index"]).is_file()
    assert Path(result["paths"]["overview"]).is_file()
    assert Path(result["paths"]["manifest"]).is_file()
    chunk_paths = result["paths"]["chunks"]
    assert len(chunk_paths) == 1
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    index_text = Path(result["paths"]["index"]).read_text(encoding="utf-8")
    chunk_text = Path(chunk_paths[0]).read_text(encoding="utf-8")
    assert "# 第一章 项目背景" in chunk_text
    assert "来源：项目说明书.pdf" in chunk_text
    assert "引用：第 1-3 页" in chunk_text
    assert "### 项目说明书.pdf" in index_text
    assert "第一章 项目背景" in index_text
    assert manifest["file_tree"]["overview"].endswith("00-project-overview.md")
    assert manifest["file_tree"]["sources"][0]["source_name"] == "项目说明书.pdf"
    assert manifest["file_tree"]["sources"][0]["chunks"][0]["title"] == "第一章 项目背景"


def test_markdown_export_records_partial_status_and_ollama_skip(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-video",
            "project_id": "project-1",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "视频片段 1",
            "content": json.dumps(
                {
                    "transcript": {"text": "可用转写"},
                    "ocr": [{"text": "幻灯片标题"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "seg-2", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-video",
                "source_name": "演示视频.mp4",
                "time_range": ["00:00:00", "00:05:00"],
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
            "confidence": 0.8,
        }
    ]

    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-video",
        title="video-brief",
        task_name="video-brief",
        project_id="project-1",
        enhance_with_ollama=False,
    )

    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["partial_chunk_count"] == 1
    assert manifest["failure_breakdown"]["transcription_failed_segments"] == 1
    assert manifest["ollama"]["enabled"] is False
    assert manifest["ollama"]["chunk_enhancement_status"] == "skipped"
    assert manifest["file_tree"]["sources"][0]["source_name"] == "演示视频.mp4"
    assert manifest["file_tree"]["sources"][0]["chunks"][0]["reference_text"] == "00:00:00 - 00:05:00"


def test_markdown_chunking_preserves_source_and_heading_boundaries_before_length_split():
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-a-1",
            "project_id": "project-1",
            "asset_id": "asset-a",
            "unit_type": "document",
            "title": "第二章 方案设计",
            "content": "# 第二章 方案设计\n\n## 架构目标\n\n" + ("目标说明。\n" * 180),
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "a.md",
                "section_path": ["第二章 方案设计", "架构目标"],
            },
        },
        {
            "record_type": "semantic_unit",
            "id": "unit-a-2",
            "project_id": "project-1",
            "asset_id": "asset-a",
            "unit_type": "document",
            "title": "第二章 方案设计",
            "content": "# 第二章 方案设计\n\n## 核心流程\n\n" + ("流程说明。\n" * 180),
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "a.md",
                "section_path": ["第二章 方案设计", "核心流程"],
            },
        },
        {
            "record_type": "semantic_unit",
            "id": "unit-b-1",
            "project_id": "project-1",
            "asset_id": "asset-b",
            "unit_type": "document",
            "title": "第三章 实施计划",
            "content": "# 第三章 实施计划\n\n## 时间安排\n\n阶段安排。",
            "labels": {
                "source": "text_asset",
                "task_id": "task-1",
                "source_name": "b.txt",
                "section_path": ["第三章 实施计划", "时间安排"],
            },
        },
    ]

    chunks = build_markdown_chunks(rows, target_chars=800, max_chars=1200)

    assert len(chunks) >= 3
    assert chunks[0]["source_name"] == "a.md"
    assert chunks[-1]["source_name"] == "b.txt"
    assert all(chunk["title"] for chunk in chunks)
    assert all(chunk["summary"] for chunk in chunks)
    assert all(chunk["section_path"] for chunk in chunks)
    assert any(chunk["split_part"] > 1 for chunk in chunks if chunk["source_name"] == "a.md")


def test_markdown_export_survives_ollama_failure_and_records_status(tmp_path, monkeypatch):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "unit_type": "document",
            "title": "第一章 项目背景",
            "content": "# 第一章 项目背景\n\n这是正文。",
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "项目说明书.pdf",
                "section_path": ["第一章 项目背景"],
                "page_range": [1, 3],
            },
        }
    ]

    def _fake_generate_with_ollama(*_args, **_kwargs):
        return {
            "enabled": True,
            "status": "failed",
            "text": "",
            "error": "connect timeout",
            "truncated": False,
            "original_prompt_chars": 100,
            "sent_prompt_chars": 100,
            "max_prompt_chars": 2000,
        }

    monkeypatch.setattr(train_export_service, "generate_with_ollama", _fake_generate_with_ollama)

    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-1",
        title="project-brief",
        task_name="project-brief",
        project_id="project-1",
        enhance_with_ollama=True,
    )

    assert Path(result["paths"]["overview"]).is_file()
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["ollama"]["enabled"] is True
    assert manifest["ollama"]["chunk_enhancement_status"] == "failed"
    assert manifest["ollama"]["failed_chunks"] == 1


def test_markdown_chunking_splits_video_and_document_sources_separately():
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "doc-1",
            "project_id": "project-1",
            "asset_id": "asset-doc",
            "unit_type": "document",
            "title": "第一章 背景",
            "content": "# 第一章 背景\n\n## 一、说明\n\n" + ("文档内容。\n" * 100),
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "doc.md",
                "section_path": ["第一章 背景", "说明"],
            },
        },
        {
            "record_type": "semantic_unit",
            "id": "video-1",
            "project_id": "project-1",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "片段一",
            "content": json.dumps(
                {
                    "transcript": {"text": "视频转写一。"},
                    "ocr": [{"text": "视频画面一"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "seg-1", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-1",
                "source_name": "video.mp4",
                "time_range": ["00:00:00", "00:02:00"],
                "section_path": ["片段一"],
                "extraction_status": "partial_success",
            },
        },
    ]

    chunks = build_markdown_chunks(rows, target_chars=400, max_chars=600)
    doc_chunks = [chunk for chunk in chunks if chunk["source_name"] == "doc.md"]
    video_chunks = [chunk for chunk in chunks if chunk["source_name"] == "video.mp4"]

    assert doc_chunks
    assert video_chunks
    assert doc_chunks[0]["split_total"] >= 1
    assert video_chunks[0]["reference_text"] == "00:00:00 - 00:02:00"
    assert all(chunk["title"] for chunk in chunks)
    assert all(chunk["summary"] for chunk in chunks)
