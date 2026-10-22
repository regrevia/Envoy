from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, TypeVar

from .media import interprocess_file_lock


T = TypeVar("T")


class OperationConflict(ValueError):
    pass


def _root() -> Path:
    configured = os.getenv("ENVOY_LOCAL_DATA_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".envoy" / "materials"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


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


def run_idempotent(
    operation_id: str,
    *,
    fingerprint: str,
    execute: Callable[[], T],
    ttl_seconds: int = 86_400,
) -> T:
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
            if record.get("state") == "completed":
                if float(record.get("expires_at") or 0) <= time.time():
                    raise OperationConflict(
                        "The completed operation has expired; choose a new operation_id"
                    )
                return record["result"]
        attempts = int(record.get("attempts") or 0) + 1 if isinstance(record, dict) else 1
        now = datetime.now(timezone.utc).isoformat()
        expires_at = time.time() + max(int(ttl_seconds), 0)
        _write(path, {
            "operation_id_hash": digest,
            "fingerprint": fingerprint,
            "state": "running",
            "attempts": attempts,
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
                "last_error": type(exc).__name__,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at,
            })
            raise
        _write(path, {
            "operation_id_hash": digest,
            "fingerprint": fingerprint,
            "state": "completed",
            "attempts": attempts,
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        })
        return result
