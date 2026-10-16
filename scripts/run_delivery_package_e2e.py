#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _sample_rows(task_id: str):
    return [
        {
            "record_type": "semantic_unit",
            "id": "delivery-video-summary",
            "project_id": "delivery-e2e-project",
            "asset_id": "asset-video",
            "unit_type": "scene",
            "title": "长视频开场与目标",
            "content": json.dumps(
                {
                    "transcript": {"text": "讲师介绍课程目标、实验要求和后续会生成知识库与训练素材。"},
                    "ocr": [{"text": "课程目标 / 实验要求 / RAG 语料"}],
                    "extraction_status": "partial_success",
                    "degraded_capabilities": ["partial_transcript"],
                    "transcription_failed_segments": [{"segment": "segment-002.mp4", "error": "timeout"}],
                },
                ensure_ascii=False,
            ),
            "labels": {
                "source": "media_asset",
                "task_id": task_id,
                "source_name": "course-long-video.mp4",
                "section_path": ["第 1 段", "开场与目标"],
                "time_range": ["00:00:00", "00:04:30"],
                "extraction_status": "partial_success",
                "degraded_capabilities": ["partial_transcript"],
                "transcription_failed_count": 1,
            },
            "confidence": 0.82,
        },
        {
            "record_type": "semantic_unit",
            "id": "delivery-pdf-section",
            "project_id": "delivery-e2e-project",
            "asset_id": "asset-pdf",
            "unit_type": "document",
            "title": "实验指导书 - 操作流程",
            "content": "# 实验指导书\n\n## 操作流程\n\n1. 准备输入素材。\n2. 执行预处理。\n3. 检查分片索引和质量报告。",
            "labels": {
                "source": "document_text",
                "task_id": task_id,
                "source_name": "实验指导书.pdf",
                "section_path": ["实验指导书", "操作流程"],
                "page_range": [4, 8],
            },
            "confidence": 0.9,
        },
        {
            "record_type": "semantic_unit",
            "id": "delivery-long-text",
            "project_id": "delivery-e2e-project",
            "asset_id": "asset-text",
            "unit_type": "text",
            "title": "长文本补充说明",
            "content": "# 长文本补充说明\n\n## 交付要求\n\n" + ("交付包需要能被知识库、agent 和训练流程直接读取。\n" * 80),
            "labels": {
                "source": "text_asset",
                "task_id": task_id,
                "source_name": "requirements.md",
                "section_path": ["长文本补充说明", "交付要求"],
            },
            "confidence": 0.87,
        },
    ]


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Generate and validate an Envoy delivery package smoke fixture.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "delivery-e2e"), help="Directory where the package root should be created.")
    parser.add_argument("--task-id", default="delivery-e2e", help="Stable task id used in generated package paths.")
    parser.add_argument("--title", default="Envoy Delivery E2E Package", help="Delivery package title.")
    args = parser.parse_args(argv)

    from app.services.train_export_service import export_delivery_package
    from validate_delivery_package import validate_delivery_package

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    export_result = export_delivery_package(
        _sample_rows(args.task_id),
        str(out_dir),
        args.task_id,
        title=args.title,
        task_name="delivery-e2e",
        project_id="delivery-e2e-project",
    )
    validator_result = validate_delivery_package(Path(export_result["root"]))
    payload = {
        "status": validator_result["status"],
        "package_root": export_result["root"],
        "manifest": export_result["paths"]["manifest"],
        "import_manifest": export_result["paths"]["import_manifest"],
        "validator": validator_result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if validator_result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
