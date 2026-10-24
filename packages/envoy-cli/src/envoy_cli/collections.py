from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .media import interprocess_file_lock
from .netpolicy import proxy_plan


class CollectionError(ValueError):
    pass


class CollectionRevisionConflict(CollectionError):
    pass


_PROFILE_WORKS_ERROR_MARKERS = ("服务异常，重新刷新拉取数据",)


def _operation_fingerprint(**payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _DouyinCandidateParser(HTMLParser):
    _profile_markers = ("usernewui", "user-profile-main", "user-detail-main")
    _void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.rows: list[dict] = []
        self._anchor_depth = 0
        self._current: dict | None = None
        self._profile_depth = 0
        self._excluded_depth = 0
        self._stack: list[tuple[bool, bool]] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_map = dict(attrs)
        markers = " ".join(
            str(attrs_map.get(name) or "")
            for name in ("class", "id", "data-e2e", "data-testid")
        ).lower()
        excluded = tag.lower() not in self._void_tags and any(
            marker in markers for marker in (
                "recommend", "related", "avatar", "comment", "search", "hot-list",
            )
        )
        profile = any(marker in markers for marker in self._profile_markers)
        if tag.lower() not in self._void_tags:
            self._stack.append((profile, excluded))
        if profile:
            self._profile_depth += 1
        if excluded:
            self._excluded_depth += 1
        if tag.lower() == "a" and self._excluded_depth == 0 and self._profile_depth > 0:
            href = str(attrs_map.get("href") or "").strip()
            if re.search(r"/(?:video|note)/[A-Za-z0-9_-]+", href):
                self._anchor_depth += 1
                self._current = {"url": urljoin(self.source_url, href), "title": ""}

    def handle_data(self, data: str):
        if self._current is not None and self._anchor_depth:
            text = " ".join(str(data).split())
            if text:
                self._current["title"] = " ".join(
                    item for item in (self._current["title"], text) if item
                )[:1000]

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._current is not None and self._anchor_depth:
            self.rows.append(self._current)
            self._current = None
            self._anchor_depth -= 1
        if self._stack and tag.lower() not in self._void_tags:
            was_profile, was_excluded = self._stack.pop()
            if was_profile:
                self._profile_depth = max(0, self._profile_depth - 1)
            if was_excluded:
                self._excluded_depth = max(0, self._excluded_depth - 1)


@dataclass(frozen=True)
class CollectionBudget:
    max_pages: int = 20
    max_scanned_candidates: int = 100
    max_items: int = 100
    max_download_bytes: int = 1_073_741_824
    max_response_chars: int = 1_000_000
    max_visual_inputs: int = 100
    max_visual_pixels: int = 100_000_000
    max_retries: int = 100
    max_work_seconds: int = 86_400

    def can_add(self, current: dict, **usage: int) -> bool:
        limits = {
            "scanned_pages": self.max_pages,
            "scanned_candidates": self.max_scanned_candidates,
            "selected_items": self.max_items,
            "downloaded_bytes": self.max_download_bytes,
            "returned_chars": self.max_response_chars,
            "visual_inputs": self.max_visual_inputs,
            "visual_pixels": self.max_visual_pixels,
            "retries": self.max_retries,
            "work_seconds": self.max_work_seconds,
        }
        return all(
            int(current.get(field, 0)) + int(amount) <= limits[field]
            for field, amount in usage.items()
            if field in limits
        )


def _collection_root() -> Path:
    configured = os.getenv("ENVOY_LOCAL_DATA_DIR", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".envoy" / "materials"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _safe_identity(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise CollectionError(f"Invalid {name}")
    return value


def _manifest_path(collection_id: str, run_id: str) -> Path:
    identity = f"{_safe_identity(collection_id, 'collection_id')}\0{_safe_identity(run_id, 'run_id')}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return _collection_root() / f"collection-{digest}.json"


def _locked(path: Path):
    return interprocess_file_lock(
        path.with_suffix(".lock"),
        max_wait_seconds=30,
        busy_message="Collection checkpoint is busy; retry the operation",
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
    fd, temporary = tempfile.mkstemp(prefix=".collection-", suffix=".tmp", dir=path.parent)
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


def canonical_candidate_url(value: str) -> str:
    parts = urlsplit(str(value or "").strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"}:
        raise CollectionError("Only public post URLs can be canonicalized")
    path = parts.path.rstrip("/")
    if host in {"douyin.com", "www.douyin.com", "v.douyin.com"}:
        match = re.fullmatch(r"/(video|note)/([A-Za-z0-9_-]+)", path)
        if not match:
            raise CollectionError("URL does not identify a canonical Douyin video or note")
        kind, post_id = match.groups()
        return f"https://www.douyin.com/{kind}/{post_id}"
    if host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av\d+)", path)
        if not match:
            raise CollectionError("URL does not identify a canonical Bilibili video")
        return f"https://www.bilibili.com/video/{match.group(1)}"
    raise CollectionError("Only public Douyin or Bilibili post URLs can be canonicalized")


def candidate_id(source_url: str) -> str:
    canonical = canonical_candidate_url(source_url)
    prefix = "bili-" if "bilibili.com" in canonical else "douyin-"
    return prefix + hashlib.sha256(canonical.encode()).hexdigest()[:24]


def canonical_profile_url(value: str) -> tuple[str, str]:
    """Return the canonical profile URL and the platform that serves it."""
    parts = urlsplit(str(value or "").strip())
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    if (
        parts.scheme not in {"http", "https"}
        or parts.username
        or parts.password
    ):
        raise CollectionError("A public profile URL is required")
    if host in {"douyin.com", "www.douyin.com"}:
        if not re.fullmatch(r"/user/[^/]+", path):
            raise CollectionError("A public Douyin user profile URL is required")
        return f"https://www.douyin.com{path}", "douyin"
    if host == "space.bilibili.com":
        match = re.fullmatch(r"/(\d{1,20})(?:/video)?", path)
        if not match:
            raise CollectionError("A public Bilibili space URL is required")
        return f"https://space.bilibili.com/{match.group(1)}/video", "bilibili"
    raise CollectionError("A public Douyin or Bilibili profile URL is required")


_BILIBILI_LINK = re.compile(
    r'<a[^>]+href="[^"]*?/video/(BV[A-Za-z0-9]{10})[^"]*"[^>]*>(.*?)</a>',
    re.S,
)
_HTML_TAG = re.compile(r"<[^>]+>")


def _bilibili_candidates(html: str, *, limit: int = 100) -> list[dict]:
    """Read the video links a rendered Bilibili space actually shows."""
    rows: list[dict] = []
    seen: set[str] = set()
    for video_id, inner in _BILIBILI_LINK.findall(str(html or "")):
        if video_id in seen:
            continue
        seen.add(video_id)
        title = " ".join(_HTML_TAG.sub(" ", inner).split())[:200]
        rows.append({
            "url": f"https://www.bilibili.com/video/{video_id}",
            "title": title,
        })
    
    return _candidate_records(rows, limit=limit)


def _candidate_records(entries: list[dict], *, offset: int = 0, limit: int = 100) -> list[dict]:
    candidates = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url") or entry.get("webpage_url") or "")
        item_id = str(entry.get("id") or "")
        if raw_url.startswith(("http://", "https://")):
            candidate_url = raw_url
        elif item_id and re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
            entry_type = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
            if "bilibili" in entry_type:
                candidate_url = f"https://www.bilibili.com/video/{item_id}"
            else:
                kind = "note" if "note" in entry_type else "video"
                candidate_url = f"https://www.douyin.com/{kind}/{item_id}"
        else:
            continue
        try:
            canonical = canonical_candidate_url(candidate_url)
        except CollectionError:
            continue
        stable_id = candidate_id(canonical)
        if stable_id in seen:
            continue
        seen.add(stable_id)
        path_kind = urlsplit(canonical).path.split("/")[1]
        candidates.append({
            "candidate_id": stable_id,
            "source_url": canonical,
            "platform": "bilibili" if "bilibili.com" in canonical else "douyin",
            "item_type": "image_note" if path_kind == "note" else "video",
            "title": str(entry.get("title") or ""),
            "published_at": entry.get("upload_date"),
            "duration_seconds": entry.get("duration"),
            "creator": str(entry.get("uploader") or entry.get("channel") or ""),
            "position": offset + len(candidates),
        })
        if len(candidates) >= limit:
            break
    return candidates


def discover_profile(
    profile_url: str,
    *,
    limit: int = 20,
    cursor: str | None = None,
    max_work_seconds: int = 60,
) -> dict:
    from .media import MediaUnsafeAddress, guard_yt_dlp_client, public_network_guard
    from .video import _load_yt_dlp

    if not 1 <= int(limit) <= 100:
        raise CollectionError("limit must be between 1 and 100")
    canonical_profile, platform = canonical_profile_url(profile_url)
    revision = ""
    offset = 0
    if cursor:
        match = re.fullmatch(r"([a-f0-9]{20}):(\d{1,3})", cursor)
        if not match:
            raise CollectionError("Invalid discovery cursor")
        revision = match.group(1)
        offset = int(match.group(2))
        snapshot_path = _collection_root() / f"discovery-{revision}.json"
        snapshot = _read(snapshot_path)
        if (
            snapshot is None
            or snapshot.get("profile_url") != canonical_profile
            or float(snapshot.get("expires_at") or 0) <= time.time()
        ):
            raise CollectionError("Discovery snapshot expired; start discovery again")
        all_candidates = list(snapshot.get("candidates") or [])
        page = all_candidates[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(all_candidates)
        return {
            "profile_url": canonical_profile,
            "platform": snapshot.get("platform", "douyin"),
            "discovery_revision": revision,
            "cursor_mode": "persisted_snapshot",
            "ordering": snapshot.get("ordering", "source"),
            "candidates": page,
            "has_more": True if has_more else snapshot.get("has_more"),
            "next_cursor": f"{revision}:{next_offset}" if has_more else None,
            "stop_reason": "page_limit" if has_more else snapshot.get("stop_reason", "source_end_unknown"),
            "discovered_at": snapshot.get("discovered_at"),
        }
    youtube_dl, _extractors = _load_yt_dlp()
    if youtube_dl is None:
        raise CollectionError("yt-dlp is not installed")
    extractor_error: Exception | None = None
    options = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 100,
        "noplaylist": False,
        "socket_timeout": min(max(int(max_work_seconds), 1), 10),
        "retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
        "playlist_items": "1-100",
    }
    info = None
    for proxy in proxy_plan():
        options["proxy"] = proxy
        try:
            with public_network_guard():
                with youtube_dl(options) as ydl:
                    guard_yt_dlp_client(ydl)
                    info = ydl.extract_info(canonical_profile, download=False)
        except MediaUnsafeAddress as exc:
            raise CollectionError(
                "Profile discovery resolved a non-public network address"
            ) from exc
        except Exception as exc:
            extractor_error = exc
            info = None
            continue
        if isinstance(info, dict):
            break
    entries = info.get("entries") if isinstance(info, dict) else None
    cursor_mode = "yt_dlp_flat_metadata"
    if isinstance(entries, (list, tuple)):
        candidates = _candidate_records(list(entries), limit=100)
    elif platform == "bilibili":
        from . import browser
        from .reader import _is_public_http_url

        try:
            page = browser.read_page(
                canonical_profile,
                max_work_seconds=max(int(max_work_seconds), 1),
            )
        except Exception as exc:
            detail = (
                f"yt-dlp space enumeration unavailable ({type(extractor_error).__name__}: "
                f"{str(extractor_error)[:160]})"
                if extractor_error
                else "yt-dlp did not expose a Bilibili space list"
            )
            raise CollectionError(
                f"{detail}; isolated-browser discovery failed "
                f"({type(exc).__name__}): {str(exc)[:240]}"
            ) from exc
        if not _is_public_http_url(page.url, {}):
            raise CollectionError("The isolated browser resolved the space to a non-public address")
        candidates = _bilibili_candidates(str(getattr(page, "html", "") or ""), limit=100)
        cursor_mode = "isolated_browser_bounded_snapshot"
        if not candidates:
            raise CollectionError(
                "No public profile candidates were discovered in the Bilibili space"
            )
    else:
        from . import browser
        from .reader import _is_public_http_url

        try:
            page = browser.read_page(
                canonical_profile,
                max_work_seconds=max(int(max_work_seconds), 1),
            )
        except Exception as exc:
            detail = (
                f"yt-dlp profile enumeration unavailable ({type(extractor_error).__name__})"
                if extractor_error
                else "yt-dlp did not expose a profile candidate list"
            )
            raise CollectionError(
                f"{detail}; isolated-browser discovery failed ({type(exc).__name__}): {str(exc)[:240]}"
            ) from exc
        if not _is_public_http_url(page.url, {}):
            raise CollectionError("The isolated browser resolved the profile to a non-public address")
        page_text = str(getattr(page, "text", "") or "")
        if any(marker in page_text for marker in _PROFILE_WORKS_ERROR_MARKERS):
            raise CollectionError(
                "Douyin profile works list reported a service error; no candidate list is trusted"
            )
        parser = _DouyinCandidateParser(page.url)
        parser.feed(page.html)
        parser.close()
        candidates = _candidate_records(parser.rows, limit=100)
        cursor_mode = "isolated_browser_bounded_snapshot"
        if not candidates:
            detail = (
                f"yt-dlp could not enumerate this profile ({type(extractor_error).__name__})"
                if extractor_error
                else "the isolated browser exposed no canonical video/note links"
            )
            raise CollectionError(f"No public profile candidates were discovered: {detail}")
    revision = uuid.uuid4().hex[:20]
    discovered_at = datetime.now(timezone.utc).isoformat()
    snapshot_has_more = None
    snapshot = {
        "profile_url": canonical_profile,
        "platform": platform,
        "discovery_revision": revision,
        "cursor_mode": cursor_mode,
        "ordering": "source",
        "candidates": candidates,
        "has_more": snapshot_has_more,
        "stop_reason": "hard_candidate_limit" if len(candidates) >= 100 else "source_end_unknown",
        "discovered_at": discovered_at,
        "expires_at": time.time() + 7 * 24 * 60 * 60,
    }
    snapshot_path = _collection_root() / f"discovery-{revision}.json"
    with _locked(snapshot_path):
        _write(snapshot_path, snapshot)
    page = candidates[:limit]
    next_offset = offset + len(page)
    has_more = len(candidates) > limit
    return {
        "profile_url": canonical_profile,
        "platform": platform,
        "discovery_revision": revision,
        "cursor_mode": cursor_mode,
        "ordering": "source",
        "candidates": page,
        "has_more": True if has_more else snapshot_has_more,
        "next_cursor": f"{revision}:{next_offset}" if has_more else None,
        "stop_reason": (
            "page_limit" if has_more
            else snapshot["stop_reason"]
        ),
        "discovered_at": discovered_at,
    }


def create_collection(
    collection_id: str,
    run_id: str,
    profile_url: str,
    *,
    operation_id: str,
    budget: CollectionBudget | None = None,
) -> dict:
    collection_id = _safe_identity(collection_id, "collection_id")
    run_id = _safe_identity(run_id, "run_id")
    operation_id = _safe_identity(operation_id, "operation_id")
    profile, platform = canonical_profile_url(profile_url)
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        existing = _read(path)
        if existing is not None:
            if operation_id in existing["applied_operations"]:
                return existing
            raise CollectionError("Collection run already exists; choose a new run_id")
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "collection_id": collection_id,
            "run_id": run_id,
            "profile_url": profile,
            "platform": platform,
            "discovery_revision": None,
            "selection_plan_revision": 0,
            "manifest_revision": 1,
            "budget": (budget or CollectionBudget()).__dict__,
            "cursor": None,
            "candidates": [],
            "selected_candidate_ids": [],
            "items": {},
            "usage": {
                "scanned_pages": 0,
                "scanned_candidates": 0,
                "selected_items": 0,
                "work_seconds": 0,
                "downloaded_bytes": 0,
                "returned_chars": 0,
                "visual_inputs": 0,
                "visual_pixels": 0,
                "retries": 0,
            },
            "evidence": {},
            "applied_operations": {operation_id: {"revision": 1}},
            "created_at": now,
            "updated_at": now,
        }
        _write(path, manifest)
        return manifest


def get_collection(collection_id: str, run_id: str) -> dict:
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        manifest = _read(path)
        if manifest is None:
            raise CollectionError("Collection run was not found")
        now = time.time()
        changed = False
        for candidate, item in manifest.get("items", {}).items():
            lease_expiry = item.get("lease_expires_at")
            if (
                item.get("state") == "processing"
                and lease_expiry is not None
                and float(lease_expiry) <= now
            ):
                item["state"] = "selected"
                item["retryable"] = True
                item["lease_expires_at"] = None
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                operation_key = f"lease-reap:{candidate}:{item.get('attempts', 0)}"
                manifest.setdefault("applied_operations", {})[operation_key] = {
                    "action": "lease_reap",
                    "candidate_id": candidate,
                }
                changed = True
        for evidence in manifest.get("evidence", {}).values():
            if (
                evidence.get("availability") == "available"
                and evidence.get("expires_at") is not None
                and float(evidence["expires_at"]) <= now
            ):
                evidence["availability"] = "expired"
                changed = True
        if changed:
            manifest["manifest_revision"] += 1
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write(path, manifest)
        return manifest


def update_collection(
    collection_id: str,
    *,
    run_id: str,
    expected_revision: int,
    operation_id: str,
    action: str,
    candidate_ids: list[str] | None = None,
    candidate_id_value: str | None = None,
    item_state: str | None = None,
    material_id: str | None = None,
    source_revision: str | None = None,
    expires_at: float | None = None,
    evidence_available: bool | None = None,
    pending_resource_ids: list[str] | None = None,
    observed_resource_ids: list[str] | None = None,
    usage: dict[str, int] | None = None,
    budget: CollectionBudget | None = None,
) -> dict:
    allowed_actions = {
        "select", "item_state", "record_evidence", "record_usage", "set_cursor",
        "discovery_snapshot", "record_handoff", "record_observation",
    }
    if action not in allowed_actions:
        raise CollectionError("Unsupported collection checkpoint action")
    operation_id = _safe_identity(operation_id, "operation_id")
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        manifest = _read(path)
        if manifest is None:
            raise CollectionError("Collection run was not found")
        applied = manifest["applied_operations"].get(operation_id)
        fingerprint = _operation_fingerprint(
            action=action,
            candidate_ids=candidate_ids,
            candidate_id_value=candidate_id_value,
            item_state=item_state,
            material_id=material_id,
            source_revision=source_revision,
            expires_at=expires_at,
            evidence_available=evidence_available,
            pending_resource_ids=pending_resource_ids,
            observed_resource_ids=observed_resource_ids,
            usage=usage,
            budget=(budget.__dict__ if budget is not None else None),
        )
        if applied is not None:
            if applied.get("fingerprint") != fingerprint:
                raise CollectionError("operation_id was already used for different checkpoint input")
            return manifest
        if int(manifest["manifest_revision"]) != int(expected_revision):
            raise CollectionRevisionConflict("manifest_revision changed; reload before updating")
        if action == "select":
            ids = list(dict.fromkeys(str(item) for item in (candidate_ids or [])))
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            if len(ids) > min(stored_budget.max_items, 100):
                raise CollectionError(
                    f"Selection exceeds the collection max_items budget ({stored_budget.max_items})"
                )
            known = {str(item.get("candidate_id")) for item in manifest["candidates"]}
            if any(item not in known for item in ids):
                raise CollectionError("Selection contains an unknown candidate_id")
            previous_candidates = set(manifest.get("selected_candidate_ids", []))
            manifest["selected_candidate_ids"] = ids
            manifest["selection_plan_revision"] += 1
            newly_selected = len(set(ids) - previous_candidates)
            if not stored_budget.can_add(
                manifest["usage"],
                selected_items=newly_selected,
            ):
                raise CollectionError("Collection selected-item budget exhausted")
            manifest["usage"]["selected_items"] += newly_selected
        elif action == "discovery_snapshot":
            snapshot = usage or {}
            discovered = snapshot.get("snapshot")
            if not isinstance(discovered, dict):
                raise CollectionError("A discovery snapshot payload is required")
            revision = str(discovered.get("discovery_revision") or "")
            if not revision:
                raise CollectionError("A discovery_revision is required")
            if (
                manifest.get("discovery_revision")
                and manifest["discovery_revision"] != revision
            ):
                manifest["candidates"] = []
                manifest["selected_candidate_ids"] = []
                manifest["items"] = {}
                manifest["evidence"] = {}
                manifest["selection_plan_revision"] += 1
            known = {
                str(item.get("candidate_id"))
                for item in manifest["candidates"]
                if isinstance(item, dict)
            }
            new_candidates = [
                item for item in (discovered.get("candidates") or [])
                if isinstance(item, dict)
                and str(item.get("candidate_id")) not in known
            ]
            if len(manifest["candidates"]) + len(new_candidates) > 100:
                raise CollectionError("Collection snapshot cannot exceed 100 candidates")
            manifest["discovery_revision"] = revision
            manifest["cursor"] = discovered.get("next_cursor")
            manifest["candidates"].extend(new_candidates)
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            increments = {
                "scanned_pages": 1,
                "scanned_candidates": len(new_candidates),
            }
            if not stored_budget.can_add(manifest["usage"], **increments):
                raise CollectionError("Collection discovery budget exhausted")
            manifest["usage"]["scanned_pages"] += increments["scanned_pages"]
            manifest["usage"]["scanned_candidates"] += increments["scanned_candidates"]
        elif action == "item_state":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("Only selected candidates can change item state")
            state = str(item_state or "")
            if state not in {"selected", "processing", "completed", "partial", "blocked", "skipped", "failed"}:
                raise CollectionError("Invalid collection item state")
            previous = manifest["items"].get(candidate, {})
            attempts = int(previous.get("attempts", 0)) + (1 if state == "processing" else 0)
            if state == "processing" and attempts > 1:
                stored_budget = CollectionBudget(**manifest.get("budget", {}))
                if not stored_budget.can_add(manifest["usage"], retries=1):
                    raise CollectionError("Collection retry budget exhausted")
                manifest["usage"]["retries"] += 1
            manifest["items"][candidate] = {
                **previous,
                "state": state,
                "attempts": attempts,
                "retryable": False,
                "lease_expires_at": time.time() + 300 if state == "processing" else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_handoff":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("A handoff must belong to a selected candidate")
            previous = manifest["items"].get(candidate, {})
            resources = list(dict.fromkeys(
                str(item) for item in (pending_resource_ids or [])
            ))
            already_observed = list(dict.fromkeys(
                str(item) for item in (previous.get("observed_resource_ids") or [])
            ))
            manifest["items"][candidate] = {
                **previous,
                "state": "partial",
                "pending_resource_ids": [
                    item for item in resources if item not in already_observed
                ],
                "observed_resource_ids": already_observed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_observation":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("An observation must belong to a selected candidate")
            item = manifest["items"].get(candidate)
            if not item:
                raise CollectionError("The candidate has no checkpoint state")
            pending = set(item.get("pending_resource_ids") or [])
            previously_observed = set(item.get("observed_resource_ids") or [])
            incoming = set(str(value) for value in (observed_resource_ids or []))
            if not incoming or not incoming <= pending:
                raise CollectionError("Observations must reference pending resource IDs")
            item["observed_resource_ids"] = sorted(previously_observed | incoming)
            item["pending_resource_ids"] = sorted(pending - incoming)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
        elif action == "record_evidence":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("Evidence must belong to a selected candidate")
            manifest["evidence"][candidate] = {
                "material_id": material_id,
                "source_revision": source_revision,
                "expires_at": expires_at,
                "availability": (
                    "available" if evidence_available is not False
                    and (expires_at is None or expires_at > time.time())
                    else "expired"
                ),
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_usage":
            increments = usage or {}
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            limits = budget or stored_budget
            if not limits.can_add(manifest["usage"], **increments):
                raise CollectionError("Collection budget exhausted; resume from the saved cursor")
            for field, amount in increments.items():
                if field in manifest["usage"]:
                    manifest["usage"][field] += int(amount)
        elif action == "set_cursor":
            manifest["cursor"] = {
                "discovery_revision": manifest.get("discovery_revision"),
                "value": str(candidate_id_value) if candidate_id_value is not None else None,
            }
        manifest["manifest_revision"] += 1
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["applied_operations"][operation_id] = {
            "revision": manifest["manifest_revision"],
            "action": action,
            "fingerprint": fingerprint,
        }
        _write(path, manifest)
        return manifest
