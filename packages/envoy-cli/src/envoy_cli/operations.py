from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, TypeVar

from . import storage
from .media import interprocess_file_lock


T = TypeVar("T")


class OperationConflict(ValueError):
    pass


def _root() -> Path:
    return storage.resolve_material_root()


def _write(path: Path, record: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".operation-", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _completed_at(record: dict) -> float:
    """Epoch seconds of a completed record, or 0.0 when it records no usable time."""
    value = record.get("completed_at")
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def run_idempotent(
    operation_id: str,
    *,
    fingerprint: str,
    execute: Callable[[], T],
    should_cache: Callable[[T], bool] | None = None,
    recover: Callable[[dict], T | None] | None = None,
    recovery_metadata: dict | None = None,
    ttl_seconds: int = 86_400,
    share_window_start: float | None = None,
) -> T:
    """Run ``execute`` at most once per distinct input under ``operation_id``.

    ``share_window_start`` makes the ledger usable as a single-flight hand-off for
    callers that bring no operation id of their own: a completed record is
    returned only when it completed after that wall-clock instant, which means the
    caller was already waiting for this work when it finished. An older completed
    record is re-executed rather than replayed, so a later read of the same source
    still observes the source as it is now.
    """
    operation_id = str(operation_id or "").strip()
    if not operation_id or len(operation_id) > 256:
        raise ValueError("operation_id must contain 1 to 256 characters")
    digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
    path = _root() / f"operation-{digest}.json"
    lock_path = path.with_suffix(".lock")
    with interprocess_file_lock(
        lock_path,
        max_wait_seconds=86_400,
        busy_message="The same Envoy operation is still running",
    ):
        record = None
        if path.is_file() and not path.is_symlink():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = None
        if isinstance(record, dict):
            if record.get("fingerprint") != fingerprint:
                raise OperationConflict("operation_id was already used for different input")
            if record.get("state") == "completed" and (
                should_cache is None or should_cache(record["result"])
            ):
                if share_window_start is None:
                    if float(record.get("expires_at") or 0) <= time.time():
                        raise OperationConflict(
                            "The completed operation has expired; choose a new operation_id"
                        )
                    return record["result"]
                if (
                    _completed_at(record) >= share_window_start
                    and float(record.get("expires_at") or 0) > time.time()
                ):
                    return record["result"]
            if record.get("state") in {"running", "retryable"} and recover is not None:
                try:
                    recovered = recover(record)
                except Exception:
                    recovered = None
                if recovered is not None:
                    result_is_cacheable = (
                        should_cache(recovered) if should_cache else True
                    )
                    recovered_record = {
                        "operation_id_hash": digest,
                        "fingerprint": fingerprint,
                        "state": "completed" if result_is_cacheable else "retryable",
                        "attempts": int(record.get("attempts") or 1),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "expires_at": max(
                            float(record.get("expires_at") or 0),
                            time.time() + max(int(ttl_seconds), 0),
                        ),
                    }
                    if result_is_cacheable:
                        recovered_record["result"] = recovered
                        recovered_record["completed_at"] = recovered_record.pop("updated_at")
                    _write(path, recovered_record)
                    return recovered
        attempts = int(record.get("attempts") or 0) + 1 if isinstance(record, dict) else 1
        now = datetime.now(timezone.utc).isoformat()
        expires_at = time.time() + max(int(ttl_seconds), 0)
        _write(path, {
            "operation_id_hash": digest,
            "fingerprint": fingerprint,
            "state": "running",
            "attempts": attempts,
            "recovery_metadata": recovery_metadata or {},
            "started_at": now,
            "expires_at": expires_at,
        })
        try:
            result = execute()
        except BaseException as exc:
            _write(path, {
                "operation_id_hash": digest,
                "fingerprint": fingerprint,
                "state": "retryable",
                "attempts": attempts,
                "recovery_metadata": recovery_metadata or {},
                "last_error": type(exc).__name__,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at,
            })
            raise
        result_is_cacheable = should_cache(result) if should_cache else True
        record = {
            "operation_id_hash": digest,
            "fingerprint": fingerprint,
            "state": "completed" if result_is_cacheable else "retryable",
            "attempts": attempts,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        }
        if result_is_cacheable:
            record["result"] = result
            record["completed_at"] = record.pop("updated_at")
        _write(path, record)
        return result
