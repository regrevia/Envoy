import json
from pathlib import Path

from app.services.train_export_service import export_markdown_bundle


def test_markdown_bundle_manifest_exposes_tree_for_detail_views(tmp_path):
    rows = [
        {
            "record_type": "semantic_unit",
            "id": "unit-1",
            "project_id": "project-1",
            "asset_id": "asset-1",
            "unit_type": "document",
            "title": "第一章 概述",
            "content": "# 第一章 概述\n\n## 背景\n\n正文。",
            "labels": {
                "source": "document_text",
                "task_id": "task-1",
                "source_name": "项目说明书.pdf",
                "section_path": ["第一章 概述", "背景"],
                "page_range": [1, 2],
            },
        }
    ]

    result = export_markdown_bundle(rows, str(tmp_path), "task-1", "bundle", task_name="bundle", project_id="project-1")
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "envoy.markdown.bundle.v1"
    assert manifest["encoding"] == "utf-8"
    assert manifest["files"]["index"].endswith("index.md")
    assert manifest["files"]["overview"].endswith("00-project-overview.md")
    assert manifest["files"]["manifest"].endswith("manifest.json")
    assert manifest["sources"][0]["source_name"] == "项目说明书.pdf"
    assert manifest["chunks"][0]["title"] == "背景"
    assert manifest["chunks"][0]["reference_text"] == "第 1-2 页"
