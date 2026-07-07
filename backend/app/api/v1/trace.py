from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.trace_link import TraceLink

router = APIRouter()
MAX_TRACE_DEPTH = 6


def _serialize_trace(row, direction: Optional[str] = None, depth: Optional[int] = None):
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
    if depth is not None:
        payload["depth"] = depth
    return payload


def _serialize_directional_traces(outgoing, incoming):
    rows = []
    seen = set()
    for direction, collection in (("outgoing", outgoing), ("incoming", incoming)):
        for row, depth in collection:
            if row.id in seen:
                continue
            seen.add(row.id)
            rows.append(_serialize_trace(row, direction=direction, depth=depth))
    return rows


def _source_query(db: Session, source_type: str, source_id: str):
    return (
        db.query(TraceLink)
        .filter(TraceLink.source_type == source_type, TraceLink.source_id == source_id)
        .order_by(TraceLink.created_at.asc(), TraceLink.id.asc())
    )


def _target_query(db: Session, target_type: str, target_id: str):
    return (
        db.query(TraceLink)
        .filter(TraceLink.target_type == target_type, TraceLink.target_id == target_id)
        .order_by(TraceLink.created_at.asc(), TraceLink.id.asc())
    )


def _walk_trace(db: Session, start_type: str, start_id: str, direction: str, max_depth: int = MAX_TRACE_DEPTH):
    rows = []
    visited_links = set()
    visited_nodes = {(start_type, start_id, direction)}
    frontier = [(start_type, start_id)]
    depth = 1
    while frontier and depth <= max_depth:
        next_frontier = []
        for node_type, node_id in frontier:
            query = _source_query(db, node_type, node_id) if direction == "outgoing" else _target_query(db, node_type, node_id)
            for row in query.all():
                if row.id in visited_links:
                    continue
                visited_links.add(row.id)
                rows.append((row, depth))
                next_node = (
                    (row.target_type, row.target_id)
                    if direction == "outgoing"
                    else (row.source_type, row.source_id)
                )
                node_key = (*next_node, direction)
                if node_key not in visited_nodes:
                    visited_nodes.add(node_key)
                    next_frontier.append(next_node)
        frontier = next_frontier
        depth += 1
    return rows


@router.get("/trace/{source_type}/{source_id}")
def get_trace_chain(source_type: str, source_id: str, db: Session = Depends(get_db)):
    outgoing = _walk_trace(db, source_type, source_id, "outgoing")
    incoming = _walk_trace(db, source_type, source_id, "incoming")
    return _serialize_directional_traces(outgoing, incoming)


@router.get("/trace/target/{target_type}/{target_id}")
def get_reverse_trace_chain(target_type: str, target_id: str, db: Session = Depends(get_db)):
    rows = _walk_trace(db, target_type, target_id, "incoming")
    return [_serialize_trace(row, direction="incoming", depth=depth) for row, depth in rows]
