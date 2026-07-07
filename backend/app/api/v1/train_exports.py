from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.train_export import TrainExportRequest
from app.services.train_export_service import export_sft_dataset, export_instruction_dataset, export_preference_dataset, export_rag_corpus, export_skill_markdown, export_markdown_bundle, export_markdown_from_video_summary, build_auto_payload_from_project
from app.services.crud_service import get_asset, get_project, get_task, list_export_records
from app.core.config import EXPORT_DIR

router = APIRouter()
SUPPORTED_EXPORT_TYPES = {"sft", "instruction", "preference", "rag", "skill", "markdown"}


def _validate_export_type(export_type: str):
    if export_type not in SUPPORTED_EXPORT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported export type")


def _validate_export_payload(rows: list[dict]):
    if not rows:
        raise HTTPException(status_code=400, detail="No exportable materials provided")
    if not any(_row_has_usable_content(row) for row in rows):
        raise HTTPException(status_code=400, detail="No usable export content provided")


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _row_has_usable_content(row: dict):
    if not isinstance(row, dict):
        return False
    for key in (
        "text",
        "content",
        "summary",
        "topic",
        "question",
        "answer",
        "input",
        "output",
        "input_text",
        "output_text",
        "instruction",
        "title",
    ):
        if _text_value(row.get(key)):
            return True
    return False


@router.get("/train-exports/history")
def list_train_exports_history(db: Session = Depends(get_db)):
    rows = list_export_records(db)
    return [
        {
            'id': row.id,
            'project_id': row.project_id,
            'task_id': row.task_id,
            'export_type': row.export_type,
            'task_name': row.task_name,
            'root_dir': row.root_dir,
            'file_list': row.file_list,
            'created_at': row.created_at,
        }
        for row in rows
    ]


def _resolve_export_context(db: Session, payload: TrainExportRequest):
    project_id = payload.project_id

    if project_id and get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.source_asset_id:
        asset = get_asset(db, payload.source_asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Source asset not found")
        if project_id and asset.project_id != project_id:
            raise HTTPException(status_code=400, detail="Source asset does not belong to project")
        project_id = project_id or asset.project_id

    if payload.source_task_id:
        task = get_task(db, payload.source_task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Source task not found")
        if project_id and task.project_id != project_id:
            raise HTTPException(status_code=400, detail="Source task does not belong to project")
        project_id = project_id or task.project_id

    if not project_id:
        raise HTTPException(status_code=400, detail="project_id or source context required")

    task_id = payload.source_task_id or payload.source_asset_id or "manual"
    return project_id, task_id


@router.post("/train-exports/auto")
def auto_build_train_export(payload: TrainExportRequest, db: Session = Depends(get_db)):
    _validate_export_type(payload.export_type)
    project_id, _task_id = _resolve_export_context(db, payload)
    auto_rows = build_auto_payload_from_project(db, project_id, payload.export_type)
    if not auto_rows:
        raise HTTPException(status_code=400, detail="No exportable materials found for project")
    payload = payload.model_copy(update={'project_id': project_id, 'payload': auto_rows})
    return create_train_export(payload, db)


@router.post("/train-exports")
def create_train_export(payload: TrainExportRequest, db: Session = Depends(get_db)):
    _validate_export_type(payload.export_type)
    samples = payload.payload or []
    _validate_export_payload(samples)
    out_dir = payload.out_dir or str(EXPORT_DIR)
    project_id, task_id = _resolve_export_context(db, payload)
    if payload.export_type == "sft":
        return export_sft_dataset(samples, out_dir, task_id, payload.task_name, db=db, project_id=project_id)
    if payload.export_type == "instruction":
        return export_instruction_dataset(samples, out_dir, task_id, payload.task_name, db=db, project_id=project_id)
    if payload.export_type == "preference":
        return export_preference_dataset(samples, out_dir, task_id, payload.task_name, db=db, project_id=project_id)
    if payload.export_type == "rag":
        return export_rag_corpus(samples, out_dir, task_id, payload.task_name, db=db, project_id=project_id)
    if payload.export_type == "skill":
        return export_skill_markdown(samples, out_dir, task_id, payload.title or 'Skill Package', payload.task_name, db=db, project_id=project_id)
    if payload.export_type == "markdown":
        return export_markdown_bundle(
            samples,
            out_dir,
            task_id,
            payload.title or 'Markdown Bundle',
            payload.task_name,
            db=db,
            project_id=project_id,
            enhance_with_ollama=payload.markdown_enhance_with_ollama,
        )
    return {"error": "unsupported export type"}
