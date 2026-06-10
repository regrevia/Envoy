from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.semantic_unit import SemanticUnit

router = APIRouter()


def _semantic_unit_payload(row: SemanticUnit):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "asset_id": row.asset_id,
        "segment_id": row.segment_id,
        "unit_type": row.unit_type,
        "title": row.title,
        "content": row.content,
        "labels": row.labels,
        "confidence": row.confidence,
        "created_at": row.created_at,
    }


@router.get("/semantic-units")
def list_semantic_units(project_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(SemanticUnit)
    if project_id:
        query = query.filter(SemanticUnit.project_id == project_id)
    rows = query.order_by(SemanticUnit.created_at.desc()).all()
    return [_semantic_unit_payload(row) for row in rows]


@router.get("/semantic-units/{semantic_unit_id}")
def get_semantic_unit(semantic_unit_id: str, db: Session = Depends(get_db)):
    row = db.query(SemanticUnit).filter(SemanticUnit.id == semantic_unit_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Semantic unit not found")
    return _semantic_unit_payload(row)
