"""Durable long-task checkpoints on disk.

The reader already keeps one material resumable across processes: a material id
plus the ranges it has delivered. What it does not keep is the task around it,
so a model that loses its context, or another model taking over, has no way
back to the material it already paid for. This module stores that task record
next to the materials, and hands back the exact next request.

The record is the checkpoint only. A material lease, an expiry, and a model
session id stay out of it: those are borrowed or isolated, not identity.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import uuid

from envoy_core import long_task as lt

from . import storage
from .media import interprocess_file_lock


class TaskError(ValueError):
    """The request cannot be served from the checkpoint that exists."""


def _task_root() -> Path:
    return storage.resolve_material_root()


def _task_path(task_id: str) -> Path:
    identity = str(task_id or "").strip()
    if not identity or len(identity) > 128:
        raise TaskError("Invalid task_id")
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return _task_root() / f"task-{digest}.json"


def _locked(path: Path):
    return interprocess_file_lock(
        path.with_suffix(".lock"),
        max_wait_seconds=30,
        busy_message="Task checkpoint is busy; retry the operation",
    )


def _read(path: Path) -> dict | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".task-", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load(task_id: str) -> tuple[Path, lt.LongTask]:
    path = _task_path(task_id)
    stored = _read(path)
    if stored is None:
        raise TaskError(f"No task checkpoint for {task_id}")
    try:
        return path, lt.from_checkpoint(stored)
    except lt.TaskCheckpointError as exc:
        raise TaskError(str(exc)) from exc


def _save(path: Path, task: lt.LongTask, *, now: str | None = None) -> dict:
    task = replace(task, updated_at=now or task.updated_at)
    payload = lt.to_checkpoint(task)
    _write(path, payload)
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_task(goal: str, sources: list[str], *, task_id: str | None = None) -> dict:
    identity = (task_id or "").strip() or f"task-{uuid.uuid4().hex[:12]}"
    try:
        task = lt.new_task(identity, goal, sources, now=_now())
    except lt.TaskCheckpointError as exc:
        raise TaskError(str(exc)) from exc
    path = _task_path(identity)
    with _locked(path):
        if path.is_file():
            raise TaskError(f"Task {identity} already exists")
        return _save(path, task)


def _with_material_progress(payload: dict, task: lt.LongTask) -> dict:
    """Add the resume-facing view of each material to a checkpoint payload.

    The stored record stays exactly what the core serialized: adding a lease,
    an expiry or a model session id here would make the checkpoint depend on
    borrowed state. Everything added below is derived from the record itself.
    """
    by_url = {item.url: item for item in task.materials}
    materials = payload.get("materials")
    if isinstance(materials, list):
        for item in materials:
            if not isinstance(item, dict):
                continue
            material = by_url.get(item.get("url"))
            if material is None:
                continue
            item["kind"] = material.kind
            item["media"] = material.media_progress()
            item["remaining_seconds"] = material.remaining_seconds()
            # The state word is not the signal: a video whose transcript covers
            # the whole timeline can still carry "partial" from its last ready
            # receipt. A caller must read these two instead.
            item["is_finished"] = material.is_finished()
            item["resume_kind"] = material.resume_kind()
    return payload


def get_task(task_id: str) -> dict:
    _, task = _load(task_id)
    return _with_material_progress(lt.to_checkpoint(task), task)


def apply_receipt(
    task_id: str,
    url: str,
    receipt: dict,
    *,
    offset: int,
    receipt_id: str | None = None,
) -> dict:
    """Fold one real receipt into the task, without inventing any delivery.

    ``offset`` is the text offset the receipt's text modality starts at: the
    character position in the transcript for a media receipt, just like the
    character position in a document for a text one. A fresh media read starts
    at 0, so 0 is a real offset and never a reason to refuse the receipt.
    """
    identity = str(
        receipt_id
        or receipt.get("receipt_id")
        or receipt.get("operation_id")
        or ""
    ).strip()
    path = _task_path(task_id)
    with _locked(path):
        path, task = _load(task_id)
        try:
            material = task.material(url)
        except KeyError as exc:
            raise TaskError(f"Task {task_id} has no source {url}") from exc
        if identity and identity in task.applied_receipts:
            # A retried apply reports the state it already reached instead of
            # folding the same characters (and the same cost) in again.
            payload = _with_material_progress(lt.to_checkpoint(task), task)
            payload["receipt_id"] = identity
            payload["duplicate"] = True
            return payload
        try:
            delivered = material.with_delivery(receipt, offset=offset)
        except lt.TaskBlocked as exc:
            task = task.with_material(replace(material, state=lt.BLOCKED, errors=(exc.code,)))
            task = task.record_error(f"{url}: {exc.code}").record_receipt(identity)
            payload = _save(path, task, now=_now())
            payload["blocked"] = {"code": exc.code, "message": str(exc)}
            if identity:
                payload["receipt_id"] = identity
            return payload
        saved = task.with_material(delivered).record_receipt(identity)
        payload = _save(path, saved, now=_now())
        if identity:
            payload["receipt_id"] = identity
        return payload


def next_request(
    task_id: str,
    url: str,
    *,
    request_id: str | None = None,
    max_chars: int = 4000,
    max_total_response_chars: int = 48000,
    observed_revision: str | None = None,
) -> dict:
    """The request to issue next, or why resuming would be dishonest."""
    _, task = _load(task_id)
    try:
        material = task.material(url)
    except KeyError as exc:
        raise TaskError(f"Task {task_id} has no source {url}") from exc
    try:
        request = material.resume_request(
            request_id=request_id or f"task-{uuid.uuid4().hex[:8]}",
            max_chars=max_chars,
            max_total_response_chars=max_total_response_chars,
            observed_revision=observed_revision,
            goal=task.goal,
        )
    except lt.TaskBlocked as exc:
        return {
            "task_id": task_id,
            "source": url,
            "result": "blocked",
            "error": {"code": exc.code, "message": str(exc)},
            "delivered_chars": material.delivered_chars,
            "remaining_chars": material.remaining_chars(),
        }
    return {
        "task_id": task_id,
        "source": url,
        "result": "ready",
        "action": material.resume_kind(),
        "media": material.media_progress(),
        "remaining_seconds": material.remaining_seconds(),
        "delivered_chars": material.delivered_chars,
        "remaining_chars": material.remaining_chars(),
        "source_revision": material.source_revision,
        "request": request,
    }


def list_tasks() -> list[dict]:
    root = _task_root()
    if not root.is_dir():
        return []
    tasks = []
    for path in sorted(root.glob("task-*.json")):
        stored = _read(path)
        if stored is None:
            continue
        try:
            task = lt.from_checkpoint(stored)
        except lt.TaskCheckpointError:
            continue
        tasks.append(
            {
                "task_id": task.task_id,
                "goal": task.goal,
                "state": task.state,
                "sources": len(task.materials),
                "delivered_chars": sum(item.delivered_chars for item in task.materials),
                "updated_at": task.updated_at,
                "materials": _with_material_progress(lt.to_checkpoint(task), task)[
                    "materials"
                ],
            }
        )
    return tasks


def handle(payload: dict) -> dict:
    """One request in, one JSON answer out; the CLI face of this module."""
    action = str(payload.get("action") or "")
    if action == "create":
        goal = str(payload.get("goal") or "")
        sources = payload.get("sources") or payload.get("urls") or []
        if not isinstance(sources, list):
            raise TaskError("sources must be a list of URLs")
        return create_task(
            goal,
            [str(source) for source in sources],
            task_id=payload.get("task_id"),
        )
    if action == "get":
        return get_task(str(payload.get("task_id") or ""))
    if action == "list":
        return {"tasks": list_tasks()}
    if action == "apply":
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict):
            raise TaskError("apply needs a receipt object")
        offset = payload.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise TaskError("apply needs a non-negative integer offset")
        return apply_receipt(
            str(payload.get("task_id") or ""),
            str(payload.get("source") or payload.get("url") or ""),
            receipt,
            offset=offset,
            receipt_id=payload.get("receipt_id") or payload.get("operation_id"),
        )
    if action == "next":
        return next_request(
            str(payload.get("task_id") or ""),
            str(payload.get("source") or payload.get("url") or ""),
            request_id=payload.get("request_id"),
            max_chars=int(payload.get("max_chars") or 4000),
            max_total_response_chars=int(payload.get("max_total_response_chars") or 48000),
            observed_revision=payload.get("observed_revision"),
        )
    raise TaskError(f"Unknown action {action!r}")
