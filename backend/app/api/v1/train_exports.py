from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.schemas.train_export import TrainExportRequest
from app.services.train_export_service import export_sft_dataset, export_instruction_dataset, export_preference_dataset, export_rag_corpus, export_skill_markdown, export_markdown_from_video_summary, build_auto_payload_from_project
from app.services.crud_service import list_export_records

router = APIRouter()


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


@router.post("/train-exports/auto")
def auto_build_train_export(payload: TrainExportRequest, db: Session = Depends(get_db)):
    project_id = payload.project_id or payload.source_asset_id or payload.source_task_id
    if not project_id:
        return {'error': 'project_id or source context required'}
    auto_rows = build_auto_payload_from_project(db, project_id, payload.export_type)
    payload = payload.model_copy(update={'payload': auto_rows})
    return create_train_export(payload, db)


@router.post("/train-exports")
def create_train_export(payload: TrainExportRequest, db: Session = Depends(get_db)):
    samples = payload.payload or []
    out_dir = payload.out_dir or "e:/2026/Envoy/tmp/exports"
    task_id = payload.source_task_id or payload.source_asset_id or 'manual'
    project_id = payload.project_id or payload.source_asset_id or 'unknown'
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
        return export_markdown_from_video_summary(samples, out_dir, task_id, payload.title or 'Video Markdown Report', payload.task_name, db=db, project_id=project_id)
    return {"error": "unsupported export type"}
