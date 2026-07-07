from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


DEFAULT_PDF_PATH = Path("/Volumes/172.30.202.40/2026/Envoy/PDF/数字电路与逻辑设计  第3版.pdf")


def main():
    parser = argparse.ArgumentParser(description="Run local PDF -> Markdown bundle acceptance flow.")
    parser.add_argument("--pdf", default=os.getenv("PDF_PATH", str(DEFAULT_PDF_PATH)), help="PDF path to preprocess and export.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'tmp' / 'large-pdf-rag-e2e.db'}"))
    args = parser.parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        raise SystemExit(f"missing pdf: {pdf_path}")

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
    from app.services.train_export_service import export_markdown_bundle
    from app.workers.jobs import preprocess_job

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        project = create_project(db, ProjectCreate(name="large pdf e2e", description="", domain_type="knowledge", status="active"))
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
        rows = [
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
        bundle_dir = ROOT / "tmp" / "large-pdf-bundle"
        export_result = export_markdown_bundle(rows, str(bundle_dir), task_id, title=pdf_path.stem, task_name=pdf_path.stem, project_id=project_id)
        manifest = json.loads(Path(export_result["paths"]["manifest"]).read_text(encoding="utf-8"))
        if stored_task.status != "success" or result.get("status") != "preprocessed":
            raise SystemExit(f"preprocess failed: task={stored_task.status} result={result.get('status')}")
        if len(units) < 2 or manifest.get("chunk_count", 0) < 2:
            raise SystemExit(f"acceptance failed: expected multi-page content, got units={len(units)} chunks={manifest.get('chunk_count', 0)}")
        print(json.dumps({
            "task_status": stored_task.status,
            "result_status": result.get("status"),
            "unit_count": len(units),
            "source_counts": manifest.get("source_counts"),
            "unit_type_counts": manifest.get("unit_type_counts"),
            "chunk_count": manifest.get("chunk_count"),
            "index": manifest["files"].get("index"),
            "overview": manifest["files"].get("overview"),
            "manifest": manifest["files"].get("manifest"),
            "source_count": len(manifest["files"].get("sources") or []),
            "first_chunk": (manifest["files"].get("chunks") or [None])[0],
            "last_chunk": (manifest["files"].get("chunks") or [None])[-1],
            "bundle_dir": str(bundle_dir),
        }, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
