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


DEFAULT_PDF_PATH = Path("/Volumes/172.30.202.40/2026/Envoy/PDF/数字电路与逻辑设计  第3版.pdf")


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


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Run local large PDF -> delivery package acceptance flow.")
    parser.add_argument("--pdf", default=os.getenv("PDF_PATH", str(DEFAULT_PDF_PATH)), help="PDF path to preprocess and export.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "large-pdf-delivery"), help="Directory for generated delivery package.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'tmp' / 'large-pdf-delivery-e2e.db'}"))
    args = parser.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        print(f"missing pdf: {pdf_path}", file=sys.stderr)
        return 2

    os.environ.setdefault("ENVOY_STORAGE_MODE", "local")
    os.environ.setdefault("ENVOY_LOCAL_STORAGE_DIR", str(ROOT / "tmp" / "storage"))
    os.environ.setdefault("ENVOY_EXPORT_DIR", str(ROOT / "tmp" / "exports"))
    os.environ.setdefault("DATABASE_URL", args.database_url)
    os.environ.setdefault("OLLAMA_MODEL", "")

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
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="large pdf delivery e2e", description="", domain_type="knowledge", status="active"))
        storage_info = storage_client.upload_bytes(pdf_path.name, pdf_path.read_bytes(), "application/pdf")
        asset = create_asset(
            db,
            AssetCreate(
                project_id=project.id,
                asset_type="document",
                file_name=pdf_path.name,
                file_path=storage_info["object_name"],
                mime_type="application/pdf",
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
        export_result = export_delivery_package(rows, args.out_dir, task_id, title=pdf_path.stem, task_name=pdf_path.stem, project_id=project_id)
        validator_result = validate_delivery_package(Path(export_result["root"]))
        if stored_task.status != "success" or result.get("status") != "preprocessed":
            print(json.dumps({"status": "failed", "reason": "preprocess_failed", "task_status": stored_task.status, "result_status": result.get("status")}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        if len(units) < 2 or validator_result.get("chunk_count", 0) < 2:
            print(json.dumps({"status": "failed", "reason": "insufficient_large_pdf_content", "unit_count": len(units), "chunk_count": validator_result.get("chunk_count", 0)}, ensure_ascii=False, indent=2), file=sys.stderr)
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
