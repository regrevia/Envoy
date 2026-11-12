"""Run many links as one resumable queue.

`envoy read` answers one link. `envoy collect` walks one creator's profile.
`envoy batch` is the general queue: a plain list of links, each with its own
durable state, run with a bounded budget and a bounded number of readers, and
resumable after a crash, a spent budget, or a site that asks a person to log
in.

The guarantees this module keeps:

* every item writes its own state before the next one starts, so an interrupted
  run loses at most the item that was in flight;
* an item that raises never sinks the batch - it is recorded and skipped;
* the run budget is charged per item and carried across runs, so resuming does
  not hand out a fresh allowance to work already done;
* a login wall is a pause with a handoff, not a failure: the batch remembers
  the action, stops scheduling items from that host, and the same list resumes
  after the person has signed in;
* nothing here invents text: an item records only what `read_public_page`
  actually returned, plus the material id that can be expanded later.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from envoy_core.schemas.read_v1 import Budget, ReadRequest

from . import auth_session, authflow, storage
from .reader import read_public_page

SCHEMA_VERSION = "envoy.batch/1"
BATCH_DIR = "batches"
STATE_FILE = "state.json"
REPORT_FILE = "report.md"

DEFAULT_MAX_WORK_SECONDS = 600
DEFAULT_PER_ITEM_SECONDS = 120
DEFAULT_CONCURRENCY = 1
MAX_CONCURRENCY = 4
DEFAULT_MAX_ATTEMPTS = 2

#: An item in one of these states needs no further work from a plain resume.
DONE_STATES = {"completed"}
#: An item in one of these states is retried on an explicit opt-in.
LOGIN_STATES = {"needs_login"}
FAILED_STATES = {"failed"}
_UNFINISHED = {"pending", "in_progress", "needs_login", "failed", "blocked", "partial"}

AUTH_CODES = {"auth_required", "challenge_required"}
LOGIN_PURPOSES = {"read_source", "read"}


class BatchError(ValueError):
    """A batch cannot be created, loaded, or resumed as asked."""


def _clock() -> float:
    return time.perf_counter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def batch_root() -> Path:
    return Path(storage.resolve_material_root()) / BATCH_DIR


def batch_dir(batch_id: str) -> Path:
    return batch_root() / _safe_batch_id(batch_id)


def state_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / STATE_FILE


def report_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / REPORT_FILE


def _safe_batch_id(batch_id: str) -> str:
    value = str(batch_id or "").strip()
    if not value or "/" in value or "\\" in value or value.startswith("."):
        raise BatchError("A batch id like batch-0123456789ab is required")
    return value


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path) + ".lock", "a+b")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def normalize_url(url: str) -> str:
    """Return the URL a batch stores; refuse anything that is not http(s)."""

    value = str(url or "").strip()
    if not value:
        raise BatchError("A batch item needs a URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BatchError(f"Not a public http(s) link: {value}")
    return value


def batch_id_for(urls: list[str]) -> str:
    """Stable identity of one ordered list, so re-running it resumes."""

    normalized = [normalize_url(url) for url in urls]
    digest = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()[:12]
    return f"batch-{digest}"


def _clean_urls(urls: Any) -> list[str]:
    if isinstance(urls, str):
        urls = [urls]
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        url = normalize_url(raw)
        if url in seen:
            continue
        seen.add(url)
        cleaned.append(url)
    if not cleaned:
        raise BatchError("A batch needs at least one link")
    return cleaned


def create_batch(
    urls: Any,
    *,
    batch_id: str | None = None,
    goal: str = "",
    max_work_seconds: int = DEFAULT_MAX_WORK_SECONDS,
    per_item_seconds: int = DEFAULT_PER_ITEM_SECONDS,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    fresh: bool = False,
) -> dict:
    """Create the durable queue for one list of links, or return the existing one.

    The same list resolves to the same batch id, so calling this again is how a
    run resumes. ``fresh=True`` is the explicit "start over" for that list.
    """

    cleaned = _clean_urls(urls)
    identity = _safe_batch_id(batch_id or batch_id_for(cleaned))
    path = state_path(identity)
    with _locked(path):
        existing = _load(identity)
        if existing is not None and not fresh:
            return existing
        attempts = max(1, min(int(max_attempts), 5))
        now = _now()
        state = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": identity,
            "created_at": now,
            "updated_at": now,
            "status": "pending",
            "reason": "",
            "goal": str(goal or ""),
            "concurrency": max(1, min(int(concurrency), MAX_CONCURRENCY)),
            "budget": {
                "max_work_seconds": max(1, int(max_work_seconds)),
                "per_item_seconds": max(1, int(per_item_seconds)),
                "max_attempts": attempts,
            },
            "usage": {"work_seconds": 0.0, "items_read": 0, "items_failed": 0},
            "items": [
                {
                    "index": index,
                    "url": url,
                    "host": (urlparse(url).hostname or "").lower(),
                    "status": "pending",
                    "attempts": 0,
                    "result": None,
                    "material_id": None,
                    "error_code": None,
                    "message": "",
                    "excerpt": "",
                    "work_seconds": 0.0,
                    "idempotency_key": f"batch:{identity}:{index}",
                    "updated_at": None,
                }
                for index, url in enumerate(cleaned)
            ],
            "login": None,
        }
        _write_json(path, state)
        _write_text(report_path(identity), render_report(state))
        return state


def _load(batch_id: str) -> dict | None:
    path = state_path(batch_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BatchError(
            f"Batch {batch_id} was written by another version of Envoy"
        )
    return payload


def load_batch(batch_id: str) -> dict:
    identity = _safe_batch_id(batch_id)
    state = _load(identity)
    if state is None:
        raise BatchError(f"No batch named {identity}")
    return state


def list_batches() -> list[dict]:
    root = batch_root()
    rows: list[dict] = []
    for path in sorted(root.glob("*/" + STATE_FILE)):
        identity = path.parent.name
        try:
            state = _load(identity)
        except BatchError:
            continue
        if not state:
            continue
        items = state.get("items") or []
        rows.append(
            {
                "batch_id": identity,
                "status": state.get("status"),
                "reason": state.get("reason", ""),
                "total": len(items),
                "completed": sum(1 for item in items if item.get("status") in DONE_STATES),
                "needs_login": sum(
                    1 for item in items if item.get("status") in LOGIN_STATES
                ),
                "failed": sum(1 for item in items if item.get("status") in FAILED_STATES),
                "work_seconds": round(float((state.get("usage") or {}).get("work_seconds") or 0), 3),
                "updated_at": state.get("updated_at"),
                "state_file": str(path),
            }
        )
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows


def _recover(state: dict) -> int:
    """An item left ``in_progress`` was interrupted; it is pending again."""

    recovered = 0
    for item in state.get("items") or []:
        if item.get("status") == "in_progress":
            item["status"] = "pending"
            item["message"] = "interrupted; queued again"
            recovered += 1
    return recovered


def _summary(
    state: dict,
    *,
    read_now: int,
    skipped: int,
    resumed: bool,
    recovered: int,
) -> dict:
    items = state.get("items") or []
    pending = [
        {
            "index": item.get("index"),
            "url": item.get("url"),
            "status": item.get("status"),
            "message": item.get("message", ""),
        }
        for item in items
        if item.get("status") not in DONE_STATES
    ]
    return {
        "ok": True,
        "batch_id": state.get("batch_id"),
        "status": state.get("status"),
        "reason": state.get("reason", ""),
        "state_file": str(state_path(str(state.get("batch_id")))),
        "report": str(report_path(str(state.get("batch_id")))),
        "total": len(items),
        "completed": sum(1 for item in items if item.get("status") in DONE_STATES),
        "needs_login": [
            row for row in pending if row.get("status") in LOGIN_STATES
        ],
        "failed": [row for row in pending if row.get("status") in FAILED_STATES],
        "pending": pending,
        "read_now": read_now,
        "skipped": skipped,
        "recovered": recovered,
        "resumed": resumed,
        "login": state.get("login"),
        "usage": dict(state.get("usage") or {}),
        "next": _next_step(state),
    }


def _next_step(state: dict) -> str:
    batch_id = str(state.get("batch_id") or "")
    if state.get("status") == "completed":
        return f"envoy batch show {batch_id}"
    login = state.get("login") or {}
    if login:
        return f"envoy batch login {batch_id} --wait"
    return f"envoy batch run --resume {batch_id}"


def _classify(response) -> tuple[str, str, str | None]:
    """Map one read response onto a queue state without losing the reason."""

    result = str(getattr(response.result, "value", response.result))
    code = None
    message = ""
    errors = getattr(response, "errors", None) or []
    if errors:
        first = errors[0].model_dump(mode="json")
        code = str(first.get("code") or "") or None
        message = str(first.get("message") or "")
    if result == "ready":
        return "completed", message, code
    if result == "blocked":
        if code in AUTH_CODES:
            return "needs_login", message or "the site asks a person to sign in", code
        return "blocked", message, code
    if result in {"partial", "needs_host"}:
        return "partial", message, code
    return "skipped", message, code


def _auth_pause(state: dict, item: dict, response) -> dict | None:
    errors = getattr(response, "errors", None) or []
    if not errors:
        return None
    error = errors[0].model_dump(mode="json")
    code = str(error.get("code") or "")
    if code not in AUTH_CODES:
        return None
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    action = details.get("needs_user_action")
    action = action if isinstance(action, dict) else {}
    return {
        "host": item.get("host") or "",
        "url": item.get("url"),
        "index": item.get("index"),
        "reason": code,
        "action_id": action.get("action_id"),
        "resume_token": action.get("resume_token"),
        "kind": action.get("kind"),
        "open_command": action.get("open_command"),
        "question": action.get("question"),
    }


def render_report(state: dict) -> str:
    items = state.get("items") or []
    completed = sum(1 for item in items if item.get("status") in DONE_STATES)
    lines = [
        f"# 批量读取：`{state.get('batch_id')}`",
        "",
        f"- 条目：{completed}/{len(items)} 已完成",
        f"- 状态：{state.get('status')}"
        + (f"（{state['reason']}）" if state.get("reason") else ""),
        f"- 累计工作：{round(float((state.get('usage') or {}).get('work_seconds') or 0), 1)}s",
    ]
    login = state.get("login") or {}
    if login:
        lines.append(f"- 待登录：{login.get('host')} · 动作 `{login.get('action_id')}`")
        lines.append(f"- 下一步：`envoy batch login {state.get('batch_id')} --wait`")
    lines.extend(["", "| # | 状态 | 来源 | material | 说明 |", "| --- | --- | --- | --- | --- |"])
    for item in items:
        message = str(item.get("message") or "").replace("|", "／").replace("\n", " ")
        lines.append(
            f"| {item.get('index')} | {item.get('status')} | {item.get('url')} "
            f"| {item.get('material_id') or '—'} | {message} |"
        )
    lines.extend(
        [
            "",
            "本文件只记录队列状态与 Envoy 实际读到的 material。",
            "任何一条 `completed` 都可以用 `envoy expand` 继续读原文。",
            "",
        ]
    )
    return "\n".join(lines)


def _update_item(
    state: dict,
    index: int,
    *,
    lock: threading.Lock,
    **fields: Any,
) -> dict:
    with lock:
        item = None
        for candidate in state.get("items") or []:
            if candidate.get("index") == index:
                item = candidate
                break
        if item is None:
            raise BatchError(f"Batch has no item {index}")
        item.update(fields)
        item["updated_at"] = _now()
        state["updated_at"] = item["updated_at"]
        _write_json(state_path(str(state["batch_id"])), state)
        return item


def _item_index(state: dict, item: dict) -> int:
    return int(item.get("index") or 0)


def run_batch(
    batch_id: str | None = None,
    urls: Any = None,
    *,
    retry_login: bool = False,
    retry_failed: bool = False,
    max_work_seconds: int | None = None,
    concurrency: int | None = None,
    reader: Callable[[ReadRequest], Any] | None = None,
) -> dict:
    """Run or resume a batch, returning one summary.

    ``reader`` exists so tests (and callers with a custom pipeline) can supply
    the read function; the default is Envoy's own public page reader.
    """

    if batch_id is None:
        if not urls:
            raise BatchError("A batch needs a batch id or a list of links")
        state = create_batch(
            urls,
            max_work_seconds=max_work_seconds or DEFAULT_MAX_WORK_SECONDS,
            concurrency=concurrency or DEFAULT_CONCURRENCY,
        )
        batch_id = str(state["batch_id"])
    identity = _safe_batch_id(batch_id)
    read = reader or read_public_page
    path = state_path(identity)
    with _locked(path.with_name("run")):
        state = load_batch(identity)
        resumed = state.get("status") not in {None, "pending"}
        recovered = _recover(state)
        if recovered:
            _write_json(path, state)
        if max_work_seconds is not None:
            state["budget"]["max_work_seconds"] = max(1, int(max_work_seconds))
        if concurrency is not None:
            state["concurrency"] = max(1, min(int(concurrency), MAX_CONCURRENCY))
        items = state.get("items") or []
        budget = state.get("budget") or {}
        per_item = max(1, int(budget.get("per_item_seconds") or DEFAULT_PER_ITEM_SECONDS))
        max_attempts = max(1, int(budget.get("max_attempts") or DEFAULT_MAX_ATTEMPTS))
        remaining = max(0.0, float(budget.get("max_work_seconds") or DEFAULT_MAX_WORK_SECONDS) - float(
            (state.get("usage") or {}).get("work_seconds") or 0
        ))

        selected: list[dict] = []
        skipped = 0
        paused_hosts: set[str] = set()
        known = state.get("login") or {}
        if known.get("host") and not retry_login:
            paused_hosts.add(str(known["host"]))
        for item in items:
            status = item.get("status")
            if status in DONE_STATES:
                skipped += 1
                continue
            if item.get("host") in paused_hosts:
                skipped += 1
                continue
            if status in LOGIN_STATES and not retry_login:
                skipped += 1
                continue
            if status in FAILED_STATES:
                if not retry_failed or int(item.get("attempts") or 0) >= max_attempts:
                    skipped += 1
                    continue
            if status == "blocked" and not retry_failed:
                skipped += 1
                continue
            selected.append(item)

        state["status"] = "running"
        state["reason"] = ""
        if retry_login:
            state["login"] = None
        _write_json(path, state)

        lock = threading.Lock()
        stop = threading.Event()
        deadline = _clock() + remaining
        read_now = 0

        def work(item: dict) -> None:
            nonlocal read_now
            if stop.is_set() or item.get("host") in paused_hosts:
                return
            index = _item_index(state, item)
            available = deadline - _clock()
            if available <= 0.05:
                stop.set()
                return
            attempt_budget = max(1, min(int(round(available)) or 1, per_item))
            _update_item(
                state,
                index,
                lock=lock,
                status="in_progress",
                attempts=int(item.get("attempts") or 0) + 1,
            )
            started = _clock()
            response = None
            failure: str | None = None
            try:
                response = read(
                    ReadRequest(
                        request_id=f"{state['batch_id']}-{index}",
                        idempotency_key=str(item.get("idempotency_key") or "")[:256],
                        source={"url": str(item.get("url") or "")},
                        goal=str(state.get("goal") or ""),
                        budget=Budget(max_work_seconds=attempt_budget),
                    )
                )
            except Exception as exc:  # one bad link must not sink the batch
                failure = f"{type(exc).__name__}: {str(exc)[:200]}"
            elapsed = _clock() - started
            pause = None if response is None else _auth_pause(state, item, response)
            fields: dict[str, Any] = {
                "work_seconds": round(float(item.get("work_seconds") or 0) + elapsed, 3),
                "material_id": getattr(response, "material_id", None),
            }
            if failure is not None:
                attempts = int(item.get("attempts") or 0)
                fields.update(
                    status="failed" if attempts >= max_attempts else "pending",
                    message=failure,
                    error_code="reader_error",
                )
            else:
                final_state, message, code = _classify(response)
                fields.update(
                    status=final_state,
                    result=str(getattr(response.result, "value", response.result)),
                    message=message,
                    error_code=code,
                    excerpt=_excerpt(response),
                )
                if pause is not None:
                    pause["action_id"] = pause.get("action_id") or (state.get("login") or {}).get(
                        "action_id"
                    )
                    pause["resume_token"] = pause.get("resume_token") or (state.get("login") or {}).get(
                        "resume_token"
                    )
            item_row = _update_item(state, index, lock=lock, **fields)
            with lock:
                state["usage"]["work_seconds"] = round(
                    float((state.get("usage") or {}).get("work_seconds") or 0) + elapsed, 3
                )
                state["usage"]["items_read"] = int(
                    (state.get("usage") or {}).get("items_read") or 0
                ) + 1
                if item_row.get("status") in FAILED_STATES:
                    state["usage"]["items_failed"] = int(
                        (state.get("usage") or {}).get("items_failed") or 0
                    ) + 1
                if pause is not None:
                    state["login"] = pause
                    if pause.get("host"):
                        paused_hosts.add(str(pause["host"]))
                    _write_json(path, state)
            read_now += 1
            if _clock() >= deadline:
                stop.set()

        workers = max(1, min(int(state.get("concurrency") or 1), MAX_CONCURRENCY, len(selected) or 1))
        if workers > 1 and len(selected) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(work, selected))
        else:
            for item in selected:
                work(item)
                if stop.is_set():
                    break

        items = state.get("items") or []
        pending = [item for item in items if item.get("status") not in DONE_STATES]
        budget_left = float((state.get("budget") or {}).get("max_work_seconds") or 0) - float(
            (state.get("usage") or {}).get("work_seconds") or 0
        )
        if not pending:
            state["status"] = "completed"
            state["reason"] = ""
        elif any(item.get("status") in LOGIN_STATES for item in pending) or state.get("login"):
            state["status"] = "paused"
            state["reason"] = "login_required"
        elif budget_left <= 0:
            state["status"] = "paused"
            state["reason"] = "budget"
        else:
            state["status"] = "paused"
            state["reason"] = "incomplete"
        state["updated_at"] = _now()
        _write_json(path, state)
        _write_text(report_path(identity), render_report(state))
        return _summary(
            state,
            read_now=read_now,
            skipped=skipped,
            resumed=resumed,
            recovered=recovered,
        )


def _excerpt(response) -> str:
    """The first stretch of what was actually read, capped for the queue record."""

    text = ""
    content = getattr(response, "content", None)
    if isinstance(content, dict):
        text = str(content.get("text") or "")
    if not text:
        for block in getattr(response, "blocks", None) or []:
            payload = block.get("content") if isinstance(block, dict) else None
            if isinstance(payload, dict) and payload.get("text"):
                text = str(payload["text"])
                break
    text = " ".join(text.split())
    return text[:400]


def cancel_batch(batch_id: str) -> dict:
    state = load_batch(batch_id)
    state["status"] = "canceled"
    state["reason"] = "canceled by user"
    state["updated_at"] = _now()
    _write_json(state_path(state["batch_id"]), state)
    return {"batch_id": state["batch_id"], "status": "canceled"}


def open_login_handoff(
    url: str,
    *,
    timeout_seconds: int = 300,
    visible: bool = False,
) -> dict:
    """Open the site's own sign-in page so a person can scan a code or type a number.

    Nothing is submitted here: Envoy opens the page in its own browser profile,
    waits for the first picture, and returns the path to it. The person signs
    in; the cookies then live in Envoy's profile and every later read of that
    site reuses them.
    """

    source_url = normalize_url(url)
    try:
        action = authflow.create_auth_action(
            source_url,
            purpose="read_source",
            reason="login_required_for_batch",
        )
        payload = auth_session.start(
            source_url,
            action_id=action["action_id"],
            purpose="read_source",
            timeout_seconds=max(10, int(timeout_seconds)),
            visible=bool(visible),
        )
    except Exception as exc:
        return {
            "status": "unavailable",
            "url": source_url,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "next": "envoy browser setup",
        }
    return {
        "status": str(payload.get("status") or "starting"),
        "action_id": payload.get("action_id"),
        "url": payload.get("source_url") or source_url,
        "host": payload.get("host"),
        "screen_path": payload.get("screen_path") or "",
        "browser": payload.get("browser"),
        "log_path": payload.get("log_path"),
        "expires_in": int(timeout_seconds),
        "next": (
            f"扫码或在打开的页面里登录，窗口不用关："
            f"envoy batch login {payload.get('action_id')} --wait 会继续这次批量读取"
        ),
    }


def login_batch(
    batch_id: str,
    *,
    timeout_seconds: int = 300,
    visible: bool = False,
    wait: bool = False,
    index: int | None = None,
    reader: Callable[[ReadRequest], Any] | None = None,
) -> dict:
    """Open the sign-in page for the first item that needs it.

    With ``wait=True`` this blocks until the browser reports a usable session
    (or the timeout expires) and then resumes the batch, retrying the items a
    login wall had stopped. Without it, the caller scans the picture and runs
    the resume command when ready.
    """

    state = load_batch(batch_id)
    target = None
    for item in state.get("items") or []:
        if item.get("status") not in LOGIN_STATES:
            continue
        if index is not None and int(item.get("index") or 0) != int(index):
            continue
        target = item
        break
    login = state.get("login") or {}
    url = str((target or {}).get("url") or login.get("url") or "")
    if not url:
        raise BatchError(f"Batch {batch_id} has no item waiting for a sign-in")

    handoff = open_login_handoff(
        url, timeout_seconds=timeout_seconds, visible=visible
    )
    result = {"batch_id": state["batch_id"], "url": url, **handoff}
    action_id = str(handoff.get("action_id") or "")
    if not wait or not action_id:
        return result
    deadline = time.monotonic() + max(10, int(timeout_seconds))
    signed_in = False
    while time.monotonic() < deadline:
        try:
            current = auth_session.status(action_id)
        except Exception:
            break
        status = str(current.get("status") or "")
        if status == "signed_in":
            signed_in = True
            break
        if status in {"failed", "canceled"}:
            break
        time.sleep(1.0)
    result["signed_in"] = signed_in
    if signed_in:
        authflow.update_auth_action(action_id, status="verifying")
        result["resumed"] = run_batch(
            state["batch_id"], retry_login=True, reader=reader
        )
        result["next"] = result["resumed"].get("next")
    else:
        result["next"] = f"登录未完成；重新执行 envoy batch login {state['batch_id']} --wait"
    return result


def render_summary(summary: dict) -> str:
    lines = [
        f"批次 {summary.get('batch_id')} · {summary.get('status')}"
        + (f"（{summary['reason']}）" if summary.get("reason") else ""),
        f"完成 {summary.get('completed')}/{summary.get('total')}"
        f" · 本次读取 {summary.get('read_now')} · 跳过 {summary.get('skipped')}",
    ]
    if summary.get("login"):
        login = summary["login"]
        lines.append(f"待登录 {login.get('host')}：先执行 {summary.get('next')}")
    if summary.get("failed"):
        lines.append(f"失败 {len(summary['failed'])} 条，可用 --retry-failed 重试")
    lines.append(f"状态文件 {summary.get('state_file')}")
    lines.append(f"续跑 {summary.get('next')}")
    return "\n".join(lines)


def needs_login(response) -> bool:
    """True when a read response stopped at a sign-in wall instead of failing."""

    if response is None:
        return False
    result = str(getattr(response.result, "value", response.result))
    if result not in {"blocked", "partial", "needs_host"}:
        return False
    for error in getattr(response, "errors", None) or []:
        if str(error.model_dump(mode="json").get("code") or "") in AUTH_CODES:
            return True
    payload = getattr(response, "needs_user_action", None)
    return bool(payload)


def wait_for_sign_in(action_id: str, timeout_seconds: int = 300) -> str:
    """Poll the background sign-in browser; return its final status."""

    deadline = time.monotonic() + max(5, int(timeout_seconds))
    status = "starting"
    while time.monotonic() < deadline:
        try:
            current = auth_session.status(action_id)
        except Exception:
            return "unavailable"
        status = str(current.get("status") or "starting")
        if status in {"signed_in", "failed", "canceled"}:
            return status
        time.sleep(1.0)
    return status


def login_first(
    request: ReadRequest,
    *,
    reader: Callable[[ReadRequest], Any] | None = None,
    timeout_seconds: int = 300,
    visible: bool = False,
) -> dict:
    """Read a link; if it hits a sign-in wall, open the site's sign-in page first.

    The point is that a login wall is an ordinary step, not a failure: Envoy
    opens the page in its own profile, hands back the picture a person needs
    (the QR code or the phone-number form), waits for the sign-in, then reads
    the same request again with the new session.
    """

    read = reader or read_public_page
    response = read(request)
    url = str(getattr(request.source, "url", "") or "")
    if not needs_login(response) or not url:
        return {"response": response, "handoff": None, "signed_in": False, "retried": False}
    handoff = open_login_handoff(url, timeout_seconds=timeout_seconds, visible=visible)
    action_id = str(handoff.get("action_id") or "")
    signed_in = False
    if action_id and timeout_seconds > 0:
        signed_in = wait_for_sign_in(action_id, timeout_seconds) == "signed_in"
    if signed_in:
        try:
            authflow.update_auth_action(action_id, status="verifying")
        except Exception:
            pass
        response = read(request)
    return {
        "response": response,
        "handoff": handoff,
        "signed_in": signed_in,
        "retried": signed_in,
    }
