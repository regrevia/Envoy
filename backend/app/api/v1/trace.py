from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.trace_link import TraceLink

router = APIRouter()


def _serialize_trace(row):
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "relation_type": row.relation_type,
    }


@router.get("/trace/{source_type}/{source_id}")
def get_trace_chain(source_type: str, source_id: str, db: Session = Depends(get_db)):
    rows = db.query(TraceLink).filter(TraceLink.source_type == source_type, TraceLink.source_id == source_id).all()
    return [_serialize_trace(row) for row in rows]


@router.get("/trace/target/{target_type}/{target_id}")
def get_reverse_trace_chain(target_type: str, target_id: str, db: Session = Depends(get_db)):
    rows = db.query(TraceLink).filter(TraceLink.target_type == target_type, TraceLink.target_id == target_id).all()
    return [_serialize_trace(row) for row in rows]
