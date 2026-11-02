"""GitHub source reader: repository trees, files with line ranges, commits, PRs
and issues, all through the public REST API.

Why this module exists
----------------------
A GitHub link is structured data, not a rendered article. Booting a browser to
scrape github.com costs a process and loses the thing the caller actually wants:
the tree, the file bytes, the line range, the diff. The REST API returns exactly
those fields, so one or two requests are enough for the common cases.

Design rules
------------
* stdlib only (``urllib`` + ``json``); no Chromium, no OCR/ASR, no repo clone.
* Navigation is one level at a time: a directory listing costs one request and
  names each child directory by a URL that can be read directly. The whole-tree
  response is opt-in (goal keywords or an explicit flag) because GitHub spends
  78 s and 1.5 MB building it for a large repository (cpython, measured), and a
  subdirectory read that waits on it is slower than opening the page by hand.
* Every request goes through ``_github_json`` / ``_github_raw`` so tests can
  monkeypatch them and run fully offline.
* A URL this module does not understand returns ``None`` and the caller keeps
  the existing generic read path (login walls, site pages, wiki, gist raw, ...).
* Citations must be verifiable: every read resolves the ref to a commit SHA and
  reports the path, line range and file-bytes SHA-256 it actually returned.
* Honesty over completeness: a truncated tree, an un-fetched file body or an
  un-fetched PR review says so in ``coverage.unresolved``; it never claims a
  complete read it did not perform.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ErrorCode,
    ExecutionState,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadError,
    ReadRequest,
    ReadResponse,
    ReadResult,
    TextContent,
    WebLocator,
)

__all__ = ["GithubLink", "parse_github_url", "read_github_source"]

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
USER_AGENT = "Envoy/0.1 (+https://github.com/regrevia/Envoy)"

#: Reader bound: an unbounded tree response is both slow to build and useless to
#: read; the caller gets a capped, navigable listing plus an honest flag.
#: A one-level listing is one directory, so the cap only bites on pathological
#: folders; the contents API itself stops at 1000 children.
MAX_ONE_LEVEL_ENTRIES = 1000
#: A recursive tree is opt-in (see ``_listing_plan``) and bounded: GitHub builds
#: the whole subtree server-side (cpython: 78 s / 1.5 MB), so the caller never
#: waits unbounded. On timeout the read degrades to one level.
MAX_RECURSIVE_ENTRIES = 1000
RECURSIVE_TREE_BUDGET_SECONDS = 20.0
#: Escape hatch for the one case goal keywords cannot express: a caller that
#: knows it wants the whole tree. The read request schema has no tree-scope
#: field, so the flag is the explicit signal.
RECURSIVE_ENV_FLAG = "ENVOY_GITHUB_RECURSIVE_TREE"
#: absolute child-directory URLs are also surfaced in ``details``; bound that map
#: so the JSON does not grow past the listing itself.
MAX_SUBDIRECTORY_URLS = 200
#: Files above this are not pulled whole: the caller gets the prefix it asked
#: for, a lower-bound line count and no content hash.
MAX_FILE_BYTES = 8 * 1024 * 1024
#: A patch is context, not the diff record; truncate it per file.
MAX_PATCH_CHARS = 1200
MAX_LOCATOR_QUOTE_CHARS = 200
MAX_BODY_CHARS = 6000
DEFAULT_TIMEOUT = 15.0
MAX_COMMENT_SUMMARIES = 20

#: github.com top-level paths that are site pages, not repositories. Matching one
#: of these must fall back to the generic reader instead of firing API calls at
#: a repository that does not exist.
RESERVED_OWNERS = {
    "about", "account", "apps", "blog", "codespaces", "collections", "contact",
    "customer-stories", "dashboard", "discussions", "edu", "enterprise", "events",
    "explore", "features", "gist", "home", "issues", "join", "login", "logout",
    "marketplace", "mobile", "new", "notifications", "organizations", "orgs",
    "pricing", "pulls", "readme", "search", "security", "sessions", "settings",
    "site", "sponsors", "stars", "team", "topics", "trending", "users", "watching",
}

_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_LINE_RANGE_RE = re.compile(r"^L(\d+)(?:C\d+)?(?:-L?(\d+)(?:C\d+)?)?$")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GithubError(Exception):
    """An API call failed in a way the caller must report instead of guessing."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        url: str = "",
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.payload = payload or {}
        self.headers = headers or {}


class GithubNotFound(GithubError):
    """404: missing repository, file, commit, PR or issue (private looks the same)."""


class GithubUnauthorized(GithubError):
    """401: the configured token is not accepted."""


class GithubRateLimited(GithubError):
    """403/429 rate limit. Carries the reset instant so the caller can wait."""

    def __init__(self, message: str, *, reset_at: str | None = None, reset_epoch: int | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.reset_epoch = reset_epoch
        self.reset_at = reset_at


class GithubUnavailable(GithubError):
    """The request never reached an HTTP response (DNS, TLS, timeout, proxy)."""


# ---------------------------------------------------------------------------
# HTTP layer (the only two functions tests monkeypatch)
# ---------------------------------------------------------------------------


def _http_headers(token: str | None, accept: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_error(status: int, url: str, body: bytes, headers: dict[str, str]) -> GithubError:
    payload: dict = {}
    try:
        decoded = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(decoded, dict):
            payload = decoded
    except (ValueError, TypeError):
        payload = {}
    message = str(payload.get("message") or f"GitHub returned HTTP {status}")
    lower = message.lower()
    reset_header = str(headers.get("x-ratelimit-reset") or "")
    remaining = str(headers.get("x-ratelimit-remaining") or "")
    reset_epoch: int | None = None
    reset_at: str | None = None
    try:
        reset_epoch = int(reset_header) if reset_header else None
    except ValueError:
        reset_epoch = None
    if reset_epoch is not None:
        reset_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset_epoch))
    besides = {"status": status, "url": url, "payload": payload, "headers": headers}
    if status in {403, 429} and (
        remaining == "0" or "rate limit" in lower or "secondary rate" in lower
    ):
        return GithubRateLimited(
            f"GitHub API rate limit reached ({message})",
            reset_at=reset_at,
            reset_epoch=reset_epoch,
            **besides,
        )
    if status == 404:
        return GithubNotFound(message, **besides)
    if status == 401:
        return GithubUnauthorized(message, **besides)
    return GithubError(message, **besides)


def _http_get(
    url: str,
    *,
    token: str | None,
    timeout: float,
    max_bytes: int,
    accept: str,
) -> tuple[bytes, bool]:
    request = Request(url, headers=_http_headers(token, accept))
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            return data[:max_bytes], len(data) > max_bytes
    except HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 - a body read must not mask the status
            body = b""
        raise _http_error(exc.code, url, body, dict(exc.headers or {})) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise GithubUnavailable(f"GitHub request failed ({type(exc).__name__})", url=url) from None


def _github_json(path: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET a GitHub API JSON document. ``path`` is ``/repos/...`` or a full URL.

    The payload is normally an object, but a few endpoints (comment lists) return
    a bare array and are legitimate documents.
    """

    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    data, _truncated = _http_get(
        url,
        token=token,
        timeout=timeout,
        max_bytes=4 * 1024 * 1024,
        accept="application/vnd.github+json",
    )
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        raise GithubError("GitHub returned a non-JSON response", url=url) from None
    return payload


def _github_raw(
    url: str,
    *,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_FILE_BYTES,
) -> tuple[bytes, bool]:
    """Download raw bytes. Returns ``(data, truncated)``; never claims completeness."""

    return _http_get(
        url,
        token=token,
        timeout=timeout,
        max_bytes=max_bytes,
        accept="text/plain",
    )


def _token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GithubLink:
    kind: str  # repo | tree | blob | commit | pull | issue | raw | gist
    url: str
    owner: str = ""
    repo: str = ""
    ref: str | None = None
    path: str = ""
    number: int | None = None
    sha: str = ""
    gist_id: str = ""
    line_start: int | None = None
    line_end: int | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}" if self.repo else self.owner


def _line_range_from_fragment(fragment: str) -> tuple[int | None, int | None]:
    match = _LINE_RANGE_RE.match(fragment.strip())
    if not match:
        return None, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    if start <= 0 or end <= 0:
        return None, None
    return min(start, end), max(start, end)


def parse_github_url(url: str) -> GithubLink | None:
    """Map a github.com URL to a link descriptor, or ``None`` to fall back."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path or "")
    segments = [segment for segment in path.split("/") if segment]
    fragment_start, fragment_end = _line_range_from_fragment(parsed.fragment or "")

    if host == "raw.githubusercontent.com":
        if len(segments) < 4:
            return None
        owner, repo, ref = segments[0], segments[1], segments[2]
        file_path = "/".join(segments[3:])
        return GithubLink(
            kind="raw",
            url=url,
            owner=owner,
            repo=repo,
            ref=ref,
            path=file_path,
            sha=ref.lower() if _SHA_RE.match(ref) else "",
            line_start=fragment_start,
            line_end=fragment_end,
        )

    if host == "gist.github.com":
        if "raw" in [segment.lower() for segment in segments]:
            return None  # gist raw files are plain text: the generic path is fine
        if not segments or len(segments) > 2:
            return None
        gist_id = segments[-1]
        if not re.fullmatch(r"[0-9A-Za-z]+", gist_id):
            return None
        return GithubLink(kind="gist", url=url, owner=segments[0] if len(segments) == 2 else "", gist_id=gist_id)

    if host not in {"github.com", "www.github.com"}:
        return None
    if len(segments) < 2:
        return None
    owner, repo = segments[0], segments[1]
    if owner.lower() in RESERVED_OWNERS or not repo:
        return None
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        return None
    rest = segments[2:]
    if not rest:
        return GithubLink(kind="repo", url=url, owner=owner, repo=repo)

    action = rest[0].lower()
    if action == "tree":
        if len(rest) < 2:
            return None
        return GithubLink(
            kind="tree",
            url=url,
            owner=owner,
            repo=repo,
            ref=rest[1],
            path="/".join(rest[2:]),
            line_start=fragment_start,
            line_end=fragment_end,
        )
    if action == "blob":
        if len(rest) < 2:
            return None
        ref = rest[1]
        return GithubLink(
            kind="blob",
            url=url,
            owner=owner,
            repo=repo,
            ref=ref,
            path="/".join(rest[2:]),
            sha=ref.lower() if _SHA_RE.match(ref) else "",
            line_start=fragment_start,
            line_end=fragment_end,
        )
    if action == "commit":
        if len(rest) < 2:
            return None
        return GithubLink(kind="commit", url=url, owner=owner, repo=repo, sha=rest[1])
    if action in {"pull", "pulls"} and len(rest) >= 2 and rest[1].isdigit():
        return GithubLink(kind="pull", url=url, owner=owner, repo=repo, number=int(rest[1]))
    if action == "issues" and len(rest) >= 2 and rest[1].isdigit():
        return GithubLink(kind="issue", url=url, owner=owner, repo=repo, number=int(rest[1]))
    return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    token: str | None
    timeout: float
    requests: list[str] = field(default_factory=list)
    authenticated: bool = False


def _resolve_sha(ctx: _Context, link: GithubLink, ref: str) -> str:
    """Resolve a branch/tag/ref to the commit SHA a citation can be bound to."""

    if _SHA_RE.match(ref):
        return ref.lower()
    data = _github_json(
        f"/repos/{link.full_name}/commits/{quote(ref, safe='/')}",
        token=ctx.token,
        timeout=ctx.timeout,
    )
    ctx.requests.append(f"commits/{ref}")
    sha = str(data.get("sha") or "")
    if not _SHA_RE.match(sha):
        raise GithubError(f"GitHub did not return a commit SHA for ref {ref!r}")
    return sha.lower()


def _repo_meta(ctx: _Context, link: GithubLink) -> dict:
    data = _github_json(f"/repos/{link.full_name}", token=ctx.token, timeout=ctx.timeout)
    ctx.requests.append(f"repos/{link.full_name}")
    return data


def _contents(ctx: _Context, link: GithubLink, ref: str, path: str) -> Any:
    suffix = f"?ref={quote(ref, safe='')}" if ref else ""
    data = _github_json(
        f"/repos/{link.full_name}/contents/{quote(path, safe='/')}{suffix}",
        token=ctx.token,
        timeout=ctx.timeout,
    )
    ctx.requests.append(f"contents/{path}@{ref[:7] or 'default'}")
    return data


_RECURSIVE_GOAL_RE = re.compile(
    r"""
      \brecursiv(?:e|ely)\b
    | \ball\s+(?:the\s+)?files\b
    | \bevery\s+file\b
    | \b(?:whole|entire)\s+(?:repo|repository|tree)\b
    | \bfull\s+(?:tree|repository)\b
    | \bcode\s*search\b
    | 递归
    | 全部文件
    | 所有文件
    | 整个(?:仓库|仓库树|代码库|树)
    | 全树
    | 代码搜索
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _listing_plan(request: ReadRequest) -> tuple[str, str | None]:
    """Decide one-level (default) vs recursive (explicit) and say why.

    One directory costs one request and a few kilobytes; the recursive tree of a
    large repository costs GitHub 78 s and 1.5 MB to build (cpython, measured),
    which is slower than a model opening the page itself and is what made
    subdirectory navigation feel broken. So recursion only happens when the
    caller asks for the whole tree: a goal that names it, or the explicit
    environment flag (the read schema has no tree-scope field to carry it).
    """

    if _env_flag(RECURSIVE_ENV_FLAG):
        return "recursive", f"explicit_env_flag: {RECURSIVE_ENV_FLAG}=1"
    match = _RECURSIVE_GOAL_RE.search(request.goal or "")
    if match is not None:
        return "recursive", f"goal_keyword: {match.group(0).strip()!r}"
    return "one_level", None


def _git_tree(ctx: _Context, link: GithubLink, sha: str, *, recursive: bool) -> tuple[list[dict], bool]:
    suffix = "?recursive=1" if recursive else ""
    data = _github_json(
        f"/repos/{link.full_name}/git/trees/{sha}{suffix}",
        token=ctx.token,
        timeout=ctx.timeout,
    )
    ctx.requests.append(f"git/trees/{sha[:7]}{'?recursive=1' if recursive else ''}")
    entries = data.get("tree")
    if not isinstance(entries, list):
        entries = []
    return entries, bool(data.get("truncated"))


def _tree_one_level(ctx: _Context, link: GithubLink, sha: str, path: str) -> tuple[list[dict], dict]:
    """List exactly one directory level: one request, no whole-repo payload.

    The two endpoints that can answer one level were measured on
    ``python/cpython/tree/main/Lib`` (191 entries): ``git/trees/<sha>`` needs the
    root tree first to find the subdirectory's tree SHA (2 requests, 49 KB, 5.0 s
    on a cold cache) while ``contents/<path>?ref=<sha>`` answers in 1 request
    (171 KB, 6.9 s). Fewer round trips wins for navigation, and the cost no
    longer grows with path depth, so a subdirectory uses the contents API and a
    repository root uses the git tree (8 KB, one request, keeps the ``truncated``
    flag the contents API does not expose).
    """

    details: dict = {}
    if not path:
        entries, truncated = _git_tree(ctx, link, sha, recursive=False)
        details["listing_source"] = "git_trees"
        details["tree_truncated"] = truncated
        details["repo_entries_total"] = None if truncated else len(entries)
        details["repo_entries_lower_bound"] = len(entries) if truncated else None
        return entries, details

    listing = _contents(ctx, link, sha, path)
    if isinstance(listing, dict):
        # A tree URL that points at a file: report that one file honestly rather
        # than an empty "directory".
        name = str(listing.get("name") or path.rsplit("/", 1)[-1])
        entries = [{
            "path": f"{path.rstrip('/')}/{name}" if listing.get("type") == "file" else path,
            "type": "tree" if listing.get("type") in {"dir", "tree"} else "blob",
            "size": listing.get("size"),
            "sha": listing.get("sha"),
        }]
        details["listing_source"] = "contents_api"
        details["listing_is_file"] = True
        return entries, details

    entries, _ = _normalize_contents_listing(listing, path)
    details["listing_source"] = "contents_api"
    details["tree_truncated"] = False
    if len(entries) >= MAX_ONE_LEVEL_ENTRIES:
        # The contents API stops at 1000 children without a truncated flag, so
        # say so instead of claiming a complete directory.
        details["listing_capped_by_api"] = True
        details["entries_total_lower_bound"] = len(entries)
    return entries, details


def _tree_recursive(
    ctx: _Context,
    link: GithubLink,
    sha: str,
    path: str,
    *,
    started: float,
) -> tuple[list[dict], dict, str | None]:
    """Whole-tree listing, bounded in wall clock and entries.

    Returns ``(entries, details, degrade_reason)``; a non-empty reason means the
    caller must fall back to one level. The request timeout is clipped to the
    recursive budget so a huge repository fails fast instead of hanging for the
    measured 78 s.
    """

    budget = RECURSIVE_TREE_BUDGET_SECONDS
    saved_timeout = ctx.timeout
    ctx.timeout = max(1.0, min(saved_timeout, budget))
    began = time.monotonic()
    try:
        entries, truncated = _git_tree(ctx, link, sha, recursive=True)
    except GithubUnavailable as exc:
        ctx.timeout = saved_timeout
        kind = "timeout" if "timeout" in str(exc).lower() else "unavailable"
        return [], {}, kind
    finally:
        ctx.timeout = saved_timeout

    elapsed = time.monotonic() - began
    details: dict = {
        "listing_source": "git_trees?recursive=1",
        "tree_truncated": truncated,
        "recursive_tree_elapsed_ms": int(elapsed * 1000),
        "recursive_tree_budget_seconds": budget,
        "repo_entries_total": None if truncated else len(entries),
        "repo_entries_lower_bound": len(entries) if truncated else None,
    }
    if path:
        prefix = path.rstrip("/") + "/"
        entries = [
            entry
            for entry in entries
            if str(entry.get("path") or "") == path.rstrip("/")
            or str(entry.get("path") or "").startswith(prefix)
        ]
    if elapsed > budget:
        # The response did arrive, so it is used, but the overrun is recorded:
        # the next call with the same URL should have asked for one level.
        details["recursive_tree_over_budget"] = True
    return entries, details, None


def _normalize_contents_listing(data: Any, path: str) -> tuple[list[dict], bool]:
    if not isinstance(data, list):
        return [], True
    entries: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        entries.append({
            "path": f"{path.rstrip('/')}/{name}" if path else name,
            "type": "tree" if str(item.get("type")) in {"dir", "tree"} else "blob",
            "size": item.get("size"),
            "sha": item.get("sha"),
        })
    return entries, False


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (truncated)"


def _split_lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines()


# ---------------------------------------------------------------------------
# Response building
# ---------------------------------------------------------------------------


class _Builder:
    """Accumulates blocks under the response character budget."""

    def __init__(self, request: ReadRequest, source_url: str) -> None:
        self.source_url = source_url
        self.blocks: list[MaterialBlock] = []
        self.truncated = False
        self._order = 0
        hard_cap = min(int(request.budget.max_response_chars or 12_000), 20_000)
        self._remaining = max(600, hard_cap - 500)

    def add(self, kind: MaterialKind, text: str, *, language: str | None = None) -> MaterialBlock | None:
        if not text:
            return None
        text = text.strip("\n")
        if not text:
            return None
        if self._remaining <= 0:
            self.truncated = True
            return None
        clipped = text
        if len(clipped) > self._remaining:
            clipped = clipped[: self._remaining]
            self.truncated = True
        self._remaining -= len(clipped)
        block_id = "blk-title" if kind is MaterialKind.HEADING and not self.blocks else f"blk-{kind.value}-{self._order:04d}"
        self._order += 1
        block = MaterialBlock(
            id=block_id,
            kind=kind,
            content=TextContent(type="text", text=clipped, language=language),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=self.source_url,
                block_id=block_id,
                quote=clipped[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=len(self.blocks),
        )
        self.blocks.append(block)
        return block


@dataclass
class _Readout:
    blocks: list[MaterialBlock]
    details: dict
    unresolved: list[str] = field(default_factory=list)
    partial: bool = False
    revision_basis: str = ""
    modality: str = "code"
    counts: dict = field(default_factory=dict)

    def add_unresolved(self, message: str) -> None:
        if message not in self.unresolved:
            self.unresolved.append(message)


@dataclass
class _Reading:
    """Everything shared by the per-kind handlers."""

    request: ReadRequest
    link: GithubLink
    ctx: _Context
    builder: _Builder
    details: dict
    unresolved: list[str] = field(default_factory=list)
    partial: bool = False
    revision_basis: str = ""
    counts: dict = field(default_factory=dict)

    def note(self, message: str) -> None:
        if message not in self.unresolved:
            self.unresolved.append(message)

    def is_partial(self) -> bool:
        return self.partial or self.builder.truncated


def _base_details(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> dict:
    return {
        "reader": "github",
        "github_kind": link.kind,
        "url": link.url,
        "owner": link.owner,
        "repo": link.repo,
        "authenticated": ctx.authenticated,
        "api_requests": list(ctx.requests),
        "request_count": len(ctx.requests),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _response(
    request: ReadRequest,
    link: GithubLink,
    ctx: _Context,
    started: float,
    *,
    blocks: list[MaterialBlock],
    details: dict,
    unresolved: list[str],
    partial: bool,
    revision_basis: str,
    modality: str,
    counts: dict,
) -> ReadResponse:
    payload = _base_details(request, link, ctx, started)
    payload.update(details)
    extraction = "partial" if (partial or unresolved) else "complete"
    item = CoverageItem(
        discovered=counts.get("discovered"),
        extracted=counts.get("extracted"),
        returned=counts.get("returned"),
        extraction=extraction,
        unresolved=list(unresolved),
        details=payload,
    )
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        source_revision=hashlib.sha256(revision_basis.encode("utf-8")).hexdigest(),
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if partial else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={modality: item}, unresolved=list(unresolved)),
    )


def _blocked(
    request: ReadRequest,
    code: ErrorCode,
    message: str,
    *,
    action_required: bool = False,
    retryable: bool = False,
    details: dict | None = None,
) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[
            ReadError(
                code=code,
                message=message,
                retryable=retryable,
                action_required=action_required,
                details=dict(details or {}),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Repository root / tree
# ---------------------------------------------------------------------------


def _subdirectory_url(repo_url: str, ref: str, path: str) -> str:
    """The absolute URL a caller can read again to list this directory."""

    if not repo_url or not ref or not path:
        return ""
    return f"{repo_url}/tree/{ref}/{path}"


def _entry_line(entry: dict, *, repo_url: str = "", ref: str = "") -> str:
    raw_path = str(entry.get("path") or "")
    if entry.get("type") == "tree":
        url = _subdirectory_url(repo_url, ref, raw_path)
        # The URL is the child-count hint: reading it returns this directory's
        # own entries, and it is one request no matter how deep the path is.
        return f"- [dir] {raw_path}/ — {url}" if url else f"- [dir] {raw_path}/"
    size = entry.get("size")
    size_text = f" ({int(size)} B)" if isinstance(size, int) else ""
    return f"- {raw_path}{size_text}"


def _read_repo_or_tree(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    builder = _Builder(request, link.url)
    details: dict = {}
    unresolved: list[str] = []
    counts: dict = {}

    metadata: dict | None = None
    if link.kind == "repo":
        metadata = _repo_meta(ctx, link)
        default_branch = str(metadata.get("default_branch") or "")
        details.update({
            "default_branch": default_branch or None,
            "description": metadata.get("description"),
            "language": metadata.get("language"),
            "license": (metadata.get("license") or {}).get("spdx_id") if isinstance(metadata.get("license"), dict) else None,
            "size_kb": metadata.get("size"),
            "stars": metadata.get("stargazers_count"),
            "forks": metadata.get("forks_count"),
            "open_issues": metadata.get("open_issues_count"),
            "pushed_at": metadata.get("pushed_at"),
        })
        ref = link.ref or default_branch or "HEAD"
    else:
        ref = link.ref or "HEAD"

    sha = _resolve_sha(ctx, link, ref)
    details["ref"] = ref
    details["commit_sha"] = sha
    if link.path:
        details["path"] = link.path

    plan, plan_reason = _listing_plan(request)
    details["plan"] = plan
    if plan_reason:
        details["plan_reason"] = plan_reason

    if plan == "recursive":
        entries, tree_details, degrade_reason = _tree_recursive(
            ctx, link, sha, link.path, started=started
        )
        if degrade_reason:
            # Never make the caller sit through the whole-tree wait: the level
            # that was asked about is still answerable in one request.
            note_key = (
                "recursive_tree_timeout"
                if degrade_reason == "timeout"
                else "recursive_tree_unavailable"
            )
            details["plan"] = "one_level"
            details["plan_downgraded_from"] = "recursive"
            details["plan_downgrade_reason"] = note_key
            entries, tree_details = _tree_one_level(ctx, link, sha, link.path)
            unresolved.append(
                f"{note_key}: the whole-tree request was abandoned "
                f"(budget {RECURSIVE_TREE_BUDGET_SECONDS:g} s); this listing covers one "
                f"level only — read a listed [dir] URL to go deeper"
            )
        elif "entries_total" not in tree_details:
            truncated = bool(tree_details.get("tree_truncated"))
            tree_details["entries_total"] = None if truncated else len(entries)
            tree_details["entries_total_lower_bound"] = len(entries) if truncated else None
    else:
        entries, tree_details = _tree_one_level(ctx, link, sha, link.path)
    details.update(tree_details)

    if "entries_total" not in details:
        # One-level listing: entries_total is the direct children of this level,
        # not the size of the repository.
        uncertain = bool(details.get("tree_truncated") or details.get("listing_capped_by_api"))
        details["entries_total"] = None if uncertain else len(entries)
        details["entries_total_lower_bound"] = len(entries) if uncertain else None

    limit = MAX_RECURSIVE_ENTRIES if details.get("plan") == "recursive" else MAX_ONE_LEVEL_ENTRIES
    ordered = sorted(entries, key=lambda entry: (entry.get("type") != "tree", str(entry.get("path") or "").lower()))
    shown = ordered[:limit]
    if len(ordered) > len(shown):
        details["entries_capped_by_reader"] = True
        details["entries_capped_reason"] = (
            f"{len(ordered)} entries in this scope, first {limit} shown"
        )
        unresolved.append(
            f"Listing was capped at {limit} entries by the reader; {len(ordered)} entries "
            f"exist in this scope. Read a listed [dir] URL to see the rest"
        )
        counts = {"discovered": len(ordered), "extracted": len(shown), "returned": len(shown)}
    else:
        counts = {"discovered": len(ordered), "extracted": len(ordered), "returned": len(ordered)}
    details["entries_shown"] = len(shown)
    details["dirs_shown"] = sum(1 for entry in shown if entry.get("type") == "tree")
    details["files_shown"] = len(shown) - details["dirs_shown"]
    if details.get("listing_capped_by_api"):
        unresolved.append(
            f"GitHub's contents API returns at most {MAX_ONE_LEVEL_ENTRIES} children for a "
            f"directory, so this listing may be incomplete"
        )
    if details.get("tree_truncated"):
        unresolved.append(
            "GitHub truncated the recursive tree response, so directory entries may be missing; "
            "read a specific subdirectory or file to get its complete contents"
        )
        details["entries_total"] = None
        details["entries_total_lower_bound"] = len(entries)

    if metadata is not None:
        heading = f"{link.full_name}"
        meta_lines = [
            f"Repository: {link.full_name}",
            f"Default branch: {details.get('default_branch') or 'unknown'}",
            f"Commit: {sha}",
        ]
        if details.get("description"):
            meta_lines.append(f"Description: {details['description']}")
        if details.get("language"):
            meta_lines.append(f"Language: {details['language']}")
        if details.get("license"):
            meta_lines.append(f"License: {details['license']}")
        if details.get("size_kb") is not None:
            meta_lines.append(f"Size: {details['size_kb']} KB")
        builder.add(MaterialKind.HEADING, heading)
        builder.add(MaterialKind.PARAGRAPH, "\n".join(meta_lines))
    else:
        builder.add(MaterialKind.HEADING, f"{link.full_name} · {link.path or '.'}")
        builder.add(
            MaterialKind.PARAGRAPH,
            f"Commit: {sha}\nRef: {ref}\nPath: {link.path or '.'}",
        )

    # ``plan`` and ``mode`` are the same value under two names: ``plan`` is what
    # the caller asked for, ``mode`` is what the payload actually contains (they
    # differ only for a timed-out recursive request, already recorded above).
    details["mode"] = details.get("plan")
    repo_url = f"https://github.com/{link.full_name}"
    listing = "\n".join(
        _entry_line(entry, repo_url=repo_url, ref=ref) for entry in shown
    ) or "(empty directory)"
    builder.add(MaterialKind.LIST, listing)

    dir_urls: dict[str, str] = {}
    for entry in shown:
        if entry.get("type") != "tree":
            continue
        url = _subdirectory_url(repo_url, ref, str(entry.get("path") or ""))
        if url:
            dir_urls[str(entry.get("path"))] = url
    if dir_urls:
        details["subdirectory_urls"] = dict(list(dir_urls.items())[:MAX_SUBDIRECTORY_URLS])
        if len(dir_urls) > MAX_SUBDIRECTORY_URLS:
            details["subdirectory_urls_capped_by_reader"] = True
    if dir_urls and details.get("plan") != "recursive":
        # A navigation hint, not an unresolved gap: nothing is missing, the
        # caller simply has not asked for the next level yet.
        details["listing_hint"] = (
            f"{len(dir_urls)} subdirectories are named but not expanded; "
            f"read a [dir] URL to list one (one request, does not grow with depth)"
        )
    if details.get("listing_hint"):
        builder.add(MaterialKind.PARAGRAPH, str(details["listing_hint"]))
    if unresolved:
        builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))

    revision_basis = f"github\0tree\0{link.full_name}\0{sha}\0{link.path or ''}"
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=(
            builder.truncated
            or bool(details.get("tree_truncated"))
            or bool(details.get("entries_capped_by_reader"))
            or bool(details.get("listing_capped_by_api"))
        ),
        revision_basis=revision_basis,
        modality="code",
        counts=counts,
    )


# ---------------------------------------------------------------------------
# File (blob) with line ranges
# ---------------------------------------------------------------------------


def _read_blob(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout | None:
    if not link.path:
        # /blob/{ref} without a path is a listing, not a file: treat it as a tree.
        return _read_repo_or_tree(
            request,
            GithubLink(kind="tree", url=link.url, owner=link.owner, repo=link.repo, ref=link.ref),
            ctx,
            started,
        )
    builder = _Builder(request, link.url)
    ref = link.ref or "HEAD"
    sha = link.sha or _resolve_sha(ctx, link, ref)
    details: dict = {"ref": ref, "commit_sha": sha, "path": link.path}
    unresolved: list[str] = []

    raw: bytes | None = None
    truncated_download = False
    content_sha: str | None = None
    api_size: int | None = None
    try:
        data = _contents(ctx, link, sha, link.path)
    except GithubError:
        data = None
    if isinstance(data, dict) and data.get("encoding") == "base64" and data.get("content"):
        try:
            raw = base64.b64decode(str(data["content"]))
        except (ValueError, TypeError):
            raw = None
        api_size = data.get("size") if isinstance(data.get("size"), int) else None
        content_sha = str(data.get("sha") or "") or None
    if raw is None:
        # Files over the contents-API limit (1MB) return no inline body; raw bytes
        # are the only way to honour a line range, and they are bounded.
        max_bytes = min(
            max(int(request.budget.max_download_bytes or MAX_FILE_BYTES), 256 * 1024),
            MAX_FILE_BYTES,
        )
        raw_url = f"{RAW_ROOT}/{link.owner}/{link.repo}/{sha}/{quote(link.path, safe='/')}"
        raw, truncated_download = _github_raw(raw_url, token=ctx.token, timeout=ctx.timeout, max_bytes=max_bytes)
        ctx.requests.append(f"raw/{link.path}@{sha[:7]}")
        details["file_source"] = "raw.githubusercontent.com"

    text = raw.decode("utf-8", errors="replace")
    lines = _split_lines(text)
    total_lines = len(lines)
    file_bytes = len(raw)
    content_hash = hashlib.sha256(raw).hexdigest() if not truncated_download else None
    details.update({
        "size_bytes": file_bytes,
        "total_lines": total_lines,
        "content_hash": content_hash,
        "content_hash_basis": "sha256 of the file bytes returned by GitHub",
        "git_blob_sha": content_sha,
        "file_truncated": truncated_download,
    })
    if api_size is not None and api_size != file_bytes:
        details["api_size_bytes"] = api_size
    if truncated_download:
        details["total_lines_is_lower_bound"] = True
        unresolved.append(
            f"The file is larger than the {MAX_FILE_BYTES} byte reader limit, so only its first "
            f"{file_bytes} bytes were fetched and total_lines is a lower bound"
        )

    range_outside_file = False
    line_start = link.line_start
    line_end = link.line_end
    if line_start is not None or line_end is not None:
        start = max(1, int(line_start or 1))
        end = int(line_end or total_lines or 1)
        if start > end:
            start, end = end, start
        if total_lines and start > total_lines:
            range_outside_file = True
            unresolved.append(
                f"The requested line range L{start}-L{end} is outside the file, which has {total_lines} lines"
            )
            start = max(1, total_lines)
            end = total_lines
        end = min(end, total_lines or 1)
    else:
        start, end = 1, total_lines
    details["line_start"] = start if total_lines else None
    details["line_end"] = end if total_lines else None
    details["line_range_requested"] = (
        {"line_start": line_start, "line_end": line_end}
        if (line_start is not None or line_end is not None)
        else None
    )

    selected = lines[start - 1 : end] if total_lines else []
    body = "\n".join(selected)
    if not total_lines:
        builder.add(MaterialKind.HEADING, f"{link.full_name}/{link.path}")
        builder.add(MaterialKind.PARAGRAPH, "The file is empty.")
        unresolved.append("The file is empty")
    else:
        language = _language_for_path(link.path)
        builder.add(MaterialKind.HEADING, f"{link.full_name}/{link.path} · L{start}-L{end} of {total_lines}")
        builder.add(
            MaterialKind.PARAGRAPH,
            f"Commit: {sha}\nPath: {link.path}\nLines: {start}-{end} of {total_lines}\n"
            f"SHA-256: {content_hash or 'unavailable (file truncated)'}",
        )
        builder.add(MaterialKind.CODE, body, language=language)

    returned_lines = min(len(selected), _count_lines(builder.blocks[-1]) if builder.blocks else len(selected))
    if builder.truncated:
        unresolved.append("The response character budget truncated the returned file text")
    revision_basis = (
        f"github\0blob\0{link.full_name}\0{sha}\0{link.path}\0L{start}-L{end}"
        if total_lines
        else f"github\0blob\0{link.full_name}\0{sha}\0{link.path}"
    )
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=builder.truncated or truncated_download or range_outside_file,
        revision_basis=revision_basis,
        modality="code",
        counts={
            "discovered": total_lines,
            "extracted": total_lines,
            "returned": returned_lines,
        },
    )


def _count_lines(block: MaterialBlock | None) -> int:
    if block is None or not isinstance(block.content, TextContent):
        return 0
    return len(block.content.text.splitlines())


def _language_for_path(path: str) -> str | None:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    mapping = {
        "py": "python", "js": "javascript", "ts": "typescript", "tsx": "tsx",
        "jsx": "jsx", "go": "go", "rs": "rust", "java": "java", "rb": "ruby",
        "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cs": "csharp",
        "sh": "bash", "bash": "bash", "zsh": "bash", "ps1": "powershell",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
        "md": "markdown", "rst": "rst", "html": "html", "css": "css", "sql": "sql",
        "swift": "swift", "kt": "kotlin", "php": "php", "lua": "lua", "dart": "dart",
    }
    return mapping.get(suffix)


# ---------------------------------------------------------------------------
# Raw URL
# ---------------------------------------------------------------------------


def _read_raw(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    builder = _Builder(request, link.url)
    unresolved: list[str] = []
    max_bytes = min(
        max(int(request.budget.max_download_bytes or MAX_FILE_BYTES), 256 * 1024),
        MAX_FILE_BYTES,
    )
    data, truncated = _github_raw(link.url, token=ctx.token, timeout=ctx.timeout, max_bytes=max_bytes)
    ctx.requests.append("raw.githubusercontent.com")
    text = data.decode("utf-8", errors="replace")
    lines = _split_lines(text)
    total_lines = len(lines)
    content_hash = hashlib.sha256(data).hexdigest() if not truncated else None
    resolved_sha: str | None = link.sha or None
    if not resolved_sha:
        # A branch-named raw URL cannot be tied to a commit without a second
        # request, and the bytes already fetched would still be unverifiable
        # against that commit. Report the gap instead of guessing.
        unresolved.append(
            "This raw URL names a branch or tag rather than a commit, so the returned bytes "
            "are not pinned to a SHA; read the /blob/ URL of the same file to bind one"
        )
    details = {
        "ref": link.ref,
        "commit_sha": resolved_sha,
        "path": link.path,
        "size_bytes": len(data),
        "total_lines": total_lines,
        "content_hash": content_hash,
        "file_truncated": truncated,
    }
    if truncated:
        details["total_lines_is_lower_bound"] = True
        unresolved.append(f"The file exceeded the {MAX_FILE_BYTES} byte reader limit and was cut short")
    start, end = 1, total_lines
    if link.line_start is not None or link.line_end is not None:
        start = max(1, int(link.line_start or 1))
        end = min(int(link.line_end or total_lines or 1), total_lines or 1)
        if start > end:
            start, end = end, start
    details["line_start"] = start if total_lines else None
    details["line_end"] = end if total_lines else None
    builder.add(MaterialKind.HEADING, f"{link.full_name}/{link.path}")
    builder.add(
        MaterialKind.PARAGRAPH,
        f"Ref: {link.ref}\nCommit: {resolved_sha or 'unresolved'}\nLines: {start}-{end} of {total_lines}\n"
        f"SHA-256: {content_hash or 'unavailable'}",
    )
    builder.add(MaterialKind.CODE, "\n".join(lines[start - 1 : end]), language=_language_for_path(link.path))
    if unresolved:
        builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=builder.truncated or truncated,
        revision_basis=f"github\0raw\0{link.full_name}\0{link.ref}\0{link.path}\0{content_hash or ''}",
        modality="code",
        counts={"discovered": total_lines, "extracted": total_lines, "returned": min(end - start + 1, total_lines) if total_lines else 0},
    )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def _read_commit(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    builder = _Builder(request, link.url)
    unresolved: list[str] = []
    data = _github_json(
        f"/repos/{link.full_name}/commits/{quote(link.sha, safe='')}",
        token=ctx.token,
        timeout=ctx.timeout,
    )
    ctx.requests.append(f"commits/{link.sha[:7]}")
    sha = str(data.get("sha") or link.sha)
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    message = str(commit.get("message") or "")
    subject, _, body = message.partition("\n")
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    files = data.get("files") if isinstance(data.get("files"), list) else []
    details = {
        "commit_sha": sha,
        "subject": subject.strip(),
        "message": message,
        "author": author.get("name"),
        "author_email": author.get("email"),
        "authored_at": author.get("date"),
        "stats": {
            "additions": stats.get("additions"),
            "deletions": stats.get("deletions"),
            "total": stats.get("total"),
        },
        "files_changed": len(files),
        "files": [],
        "patch_truncated": False,
        "html_url": data.get("html_url"),
    }
    builder.add(MaterialKind.HEADING, f"{link.full_name}@{sha[:7]} — {subject.strip()}")
    meta_lines = [f"Commit: {sha}"]
    if author.get("name"):
        meta_lines.append(f"Author: {author['name']}")
    if author.get("date"):
        meta_lines.append(f"Date: {author['date']}")
    if stats:
        meta_lines.append(
            f"Stats: +{stats.get('additions', 0)} -{stats.get('deletions', 0)} "
            f"({stats.get('total', 0)} changed lines across {len(files)} files)"
        )
    builder.add(MaterialKind.PARAGRAPH, "\n".join(meta_lines))
    if body.strip():
        builder.add(MaterialKind.PARAGRAPH, body.strip())

    file_lines = []
    patch_parts = []
    patches_truncated = False
    for item in files[:300]:
        filename = str(item.get("filename") or "")
        file_lines.append(
            f"- {item.get('status', 'modified')} {filename} "
            f"(+{item.get('additions', 0)} -{item.get('deletions', 0)})"
        )
        patch = item.get("patch")
        if isinstance(patch, str) and patch.strip():
            clipped = patch
            if len(clipped) > MAX_PATCH_CHARS:
                clipped = clipped[:MAX_PATCH_CHARS] + "\n… (patch truncated)"
                patches_truncated = True
            patch_parts.append(f"### {filename}\n{clipped}")
        else:
            patches_truncated = True
            if item.get("status") != "renamed" and len(files) <= 300:
                patch_parts.append(f"### {filename}\n(patch not returned by GitHub for this file)")
        details["files"].append({
            "filename": filename,
            "status": item.get("status"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "changes": item.get("changes"),
            "patch_truncated": isinstance(patch, str) and len(patch) > MAX_PATCH_CHARS,
        })
    if len(files) > 300:
        unresolved.append("GitHub returned more than 300 changed files; only the first 300 are listed")
    details["patch_truncated"] = patches_truncated
    if patches_truncated:
        unresolved.append(
            "One or more file patches were truncated or not returned by GitHub; the file list and "
            "per-file counts are complete for the files shown"
        )
    builder.add(MaterialKind.LIST, "\n".join(file_lines) or "(no files)")
    if patch_parts:
        builder.add(MaterialKind.CODE, "\n\n".join(patch_parts), language="diff")
    if unresolved:
        builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=builder.truncated or patches_truncated or len(files) > 300,
        revision_basis=f"github\0commit\0{link.full_name}\0{sha}",
        modality="code",
        counts={"discovered": len(files), "extracted": len(files), "returned": min(len(files), 300)},
    )


# ---------------------------------------------------------------------------
# Pull request / issue
# ---------------------------------------------------------------------------


def _comment_summaries(ctx: _Context, link: GithubLink, number: int, unresolved: list[str]) -> tuple[list[dict], int]:
    try:
        data = _github_json(
            f"/repos/{link.full_name}/issues/{number}/comments?per_page={MAX_COMMENT_SUMMARIES}",
            token=ctx.token,
            timeout=ctx.timeout,
        )
        ctx.requests.append(f"issues/{number}/comments")
    except GithubError as exc:
        unresolved.append(f"Comments were not fetched ({type(exc).__name__})")
        return [], 0
    if isinstance(data, list):
        comments = data
    elif isinstance(data, dict) and isinstance(data.get("comments"), list):
        comments = data["comments"]
    else:
        return [], 0
    summaries = []
    for comment in comments[:MAX_COMMENT_SUMMARIES]:
        if not isinstance(comment, dict):
            continue
        user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        summaries.append({
            "author": user.get("login"),
            "created_at": comment.get("created_at"),
            "body": _short(str(comment.get("body") or ""), 600),
        })
    return summaries, len(comments)


def _read_pull(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    number = int(link.number or 0)
    builder = _Builder(request, link.url)
    unresolved: list[str] = []
    data = _github_json(f"/repos/{link.full_name}/pulls/{number}", token=ctx.token, timeout=ctx.timeout)
    ctx.requests.append(f"pulls/{number}")
    title = str(data.get("title") or "")
    body = str(data.get("body") or "")
    head = data.get("head") if isinstance(data.get("head"), dict) else {}
    base = data.get("base") if isinstance(data.get("base"), dict) else {}
    details = {
        "number": number,
        "title": title,
        "body": _short(body, MAX_BODY_CHARS),
        "body_truncated": len(body) > MAX_BODY_CHARS,
        "state": data.get("state"),
        "draft": data.get("draft"),
        "merged": data.get("merged"),
        "author": (data.get("user") or {}).get("login") if isinstance(data.get("user"), dict) else None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "head_sha": head.get("sha"),
        "head_ref": head.get("ref"),
        "base_sha": base.get("sha"),
        "base_ref": base.get("ref"),
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
        "changed_files": data.get("changed_files"),
        "comment_count": data.get("comments"),
        "files": [],
        "comment_summaries": [],
        "review_comments_fetched": False,
    }
    builder.add(MaterialKind.HEADING, f"{link.full_name} PR #{number} — {title}")
    meta_lines = [f"State: {data.get('state')}{' (draft)' if data.get('draft') else ''}"]
    if details["merged"]:
        meta_lines.append("Merged: yes")
    if details["author"]:
        meta_lines.append(f"Author: {details['author']}")
    if details["head_sha"]:
        meta_lines.append(f"Head commit: {details['head_sha']}")
    if details["base_ref"]:
        meta_lines.append(f"Base: {details['base_ref']} @ {details['base_sha']}")
    if data.get("changed_files") is not None:
        meta_lines.append(
            f"Changed files: {data.get('changed_files')} (+{data.get('additions', 0)} -{data.get('deletions', 0)})"
        )
    builder.add(MaterialKind.PARAGRAPH, "\n".join(meta_lines))
    if details["body"]:
        builder.add(MaterialKind.PARAGRAPH, details["body"])
        if details["body_truncated"]:
            unresolved.append(f"The PR body was truncated to {MAX_BODY_CHARS} characters")

    try:
        files_data = _github_json(
            f"/repos/{link.full_name}/pulls/{number}/files?per_page=100",
            token=ctx.token,
            timeout=ctx.timeout,
        )
        ctx.requests.append(f"pulls/{number}/files")
        if isinstance(files_data, list):
            file_items = files_data
        else:
            file_items = files_data.get("files") if isinstance(files_data, dict) else []
        if not isinstance(file_items, list):
            file_items = []
    except GithubError as exc:
        file_items = []
        unresolved.append(f"The PR file list was not fetched ({type(exc).__name__})")
    file_lines = []
    for item in file_items[:300]:
        if not isinstance(item, dict):
            continue
        patch = item.get("patch") if isinstance(item.get("patch"), str) else None
        details["files"].append({
            "filename": item.get("filename"),
            "status": item.get("status"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "patch": _short(patch, MAX_PATCH_CHARS) if patch else None,
            "patch_truncated": bool(patch and len(patch) > MAX_PATCH_CHARS),
        })
        file_lines.append(
            f"- {item.get('status', 'modified')} {item.get('filename')} "
            f"(+{item.get('additions', 0)} -{item.get('deletions', 0)})"
        )
    if file_lines:
        builder.add(MaterialKind.LIST, "\n".join(file_lines))
    if any(item["patch_truncated"] for item in details["files"]):
        unresolved.append("One or more PR patches were truncated")
    if data.get("changed_files") and len(file_items) < int(data["changed_files"]):
        unresolved.append(
            f"Only {len(file_items)} of {data['changed_files']} changed files were fetched (first page)"
        )

    comment_count = int(data.get("comments") or 0)
    summaries, fetched = _comment_summaries(ctx, link, number, unresolved)
    if comment_count and not summaries:
        unresolved.append(f"{comment_count} comments exist but none were fetched")
    details["comment_summaries"] = summaries
    if summaries:
        builder.add(
            MaterialKind.PARAGRAPH,
            "\n".join(
                f"- {item['author']} ({item['created_at']}): {item['body']}" for item in summaries
            ),
        )
    unresolved.append("Pull request review comments (inline diff discussions) are not fetched")
    if unresolved:
        builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))
    revision = f"github\0pull\0{link.full_name}\0{number}\0{head.get('sha') or ''}"
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=builder.truncated or bool(details["body_truncated"]) or bool(file_items and len(file_items) < int(data.get("changed_files") or 0)),
        revision_basis=revision,
        modality="code",
        counts={
            "discovered": int(data.get("changed_files") or len(file_items)),
            "extracted": len(file_items),
            "returned": len(summaries),
        },
    )


def _read_issue(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    number = int(link.number or 0)
    builder = _Builder(request, link.url)
    unresolved: list[str] = []
    data = _github_json(f"/repos/{link.full_name}/issues/{number}", token=ctx.token, timeout=ctx.timeout)
    ctx.requests.append(f"issues/{number}")
    title = str(data.get("title") or "")
    body = str(data.get("body") or "")
    labels = [
        str(label.get("name")) for label in (data.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    details = {
        "number": number,
        "title": title,
        "body": _short(body, MAX_BODY_CHARS),
        "body_truncated": len(body) > MAX_BODY_CHARS,
        "state": data.get("state"),
        "author": (data.get("user") or {}).get("login") if isinstance(data.get("user"), dict) else None,
        "labels": labels,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "comment_count": data.get("comments"),
        "comment_summaries": [],
    }
    builder.add(MaterialKind.HEADING, f"{link.full_name} issue #{number} — {title}")
    meta_lines = [f"State: {data.get('state')}"]
    if details["author"]:
        meta_lines.append(f"Author: {details['author']}")
    if labels:
        meta_lines.append(f"Labels: {', '.join(labels)}")
    if details["comment_count"]:
        meta_lines.append(f"Comments: {details['comment_count']}")
    builder.add(MaterialKind.PARAGRAPH, "\n".join(meta_lines))
    if details["body"]:
        builder.add(MaterialKind.PARAGRAPH, details["body"])
        if details["body_truncated"]:
            unresolved.append(f"The issue body was truncated to {MAX_BODY_CHARS} characters")
    summaries, _fetched = _comment_summaries(ctx, link, number, unresolved)
    details["comment_summaries"] = summaries
    if isinstance(data.get("comments"), int) and data["comments"] and not summaries:
        unresolved.append(f"{data['comments']} comments exist but none were fetched")
    if summaries:
        builder.add(
            MaterialKind.PARAGRAPH,
            "\n".join(
                f"- {item['author']} ({item['created_at']}): {item['body']}" for item in summaries
            ),
        )
    if unresolved:
        builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=builder.truncated or bool(details["body_truncated"]),
        revision_basis=f"github\0issue\0{link.full_name}\0{number}\0{data.get('updated_at') or ''}",
        modality="text",
        counts={
            "discovered": int(data.get("comments") or 0) + 1,
            "extracted": 1 + len(summaries),
            "returned": 1 + len(summaries),
        },
    )


# ---------------------------------------------------------------------------
# Gist (basic title / file list only)
# ---------------------------------------------------------------------------


def _read_gist(request: ReadRequest, link: GithubLink, ctx: _Context, started: float) -> _Readout:
    builder = _Builder(request, link.url)
    data = _github_json(f"/gists/{link.gist_id}", token=ctx.token, timeout=ctx.timeout)
    ctx.requests.append(f"gists/{link.gist_id}")
    description = str(data.get("description") or "")
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    file_names = list(files.keys())
    details = {
        "gist_id": link.gist_id,
        "description": description,
        "owner": (data.get("owner") or {}).get("login") if isinstance(data.get("owner"), dict) else link.owner,
        "public": data.get("public"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "files": {},
    }
    for name, item in files.items():
        if isinstance(item, dict):
            details["files"][name] = {
                "size": item.get("size"),
                "language": item.get("language"),
                "truncated": item.get("truncated"),
            }
    builder.add(MaterialKind.HEADING, f"Gist {link.gist_id} — {description or 'no description'}")
    builder.add(
        MaterialKind.LIST,
        "\n".join(f"- {name} ({details['files'].get(name, {}).get('size') or '?'} B)" for name in file_names)
        or "(no files)",
    )
    unresolved = [
        "Gist file contents are not fetched by this reader; open the file's raw URL to read it",
    ]
    builder.add(MaterialKind.PARAGRAPH, "\n".join(f"Note: {item}" for item in unresolved))
    return _Readout(
        blocks=builder.blocks,
        details=details,
        unresolved=unresolved,
        partial=True,
        revision_basis=f"github\0gist\0{link.gist_id}\0{data.get('updated_at') or ''}",
        modality="text",
        counts={"discovered": len(file_names), "extracted": len(file_names), "returned": len(file_names)},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_github_source(request: ReadRequest, source_url: str | None = None) -> ReadResponse | None:
    """Read a GitHub URL with the REST API, or return ``None`` to fall back.

    ``None`` means "this is not a GitHub resource this reader owns" (site pages,
    wiki, gist raw, ...). Once the URL is recognized, a failure is reported as a
    structured ``ReadResponse`` rather than delegated to the browser path: a
    GitHub API error must not cost a Chromium boot.
    """

    link = parse_github_url(str(source_url or request.source.url))
    if link is None:
        return None

    if request.scope.locator and link.line_start is None and link.line_end is None:
        start, end = _line_range_from_fragment(str(request.scope.locator))
        if start is not None:
            link = GithubLink(
                kind=link.kind,
                url=link.url,
                owner=link.owner,
                repo=link.repo,
                ref=link.ref,
                path=link.path,
                number=link.number,
                sha=link.sha,
                gist_id=link.gist_id,
                line_start=start,
                line_end=end,
            )
    if link.line_start is not None and link.line_end is not None and link.line_end < link.line_start:
        return _blocked(
            request,
            ErrorCode.INVALID_REQUEST,
            f"The requested line range L{link.line_start}-L{link.line_end} is inverted",
            details={"reader": "github", "github_error_code": "invalid_line_range"},
        )

    started = time.monotonic()
    token = _token()
    ctx = _Context(
        token=token,
        timeout=max(5.0, min(DEFAULT_TIMEOUT, float(request.budget.max_work_seconds or DEFAULT_TIMEOUT))),
        authenticated=bool(token),
    )
    try:
        if link.kind in {"repo", "tree"}:
            readout = _read_repo_or_tree(request, link, ctx, started)
        elif link.kind == "blob":
            readout = _read_blob(request, link, ctx, started)
        elif link.kind == "raw":
            readout = _read_raw(request, link, ctx, started)
        elif link.kind == "commit":
            readout = _read_commit(request, link, ctx, started)
        elif link.kind == "pull":
            readout = _read_pull(request, link, ctx, started)
        elif link.kind == "issue":
            readout = _read_issue(request, link, ctx, started)
        elif link.kind == "gist":
            readout = _read_gist(request, link, ctx, started)
        else:  # pragma: no cover - parse_github_url never emits another kind
            return None
    except GithubRateLimited as exc:
        details = {
            "reader": "github",
            "github_error_code": "github_rate_limited",
            "status": exc.status,
            "reset_at": exc.reset_at,
            "reset_epoch": exc.reset_epoch,
            "authenticated": ctx.authenticated,
            "api_requests": list(ctx.requests),
            "hint": "Set GITHUB_TOKEN (or GH_TOKEN) to raise the API quota, then retry after the reset time",
        }
        return _blocked(
            request,
            ErrorCode.NETWORK_ERROR,
            f"GitHub API rate limit reached; retry after {exc.reset_at or 'the reset time'} "
            f"or set GITHUB_TOKEN to raise the quota",
            retryable=True,
            details=details,
        )
    except GithubNotFound:
        return _blocked(
            request,
            ErrorCode.SOURCE_UNAVAILABLE,
            f"GitHub returned 404 for {_safe_url(link.url)}. The repository, file, commit, PR or "
            f"issue does not exist publicly, or it is private",
            details={
                "reader": "github",
                "github_error_code": "github_not_found",
                "status": 404,
                "authenticated": ctx.authenticated,
                "hint": "Private repositories need GITHUB_TOKEN with repo read access",
                "api_requests": list(ctx.requests),
            },
        )
    except GithubUnauthorized:
        return _blocked(
            request,
            ErrorCode.AUTH_REQUIRED,
            "GitHub rejected the configured token (401). Check GITHUB_TOKEN / GH_TOKEN",
            action_required=True,
            details={
                "reader": "github",
                "github_error_code": "github_auth_failed",
                "status": 401,
                "api_requests": list(ctx.requests),
            },
        )
    except GithubUnavailable as exc:
        return _blocked(
            request,
            ErrorCode.NETWORK_ERROR,
            f"The GitHub API could not be reached ({exc}); the page was not opened in a browser",
            retryable=True,
            details={
                "reader": "github",
                "github_error_code": "github_unreachable",
                "api_requests": list(ctx.requests),
            },
        )
    except GithubError as exc:
        return _blocked(
            request,
            ErrorCode.SOURCE_UNAVAILABLE,
            f"GitHub API error: {exc}",
            details={
                "reader": "github",
                "github_error_code": "github_api_error",
                "status": exc.status,
                "api_requests": list(ctx.requests),
            },
        )
    except Exception:  # noqa: BLE001 - a reader bug must not be worse than today's path
        return None

    return _response(
        request,
        link,
        ctx,
        started,
        blocks=readout.blocks,
        details=readout.details,
        unresolved=readout.unresolved,
        partial=readout.partial,
        revision_basis=readout.revision_basis,
        modality=readout.modality,
        counts=readout.counts,
    )


def _safe_url(url: str) -> str:
    """Drop any userinfo/query secrets from a URL before it enters an error message."""

    parsed = urlparse(url)
    host = parsed.hostname or ""
    return f"https://{host}{parsed.path}"
