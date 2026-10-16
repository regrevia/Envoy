from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _semantic_rows(units):
    return [
        {
            "record_type": "semantic_unit",
            "id": unit.id,
            "project_id": unit.project_id,
            "asset_id": unit.asset_id,
            "unit_type": unit.unit_type,
            "title": unit.title,
            "content": unit.content,
            "labels": unit.labels,
            "confidence": unit.confidence,
        }
        for unit in units
    ]


def _large_text(repeat_count: int):
    section = (
        "Envoy commercial delivery package requirements:\n"
        "- preserve source order and chunk indexes\n"
        "- produce RAG JSONL, Markdown chunks, training seeds, and agent import manifests\n"
        "- keep quality metadata and import paths stable for existing knowledge bases\n"
        "- support long files without loading downstream agents with duplicated context\n"
    )
    return "\n\n".join(f"## Section {index + 1}\n{section}" for index in range(max(repeat_count, 1)))


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run local large text -> delivery package acceptance flow.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "large-text-delivery"), help="Directory for generated delivery package.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'tmp' / 'large-text-delivery-e2e.db'}"))
    parser.add_argument("--repeat-count", type=int, default=120, help="Number of repeated sections in the generated long text fixture.")
    args = parser.parse_args(argv)

    os.environ.setdefault("ENVOY_STORAGE_MODE", "local")
    os.environ.setdefault("ENVOY_LOCAL_STORAGE_DIR", str(ROOT / "tmp" / "storage"))
    os.environ.setdefault("ENVOY_EXPORT_DIR", str(ROOT / "tmp" / "exports"))
    os.environ.setdefault("DATABASE_URL", args.database_url)
    os.environ.setdefault("OLLAMA_MODEL", "")
    os.environ.setdefault("TEXT_PREPROCESS_CHUNK_CHARS", "1200")
    os.environ.setdefault("TEXT_PREPROCESS_CHUNK_OVERLAP_CHARS", "120")

    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401
    from app.core.storage import storage_client
    from app.models.semantic_unit import SemanticUnit
    from app.schemas.asset import AssetCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services.crud_service import create_asset, create_project, create_task, get_task
    from app.services.train_export_service import export_delivery_package
    from app.workers.jobs import preprocess_job
    from validate_delivery_package import validate_delivery_package

    Base.metadata.create_all(bind=engine)
    text = _large_text(args.repeat_count)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="large text delivery e2e", description="", domain_type="knowledge", status="active"))
        storage_info = storage_client.upload_bytes("commercial-long-text.md", text.encode("utf-8"), "text/markdown")
        asset = create_asset(
            db,
            AssetCreate(
                project_id=project.id,
                asset_type="text",
                file_name="commercial-long-text.md",
                file_path=storage_info["object_name"],
                mime_type="text/markdown",
                metadata={"storage": storage_info["storage"]},
            ),
        )
        task = create_task(db, TaskCreate(project_id=project.id, asset_id=asset.id, task_type="preprocess", input_config={}))
        project_id = project.id
        asset_id = asset.id
        task_id = task.id
    finally:
        db.close()

    result = preprocess_job.run({"task_id": task_id, "project_id": project_id, "asset_id": asset_id})

    db = SessionLocal()
    try:
        stored_task = get_task(db, task_id)
        units = db.query(SemanticUnit).filter(SemanticUnit.asset_id == asset_id).order_by(SemanticUnit.created_at.asc()).all()
        rows = _semantic_rows(units)
        export_result = export_delivery_package(rows, args.out_dir, task_id, title="commercial-long-text", task_name="commercial-long-text", project_id=project_id)
        validator_result = validate_delivery_package(Path(export_result["root"]))
        if stored_task.status != "success" or result.get("status") != "preprocessed":
            print(json.dumps({"status": "failed", "reason": "preprocess_failed", "task_status": stored_task.status, "result_status": result.get("status")}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        if len(units) < 4 or validator_result.get("chunk_count", 0) < 4:
            print(json.dumps({"status": "failed", "reason": "insufficient_large_text_content", "unit_count": len(units), "chunk_count": validator_result.get("chunk_count", 0)}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        if validator_result["status"] != "ok":
            print(json.dumps({"status": "failed", "reason": "delivery_validation_failed", "validator": validator_result}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        print(json.dumps({
            "status": "ok",
            "task_status": stored_task.status,
            "result_status": result.get("status"),
            "unit_count": len(units),
            "package_root": export_result["root"],
            "manifest": export_result["paths"]["manifest"],
            "import_manifest": export_result["paths"]["import_manifest"],
            "validator": validator_result,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
