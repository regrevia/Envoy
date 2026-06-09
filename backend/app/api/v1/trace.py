from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.trace_link import TraceLink

router = APIRouter()


def _serialize_trace(row, direction: str | None = None):
    payload = {
        "id": row.id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "relation_type": row.relation_type,
    }
    if direction is not None:
        payload["direction"] = direction
    return payload


def _serialize_directional_traces(outgoing, incoming):
    rows = []
    seen = set()
    for direction, collection in (("outgoing", outgoing), ("incoming", incoming)):
        for row in collection:
            if row.id in seen:
                continue
            seen.add(row.id)
            rows.append(_serialize_trace(row, direction=direction))
    return rows


def _source_query(db: Session, source_type: str, source_id: str):
    return db.query(TraceLink).filter(TraceLink.source_type == source_type, TraceLink.source_id == source_id)


def _target_query(db: Session, target_type: str, target_id: str):
    return db.query(TraceLink).filter(TraceLink.target_type == target_type, TraceLink.target_id == target_id)


@router.get("/trace/{source_type}/{source_id}")
def get_trace_chain(source_type: str, source_id: str, db: Session = Depends(get_db)):
    outgoing = _source_query(db, source_type, source_id).all()
    incoming = _target_query(db, source_type, source_id).all()
    return _serialize_directional_traces(outgoing, incoming)


@router.get("/trace/target/{target_type}/{target_id}")
def get_reverse_trace_chain(target_type: str, target_id: str, db: Session = Depends(get_db)):
    rows = _target_query(db, target_type, target_id).all()
    return [_serialize_trace(row, direction="incoming") for row in rows]
