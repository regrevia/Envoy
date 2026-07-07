from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.generated_sample import GeneratedSample

router = APIRouter()


def _serialize_generated_sample(row: GeneratedSample):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "source_unit_id": row.source_unit_id,
        "sample_type": row.sample_type,
        "input_text": row.input_text,
        "output_text": row.output_text,
        "quality_score": row.quality_score,
        "created_at": row.created_at,
    }


@router.get("/generated-samples")
def list_generated_samples(project_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(GeneratedSample)
    if project_id:
        query = query.filter(GeneratedSample.project_id == project_id)
    rows = query.order_by(GeneratedSample.created_at.desc()).all()
    return [_serialize_generated_sample(row) for row in rows]


@router.get("/generated-samples/{sample_id}")
def get_generated_sample(sample_id: str, db: Session = Depends(get_db)):
    row = db.query(GeneratedSample).filter(GeneratedSample.id == sample_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Generated sample not found")
    return _serialize_generated_sample(row)
