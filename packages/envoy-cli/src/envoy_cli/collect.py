"""Compose one profile link into a resumable Markdown source pack.

`envoy read` answers "what is on this page". `envoy collect` answers "give me
everything this creator published that matters, in one file, and let me stop
and resume". It is deliberately deterministic: it enumerates the profile,
reads each selected post once, records durable evidence, and writes a Markdown
skeleton whose sections are anchored to material it actually stored. It never
invents a summary, a timestamp, or a quote.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse
import threading
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from envoy_core.schemas.read_v1 import Budget, ReadRequest

from . import collections as checkpoint
from .reader import read_public_page

EXCERPT_LIMIT = 4000
_TEXT_BLOCK_FIELDS = ("text", "value", "transcript", "content", "caption", "summary")
_UNFINISHED_STATES = {"blocked", "failed", "partial", "skipped"}


def _record_work_usage(
    manifest: dict,
    collection_id: str,
    candidate: str,
    seconds: float,
) -> dict:
    """Charge consumed work against the run budget under a stable operation id.

    The id is derived from the run and the item, so replaying the same read
    cannot double-charge the manifest, and the stage a given second belonged to
    stays readable in ``applied_operations`` after a resume.
    """
    if seconds <= 0:
        return manifest
    stage = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    run_id = str(manifest["run_id"])
    usage = {"work_seconds": max(int(round(seconds)), 1)}
    try:
        return checkpoint.update_collection(
            collection_id,
            run_id=run_id,
            expected_revision=int(manifest["manifest_revision"]),
            operation_id=f"collect-work-{run_id}-{stage}",
            action="record_usage",
            usage=usage,
        )
    except checkpoint.CollectionError:
        pass
    # A long item outlives the lease heartbeat, which bumps the manifest
    # revision while the read runs. Retry once against the latest revision so
    # consumed work is not silently dropped; if the write still fails the run
    # continues on the manifest it already had.
    try:
        fresh = checkpoint.get_collection(collection_id, run_id)
        return checkpoint.update_collection(
            collection_id,
            run_id=run_id,
            expected_revision=int(fresh["manifest_revision"]),
            operation_id=f"collect-work-{run_id}-{stage}",
            action="record_usage",
            usage=usage,
        )
    except checkpoint.CollectionError:
        return manifest


def _sources_path(out: Path) -> Path:
    return out.with_name(out.name + ".sources.json")


def _load_sources(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_sources(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_markdown(out: Path, text: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)


def _excerpt_from_blocks(blocks: list) -> str:
    parts: list[str] = []
    for data in blocks:
        if not isinstance(data, dict):
            continue
        for field in _TEXT_BLOCK_FIELDS:
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, dict):
                # A real block keeps its payload as {"type": "text", "text": ...};
                # reading only top-level strings left the excerpt empty.
                inner = value.get("text") or value.get("value")
                if isinstance(inner, str) and inner.strip():
                    parts.append(inner.strip())
    text = "\n\n".join(dict.fromkeys(parts))
    if len(text) > EXCERPT_LIMIT:
        return text[:EXCERPT_LIMIT] + "\n\n（摘录已截断，用 material_id 展开）"
    return text


def _excerpt(response) -> str:
    return _excerpt_from_blocks(
        [block.model_dump(mode="json") for block in response.blocks]
    )


def _excerpt_from_material(material_id: str) -> str:
    """Rebuild an excerpt from the local material store, without the network."""
    if not material_id:
        return ""
    try:
        from .reader import _material_root

        path = _material_root() / f"{material_id}.json"
        if path.is_symlink() or not path.is_file():
            return ""
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(stored, dict):
        return ""
    response = stored.get("operation_response") or stored.get("response")
    blocks = response.get("blocks") if isinstance(response, dict) else None
    if not isinstance(blocks, list):
        blocks = stored.get("blocks")
    return _excerpt_from_blocks(blocks if isinstance(blocks, list) else [])


def _reason(response) -> str:
    if not response.errors:
        return ""
    data = response.errors[0].model_dump(mode="json")
    message = str(data.get("message") or "").strip()
    code = str(data.get("code") or "").strip()
    return f"{code}: {message}" if message else code


def _host_of(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


def _should_retry(
    state: str,
    reason: str,
    retry_blocked: bool,
    retry_reason: str | None,
) -> bool:
    if state not in _UNFINISHED_STATES:
        return True
    wanted = str(retry_reason or "").strip().lower()
    if wanted:
        return wanted in str(reason or "").lower()
    return retry_blocked


def _auth_pause_of(response, candidate: str, source_url: str) -> dict | None:
    """One site needs a person once, not once per post."""

    if not getattr(response, "errors", None):
        return None
    error = response.errors[0].model_dump(mode="json")
    code = str(error.get("code") or "")
    if code not in {"auth_required", "challenge_required"}:
        return None
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    action = details.get("needs_user_action")
    action = action if isinstance(action, dict) else {}
    return {
        "host": _host_of(source_url),
        "reason": code,
        "action_id": action.get("action_id"),
        "resume_token": action.get("resume_token"),
        "open_command": action.get("open_command"),
        "candidates": [candidate],
    }


_LEASE_INTERVAL_SECONDS = 60.0


class _LeaseHeartbeat:
    """Keep the lease warm while a long read is still running."""

    def __init__(self, collection_id: str, run_id: str, candidate: str, interval: float | None = None):
        self._args = (collection_id, run_id, candidate)
        self._interval = max(0.01, float(_LEASE_INTERVAL_SECONDS if interval is None else interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        def beat():
            while not self._stop.wait(self._interval):
                try:
                    checkpoint.renew_lease(*self._args)
                except Exception:
                    return

        self._thread = threading.Thread(target=beat, name="envoy-lease", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def _open_collection(
    collection_id: str,
    run_id: str,
    profile_url: str,
    *,
    budget=None,
) -> tuple[dict, bool]:
    """Return the existing run when there is one, else a fresh one."""
    try:
        return checkpoint.get_collection(collection_id, run_id), True
    except checkpoint.CollectionError:
        pass
    return (
        checkpoint.create_collection(
            collection_id,
            run_id,
            profile_url,
            operation_id="collect-create",
            budget=budget,
        ),
        False,
    )


def _select_candidates(
    manifest: dict,
    profile_url: str,
    *,
    limit: int,
    goal: str,
    max_work_seconds: int,
) -> dict:
    page = checkpoint.discover_profile(
        profile_url,
        limit=limit,
        max_work_seconds=max_work_seconds,
    )
    manifest = checkpoint.update_collection(
        manifest["collection_id"],
        run_id=manifest["run_id"],
        expected_revision=int(manifest["manifest_revision"]),
        operation_id=f"collect-discover-{page['discovery_revision']}",
        action="discovery_snapshot",
        usage={"snapshot": page},
    )
    ids = [
        str(item["candidate_id"])
        for item in page.get("candidates") or []
        if isinstance(item, dict) and item.get("candidate_id")
    ]
    if not ids:
        raise checkpoint.CollectionError("The profile exposed no candidates to collect")
    manifest = checkpoint.update_collection(
        manifest["collection_id"],
        run_id=manifest["run_id"],
        expected_revision=int(manifest["manifest_revision"]),
        operation_id="collect-select-" + hashlib.sha256(
            ("\0".join(ids)).encode("utf-8")
        ).hexdigest()[:16],
        action="select",
        candidate_ids=ids,
    )
    return manifest


def _set_state(manifest: dict, candidate: str, state: str) -> dict:
    return checkpoint.update_collection(
        manifest["collection_id"],
        run_id=manifest["run_id"],
        expected_revision=int(manifest["manifest_revision"]),
        operation_id=f"collect-state-{candidate}-{state}-{int(manifest['manifest_revision'])}",
        action="item_state",
        candidate_id_value=candidate,
        item_state=state,
    )


def _record_evidence(manifest: dict, candidate: str, response) -> dict:
    return checkpoint.update_collection(
        manifest["collection_id"],
        run_id=manifest["run_id"],
        expected_revision=int(manifest["manifest_revision"]),
        operation_id=f"collect-evidence-{candidate}-{int(manifest['manifest_revision'])}",
        action="record_evidence",
        candidate_id_value=candidate,
        material_id=response.material_id,
        source_revision=response.source_revision,
        expires_at=response.expires_at.timestamp() if response.expires_at else None,
        evidence_available=bool(response.material_id),
    )


def _render(
    manifest: dict,
    sources: dict,
    *,
    platform: str,
    profile_url: str,
) -> str:
    candidates = {
        str(item.get("candidate_id")): item
        for item in manifest.get("candidates") or []
        if isinstance(item, dict)
    }
    ordered = [
        candidate
        for candidate in (str(value) for value in manifest.get("selected_candidate_ids") or [])
        if candidate in candidates
    ]
    ordered.sort(key=lambda candidate: int(candidates[candidate].get("position") or 0))
    done = [c for c in ordered if manifest["items"].get(c, {}).get("state") == "completed"]
    pending = [c for c in ordered if c not in done]
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# {platform} 主页素材：`{profile_url}`",
        "",
        f"> Envoy collect · 生成于 {generated} · 已完成 {len(done)}/{len(ordered)} 条",
        f"> 断点续跑 ID：`{manifest['collection_id']}` / `{manifest['run_id']}`",
        "",
        "## 使用说明",
        "",
        "本文件是**素材稿**，不是成品。每条记录只包含 Envoy 实际读取到的内容："
        "标题、来源链接、material_id 与原文摘录。改写为干货时，只能使用下面已经"
        "读到的摘录，或先用 material_id 继续展开；不要补写来源里没有的结论、"
        "时间点或引文。未完成部分列在文末，不计入结论。",
        "",
        "## 目录",
        "",
    ]
    if not ordered:
        lines.append("- （尚未选择任何候选）")
    for index, candidate in enumerate(ordered, start=1):
        record = sources.get(candidate) or {}
        title = str(record.get("title") or candidates[candidate].get("title") or candidate)
        lines.append(f"{index}. {title}")
    lines.append("")
    for index, candidate in enumerate(ordered, start=1):
        item = candidates[candidate]
        record = sources.get(candidate) or {}
        state = manifest["items"].get(candidate, {}).get("state", "selected")
        title = str(record.get("title") or item.get("title") or item.get("source_url"))
        lines.extend([
            f"## {index}. {title}",
            "",
            f"- 来源：{item.get('source_url')}",
        ])
        meta = [
            f"作者 {item['creator']}" if item.get("creator") else "",
            f"时长 {item['duration_seconds']}s" if item.get("duration_seconds") else "",
            f"发布 {item['published_at']}" if item.get("published_at") else "",
            f"状态 {state}",
        ]
        lines.append("- " + " · ".join(value for value in meta if value))
        if record.get("material_id"):
            lines.append(
                f"- Envoy material：`{record['material_id']}`（可用 `envoy_expand` 续读）"
            )
        if record.get("reason"):
            lines.append(f"- 未完成原因：{record['reason']}")
        lines.extend(["", "### 原文摘录", ""])
        excerpt = str(record.get("excerpt") or "").strip()
        if excerpt:
            lines.extend(
                f"> {line}" if line.strip() else ">"
                for line in excerpt.splitlines()
            )
        else:
            lines.append("> （本条未取得可用文本；不要凭标题推断内容）")
        lines.append("")
    lines.extend(["## 未完成", ""])
    if not pending:
        lines.append("无。")
    else:
        lines.append("| 候选 | 状态 | 原因 | 来源 |")
        lines.append("| --- | --- | --- | --- |")
        for candidate in pending:
            item = candidates[candidate]
            record = sources.get(candidate) or {}
            state = manifest["items"].get(candidate, {}).get("state", "selected")
            reason = str(record.get("reason") or "尚未处理").replace("|", "／")
            lines.append(
                f"| {str(record.get('title') or item.get('title') or candidate)} "
                f"| {state} | {reason} | {item.get('source_url')} |"
            )
    lines.extend([
        "",
        "## 待补（人工或模型）",
        "",
        "- [ ] 把每条摘录收敛成 1–3 条可复用结论，并保留来源链接",
        "- [ ] 标注相互矛盾或相互印证的条目",
        "- [ ] 补齐未完成条目，或明确声明未覆盖",
        "",
    ])
    return "\n".join(lines)


def _repair_artifacts(
    out: Path,
    sources_path: Path,
    manifest: dict,
    sources: dict,
    candidates: dict,
    selected: list[str],
    *,
    platform: str,
    profile_url: str,
) -> dict:
    """Recreate lost sidecars from the material store instead of re-reading."""
    repaired: list[str] = []
    filled = 0
    for candidate in selected:
        item = candidates.get(candidate) or {}
        record = sources.get(candidate) or {}
        state = str(manifest["items"].get(candidate, {}).get("state") or "selected")
        evidence = (manifest.get("evidence") or {}).get(candidate) or {}
        material_id = str(record.get("material_id") or evidence.get("material_id") or "")
        if not material_id:
            continue
        if not record:
            record = {"title": str(item.get("title") or ""), "url": item.get("source_url")}
        record["material_id"] = material_id
        if not str(record.get("excerpt") or "").strip():
            excerpt = _excerpt_from_material(material_id)
            if excerpt:
                record["excerpt"] = excerpt
                filled += 1
        record.setdefault("state", state)
        if not record.get("state"):
            record["state"] = state
        sources[candidate] = record
    if filled or sources:
        _save_sources(sources_path, sources)
    markdown_missing = not out.is_file() or out.stat().st_size == 0
    if markdown_missing and (sources or selected):
        _write_markdown(
            out, _render(manifest, sources, platform=platform, profile_url=profile_url)
        )
        repaired.append("markdown")
    if filled:
        repaired.append("excerpts")
    return {"rebuilt": repaired, "excerpts_filled": filled}


def collect(
    profile_url: str,
    out_path: str | Path,
    *,
    limit: int = 10,
    goal: str = "",
    collection_id: str | None = None,
    run_id: str | None = None,
    max_work_seconds: int = 120,
    retry_blocked: bool = False,
    retry_reason: str | None = None,
    budget=None,
) -> dict:
    """Enumerate a profile, read the selected posts, and write the pack."""
    canonical, platform = checkpoint.canonical_profile_url(profile_url)
    out = Path(out_path).expanduser()
    if not str(out).strip():
        raise checkpoint.CollectionError("An output path is required")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    collection_id = collection_id or f"collect-{digest}"
    run_id = run_id or "run-1"
    manifest, resumed = _open_collection(
        collection_id,
        run_id,
        canonical,
        budget=budget,
    )
    if manifest.get("profile_url") != canonical:
        raise checkpoint.CollectionError(
            "This collection run belongs to a different profile"
        )
    work_started = time.monotonic()

    def remaining_work() -> float:
        """Seconds this run may still spend, discovery included."""
        return float(max_work_seconds) - (time.monotonic() - work_started)

    sources_path = _sources_path(out)
    sources = _load_sources(sources_path)
    if not manifest.get("selected_candidate_ids"):
        discovery_started = time.monotonic()
        manifest = _select_candidates(
            manifest,
            canonical,
            limit=limit,
            goal=goal,
            max_work_seconds=max(int(remaining_work()), 1),
        )
        manifest = _record_work_usage(
            manifest, collection_id, "discovery", time.monotonic() - discovery_started
        )
    candidates = {
        str(item.get("candidate_id")): item
        for item in manifest.get("candidates") or []
        if isinstance(item, dict)
    }
    selected = [str(value) for value in manifest.get("selected_candidate_ids") or []]
    rebuilt = _repair_artifacts(
        out,
        sources_path,
        manifest,
        sources,
        candidates,
        selected,
        platform=platform,
        profile_url=canonical,
    )
    read_now = 0
    skipped = 0
    auth_pause: dict | None = None
    for candidate in selected:
        item = candidates.get(candidate) or {}
        record = sources.get(candidate) or {}
        state = str(manifest["items"].get(candidate, {}).get("state") or "selected")
        if state == "completed" and (record.get("excerpt") or record.get("material_id")):
            skipped += 1
            continue
        if state in _UNFINISHED_STATES and not _should_retry(
            state, str(record.get("reason") or ""), retry_blocked, retry_reason
        ):
            skipped += 1
            continue
        if (
            auth_pause
            and _host_of(item.get("source_url")) == auth_pause["host"]
            and str(retry_reason or "").lower() not in {"auth", "challenge", "auth_required", "challenge_required"}
        ):
            manifest = _set_state(manifest, candidate, "blocked")
            sources[candidate] = {
                **record,
                "title": record.get("title") or item.get("title") or "",
                "url": item.get("source_url"),
                "state": "blocked",
                "reason": f"auth_pause: waiting for {auth_pause.get('action_id') or auth_pause['host']}",
            }
            auth_pause["candidates"].append(candidate)
            skipped += 1
            continue
        available = remaining_work()
        if available < 1:
            # Out of run budget: the untouched items stay selected so the next
            # run resumes here instead of reading them with a fresh allowance.
            break
        manifest = _set_state(manifest, candidate, "processing")
        item_started = time.monotonic()
        try:
            with _LeaseHeartbeat(collection_id, run_id, candidate):
                response = read_public_page(
                    ReadRequest(
                        request_id=f"collect-{collection_id}-{candidate[:24]}",
                        idempotency_key=(
                            f"collect:{collection_id}:{run_id}:{candidate}"
                        )[:256],
                        source={"url": str(item.get("source_url") or "")},
                        goal=goal,
                        # Never hand a child read the full allowance: it gets
                        # what this run actually has left.
                        budget=Budget(max_work_seconds=max(int(available), 1)),
                    )
                )
        except Exception as exc:  # a single item must not sink the run
            manifest = _set_state(manifest, candidate, "failed")
            sources[candidate] = {
                **record,
                "title": record.get("title") or item.get("title") or "",
                "state": "failed",
                "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
            }
            _save_sources(sources_path, sources)
            _write_markdown(out, _render(manifest, sources, platform=platform, profile_url=canonical))
            continue
        finally:
            manifest = _record_work_usage(
                manifest, collection_id, candidate, time.monotonic() - item_started
            )
        result = str(getattr(response.result, "value", response.result))
        if response.material_id:
            manifest = _record_evidence(manifest, candidate, response)
        if result == "ready":
            final_state = "completed"
        elif result == "blocked":
            final_state = "blocked"
            pause = _auth_pause_of(response, candidate, str(item.get("source_url") or ""))
            if pause:
                auth_pause = pause
        elif result in {"partial", "needs_host"}:
            final_state = "partial"
        else:
            final_state = "skipped"
        manifest = _set_state(manifest, candidate, final_state)
        sources[candidate] = {
            "title": record.get("title") or item.get("title") or "",
            "url": item.get("source_url"),
            "material_id": response.material_id,
            "state": final_state,
            "result": result,
            "reason": _reason(response),
            "excerpt": _excerpt(response),
        }
        read_now += 1
        _save_sources(sources_path, sources)
        _write_markdown(out, _render(manifest, sources, platform=platform, profile_url=canonical))
    _save_sources(sources_path, sources)
    markdown = _render(manifest, sources, platform=platform, profile_url=canonical)
    _write_markdown(out, markdown)
    finished = sum(
        1
        for candidate in selected
        if manifest["items"].get(candidate, {}).get("state") == "completed"
    )
    pending = [
        {
            "candidate_id": candidate,
            "source_url": (candidates.get(candidate) or {}).get("source_url"),
            "state": manifest["items"].get(candidate, {}).get("state", "selected"),
            "reason": (sources.get(candidate) or {}).get("reason", ""),
        }
        for candidate in selected
        if manifest["items"].get(candidate, {}).get("state") != "completed"
    ]
    return {
        "ok": True,
        "output": str(out),
        "rebuilt": rebuilt.get("rebuilt") or [],
        "excerpts_filled": rebuilt.get("excerpts_filled") or 0,
        "sources": str(sources_path),
        "platform": platform,
        "profile_url": canonical,
        "collection_id": collection_id,
        "run_id": run_id,
        "resumed": resumed,
        "selected": len(selected),
        "completed": finished,
        "read_now": read_now,
        "skipped": skipped,
        "pending": pending,
        "auth_pause": auth_pause,
        "manifest_revision": manifest["manifest_revision"],
    }
