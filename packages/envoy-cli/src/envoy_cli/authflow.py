"""Persisted authentication requests.

A site that wants a signed-in session stops a read with ``auth_required``.
That stop has to be a step a human can act on and a later run can find
again, so the request is written to disk with a stable id, the link that
triggered it, and the reason. Nothing here stores credentials: the record
holds a site, a URL, a status and the verification outcome.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ACTION_TTL_SECONDS = 24 * 3600
STATUSES = (
    "pending",
    "verifying",
    "ok",
    "failed",
    "expired",
    "canceled",
)


def auth_root() -> Path:
    override = os.environ.get("ENVOY_AUTH_DIR")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("ENVOY_LOCAL_DATA_DIR")
    if local:
        return Path(local).expanduser() / "auth"
    return Path.home() / ".envoy" / "auth"


def actions_dir() -> Path:
    path = auth_root() / "actions"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _action_path(action_id: str) -> Path:
    return actions_dir() / f"{action_id}.json"


def _now() -> float:
    return time.time()


def _iso(value: float | None = None) -> str:
    return datetime.fromtimestamp(value or _now(), timezone.utc).isoformat()


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _host_of(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


def create_auth_action(
    source_url: str,
    *,
    purpose: str = "read",
    reason: str = "",
    resume_token: str | None = None,
    allow_visible: bool = True,
) -> dict:
    """Record one pause. Reuse a live request for the same link and purpose."""

    host = _host_of(source_url)
    for existing in list_auth_actions():
        if (
            existing.get("status") in {"pending", "verifying"}
            and existing.get("host") == host
            and existing.get("source_url") == str(source_url)
            and existing.get("purpose") == purpose
        ):
            return existing
    action_id = "auth-" + secrets.token_hex(8)
    now = _now()
    payload = {
        "action_id": action_id,
        "type": "authenticate",
        "status": "pending",
        "host": host,
        "source_url": str(source_url),
        "purpose": purpose,
        "reason": str(reason or "")[:400],
        "resume_token": resume_token or secrets.token_urlsafe(18),
        "allow_visible": bool(allow_visible),
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "expires_at": now + ACTION_TTL_SECONDS,
        "attempts": 0,
        "last_verification": None,
    }
    _write(_action_path(action_id), payload)
    return payload


def load_auth_action(action_id: str) -> dict | None:
    action_id = str(action_id or "").strip()
    if not action_id or "/" in action_id or action_id.startswith("."):
        return None
    path = _action_path(action_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") in {"pending", "verifying"} and float(
        payload.get("expires_at") or 0
    ) <= _now():
        payload["status"] = "expired"
        payload["updated_at"] = _iso()
        try:
            _write(path, payload)
        except OSError:
            pass
    return payload


def list_auth_actions(*, status: str | None = None) -> list[dict]:
    items: list[dict] = []
    for path in sorted(actions_dir().glob("auth-*.json")):
        action = load_auth_action(path.stem)
        if not action:
            continue
        if status and action.get("status") != status:
            continue
        items.append(action)
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items


def update_auth_action(action_id: str, **fields) -> dict | None:
    action = load_auth_action(action_id)
    if not action:
        return None
    action.update(fields)
    action["updated_at"] = _iso()
    _write(_action_path(str(action["action_id"])), action)
    return action


def resolve_resume_token(token: str) -> dict | None:
    token = str(token or "").strip()
    if not token:
        return None
    for action in list_auth_actions():
        if action.get("resume_token") == token:
            return action
    return None


def record_verification(action_id: str, verification: dict) -> dict | None:
    verification = dict(verification or {})
    verification["checked_at"] = _iso()
    usable = bool(verification.get("page_access")) and bool(
        verification.get("headless_reusable")
    )
    return update_auth_action(
        action_id,
        status="ok" if usable else "failed",
        last_verification=verification,
    )


def invalidate_actions(*, host: str | None = None, reason: str = "credentials_cleared") -> int:
    """Mark live requests unusable after their credentials were removed."""

    host = (host or "").lower()
    changed = 0
    for action in list_auth_actions():
        if action.get("status") not in {"pending", "verifying", "ok", "failed"}:
            continue
        if host and action.get("host") != host:
            continue
        update_auth_action(
            action["action_id"],
            status="canceled",
            invalidated_reason=reason,
        )
        changed += 1
    return changed


def auth_status() -> dict:
    actions = list_auth_actions()
    pending = [
        {
            "action_id": action["action_id"],
            "host": action.get("host"),
            "purpose": action.get("purpose"),
            "status": action.get("status"),
            "created_at": action.get("created_at"),
        }
        for action in actions
        if action.get("status") in {"pending", "verifying"}
    ]
    return {"pending": pending, "total": len(actions)}
