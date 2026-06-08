from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.semantic_unit import SemanticUnit

router = APIRouter()


@router.get("/semantic-units")
def list_semantic_units(db: Session = Depends(get_db)):
    rows = db.query(SemanticUnit).order_by(SemanticUnit.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "asset_id": row.asset_id,
            "unit_type": row.unit_type,
            "title": row.title,
            "content": row.content,
            "labels": row.labels,
            "confidence": row.confidence,
        }
        for row in rows
    ]


@router.get("/semantic-units/{semantic_unit_id}")
def get_semantic_unit(semantic_unit_id: str, db: Session = Depends(get_db)):
    row = db.query(SemanticUnit).filter(SemanticUnit.id == semantic_unit_id).first()
    if row is None:
        return None
    return {
        "id": row.id,
        "project_id": row.project_id,
        "asset_id": row.asset_id,
        "unit_type": row.unit_type,
        "title": row.title,
        "content": row.content,
        "labels": row.labels,
        "confidence": row.confidence,
    }
