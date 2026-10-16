import json
from pathlib import Path

from app.services import train_export_service
from app.services.train_export_service import build_markdown_chunks, export_delivery_package, export_markdown_bundle


def test_delivery_package_writes_importable_agent_and_knowledge_base_indexes(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "video-001",
            "project_id": "project-delivery",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "开场与课程目标",
            "content": json.dumps(
                {
                    "transcript": {"text": "老师介绍本节课会讲组合逻辑、触发器和实验要求。"},
                    "ocr": [{"text": "数字电路与逻辑设计 第三章"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "seg-002.mp4", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-delivery",
                "source_name": "数字电路课程.mp4",
                "section_path": ["第 1 段", "开场与课程目标"],
                "time_range": ["00:00:00", "00:04:30"],
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
            "confidence": 0.82,
        },
        {
            "record_type": "semantic_unit",
            "id": "doc-001",
            "project_id": "project-delivery",
            "asset_id": "asset-doc",
            "unit_type": "document",
            "title": "实验说明",
            "content": "# 实验说明\n\n## 操作步骤\n\n连接芯片并记录状态转换。",
            "labels": {
                "source": "document_text",
                "task_id": "task-delivery",
                "source_name": "实验指导书.pdf",
                "section_path": ["实验说明", "操作步骤"],
                "page_range": [4, 6],
            },
            "confidence": 0.9,
        },
    ]

    result = export_delivery_package(
        rows,
        out_dir=str(tmp_path),
        task_id="task-delivery",
        title="数字电路课程交付包",
        task_name="digital-delivery",
        project_id="project-delivery",
    )

    root = Path(result["root"])
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    import_manifest = json.loads(Path(result["paths"]["import_manifest"]).read_text(encoding="utf-8"))
    chunk_index = json.loads(Path(result["paths"]["chunk_index"]).read_text(encoding="utf-8"))
    source_index = json.loads(Path(result["paths"]["source_index"]).read_text(encoding="utf-8"))
    quality_report = json.loads(Path(result["paths"]["quality_report"]).read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "envoy.delivery.v1"
    assert manifest["directories"] == {
        "readable": "readable",
        "knowledge_base": "knowledge_base",
        "training": "training",
        "agent": "agent",
        "indexes": "indexes",
    }
    assert Path(root / "README.md").is_file()
    assert Path(root / "readable" / "index.md").is_file()
    assert Path(root / "knowledge_base" / "rag_corpus.jsonl").is_file()
    assert Path(root / "training" / "instruction_train.jsonl").is_file()
    assert Path(root / "agent" / "skill.md").is_file()
    assert import_manifest["schema_version"] == "envoy.agent.import.v1"
    assert import_manifest["compatible_targets"] == [
        "generic_rag_jsonl",
        "markdown_knowledge_base",
        "agent_skill_context",
        "instruction_tuning_seed",
    ]
    target_profiles = import_manifest["target_profiles"]
    assert target_profiles["generic_rag_jsonl"]["primary_file"] == "knowledge_base/rag_corpus.jsonl"
    assert target_profiles["generic_rag_jsonl"]["required_fields"] == ["doc_id", "title", "text", "metadata"]
    assert target_profiles["markdown_knowledge_base"]["file_globs"] == ["readable/index.md", "readable/sources/*.md", "knowledge_base/chunks/*.md"]
    assert target_profiles["agent_skill_context"]["primary_file"] == "agent/skill.md"
    assert target_profiles["instruction_tuning_seed"]["primary_file"] == "training/instruction_train.jsonl"
    assert target_profiles["instruction_tuning_seed"]["required_fields"] == ["instruction", "input", "output"]
    assert import_manifest["recommended_import_order"][0]["path"] == "knowledge_base/rag_corpus.jsonl"
    assert import_manifest["recommended_import_order"][0]["use"] == "rag_knowledge_base"
    assert import_manifest["recommended_import_order"][1]["path"] == "readable/index.md"
    assert chunk_index["chunk_count"] >= 2
    assert any(item["reference_text"] == "00:00:00 - 00:04:30" for item in chunk_index["chunks"])
    assert source_index["source_count"] == 2
    assert quality_report["partial_chunk_count"] == 1
    assert quality_report["failure_breakdown"]["transcription_failed_segments"] == 1
    assert result["paths"]["rag_corpus"].endswith("knowledge_base/rag_corpus.jsonl")


def test_delivery_package_avoids_duplicate_media_summary_when_transcript_exists(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "transcript-001",
            "project_id": "project-video",
            "asset_id": "asset-video",
            "unit_type": "transcript",
            "title": "media transcript",
            "content": "Envoy delivery package converts long videos into retrieval ready chunks.",
            "labels": {
                "source": "media_transcript",
                "source_name": "media transcript",
                "task_id": "task-video",
            },
        },
        {
            "record_type": "semantic_unit",
            "id": "summary-001",
            "project_id": "project-video",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "preprocess summary",
            "content": json.dumps(
                {
                    "transcript": {"text": "Envoy delivery package converts long videos into retrieval ready chunks."},
                    "ocr": [],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["ocr_unavailable"],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "source_name": "preprocess summary",
                "task_id": "task-video",
                "extraction_status": "partial_success",
                "degraded_capabilities": ["ocr_unavailable"],
            },
        },
    ]

    result = export_delivery_package(
        rows,
        out_dir=str(tmp_path),
        task_id="task-video",
        title="视频交付包",
        task_name="video-delivery",
        project_id="project-video",
    )

    rag_rows = [
        json.loads(line)
        for line in Path(result["paths"]["rag_corpus"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    chunk_index = json.loads(Path(result["paths"]["chunk_index"]).read_text(encoding="utf-8"))
    quality_report = json.loads(Path(result["paths"]["quality_report"]).read_text(encoding="utf-8"))

    assert [row["source"] for row in rag_rows] == ["media_transcript"]
    assert chunk_index["chunk_count"] == 1
    assert chunk_index["chunks"][0]["source_type"] == "media_transcript"
    assert quality_report["partial_chunk_count"] == 1
    assert quality_report["source_counts"]["media_asset"] == 1


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


def test_video_markdown_bundle_writes_timeline_index_and_clean_chunk_names(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "video-001",
            "project_id": "project-video",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "开场与课程目标",
            "content": json.dumps(
                {
                    "transcript": {"text": "老师介绍本节课会讲组合逻辑、触发器和实验要求。"},
                    "ocr": [{"text": "数字电路与逻辑设计 第三章"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "seg-002.mp4", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-video",
                "source_name": "数字电路课程.mp4",
                "section_path": ["第 1 段", "开场与课程目标"],
                "time_range": ["00:00:00", "00:04:30"],
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
            "confidence": 0.82,
        },
        {
            "record_type": "semantic_unit",
            "id": "video-002",
            "project_id": "project-video",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "触发器例题",
            "content": json.dumps(
                {
                    "transcript": {"text": "这里分析 JK 触发器的状态转换表，并说明时钟边沿。"},
                    "ocr": [{"text": "JK 触发器 状态转换"}],
                    "extraction_status": "success",
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": "task-video",
                "source_name": "数字电路课程.mp4",
                "section_path": ["第 2 段", "触发器例题"],
                "time_range": ["00:04:30", "00:09:00"],
                "extraction_status": "success",
            },
            "confidence": 0.88,
        },
    ]

    result = export_markdown_bundle(
        rows,
        out_dir=str(tmp_path),
        task_id="task-video",
        title="数字电路课程视频",
        task_name="digital-video",
        project_id="project-video",
        enhance_with_ollama=False,
    )

    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    index_text = Path(result["paths"]["index"]).read_text(encoding="utf-8")
    source_text = Path(result["paths"]["sources"][0]).read_text(encoding="utf-8")
    chunk_paths = [Path(path) for path in result["paths"]["chunks"]]
    chunk_text = chunk_paths[0].read_text(encoding="utf-8")

    assert "## 视频时间线" in index_text
    assert "- [00:00:00 - 00:04:30]" in index_text
    assert "## 视频时间线" in source_text
    assert "转写失败片段数：1" in source_text
    assert "引用：00:00:00 - 00:04:30" in chunk_text
    assert "老师介绍本节课" in chunk_text
    assert "数字电路与逻辑设计 第三章" in chunk_text
    assert any(path.name.startswith("001-00-00-00-00-04-30-") for path in chunk_paths)
    assert manifest["video_source_count"] == 1
    assert manifest["timeline_index"][0]["reference_text"] == "00:00:00 - 00:04:30"
    assert manifest["timeline_index"][0]["degraded_capabilities"] == ["partial_transcript"]
    assert manifest["file_tree"]["sources"][0]["timeline"][1]["title"] == "触发器例题"
