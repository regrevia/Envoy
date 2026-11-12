"""A per-commit index of what this reader has listed and read in a repository.

The GitHub reader answers one URL per call: a repository root, one directory,
one file. A caller exploring a repository therefore has no way to tell an empty
repository from a repository whose interesting half it has not opened yet, and
"what has this session already covered at this commit" is not recoverable from
the conversation.

This module keeps exactly that bookkeeping and nothing else: it does not mirror
file contents, does not build a database, and does not make API requests. It is
a small JSON document per ``(repository, commit_sha)`` recording

* which directories were expanded, at which depth, and which were only named;
* which files were read completely, read partially (with a percentage), or
  only listed;
* what remains unresolved at this commit.

Every entry is pinned to a commit SHA, so a manifest can never describe two
different versions of the same path. Recording is best-effort: a state
directory that cannot be written degrades the manifest to the current call
instead of failing a read.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

SCHEMA = "envoy.github-index/1"
ENV_INDEX_DIR = "ENVOY_GITHUB_INDEX_DIR"

# The index is shell for a conversation, not a repository mirror: a bound keeps
# a monorepo walk from turning into a second copy of the tree.
MAX_DIRECTORIES = 512
MAX_FILES = 4096
# A root listing of a monorepo can name hundreds of directories; the manifest
# needs the set to stay honest, the payload needs it small.
MAX_CHILD_DIRS = 256
# A manifest that ships inside an MCP response must stay readable: the payload
# names a few paths of each kind and gives the exact counts for the rest.
SUMMARY_PATHS = 8


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def index_dir() -> Path:
    configured = os.getenv(ENV_INDEX_DIR, "").strip()
    if configured:
        return Path(configured).expanduser()
    # Same root as retained material: tests point ENVOY_LOCAL_DATA_DIR at a temp
    # directory, so the index follows it instead of writing into the home dir.
    # ``resolve_material_root`` is the resolver the reader itself uses, so an
    # index written by a read is later found by the same process that listed it.
    from . import storage

    return storage.resolve_material_root() / "github-index"


def _index_path(repository: str, commit_sha: str) -> Path:
    owner, _, name = repository.partition("/")
    safe = "".join(char if char.isalnum() or char in "-._" else "_" for char in f"{owner}__{name}")
    sha = "".join(char for char in commit_sha if char.isalnum())[:40] or "unknown"
    return index_dir() / f"{safe[:96]}__{sha}.json"


def _empty(repository: str, commit_sha: str) -> dict:
    return {
        "schema": SCHEMA,
        "repository": repository,
        "commit_sha": commit_sha,
        "updated_at": None,
        "directories": {},
        "files": {},
        "unresolved": [],
        "dirs_omitted": 0,
        "files_omitted": 0,
        "reads": {"listings": 0, "files": 0},
        "persisted": False,
    }


def _coerce(raw: object, repository: str, commit_sha: str) -> dict:
    """Load a stored index, discarding anything that is not the shape we write."""

    index = _empty(repository, commit_sha)
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return index
    if str(raw.get("repository") or "") != repository or str(raw.get("commit_sha") or "") != commit_sha:
        return index
    directories = raw.get("directories")
    if isinstance(directories, dict):
        for path, entry in directories.items():
            if isinstance(path, str) and isinstance(entry, dict):
                children = [
                    str(child)
                    for child in entry.get("child_dirs") or []
                    if isinstance(child, str) and child
                ][:MAX_CHILD_DIRS]
                index["directories"][path] = {
                    "mode": str(entry.get("mode") or "one_level"),
                    "children": int(entry.get("children") or 0),
                    "child_dirs": children,
                    "complete": bool(entry.get("complete")),
                }
    files = raw.get("files")
    if isinstance(files, dict):
        for path, entry in files.items():
            if not isinstance(path, str) or not isinstance(entry, dict):
                continue
            state = str(entry.get("state") or "listed")
            if state not in {"listed", "partial", "complete"}:
                state = "listed"
            percent = entry.get("percent")
            index["files"][path] = {
                "state": state,
                "percent": int(percent) if isinstance(percent, int) else None,
            }
    unresolved = raw.get("unresolved")
    if isinstance(unresolved, list):
        index["unresolved"] = [str(item) for item in unresolved if isinstance(item, str)][:MAX_FILES]
    for key in ("dirs_omitted", "files_omitted"):
        value = raw.get(key)
        if isinstance(value, int) and value > 0:
            index[key] = value
    reads = raw.get("reads")
    if isinstance(reads, dict):
        index["reads"] = {
            "listings": int(reads.get("listings") or 0),
            "files": int(reads.get("files") or 0),
        }
    index["updated_at"] = str(raw.get("updated_at")) if raw.get("updated_at") else None
    return index


def load(repository: str, commit_sha: str) -> dict:
    """Read the index for one commit. Never raises; a miss is an empty index."""

    index = _empty(repository, commit_sha)
    if not repository or not commit_sha:
        return index
    path = _index_path(repository, commit_sha)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return index
    return _coerce(raw, repository, commit_sha)


def save(index: dict) -> bool:
    """Write the index atomically. Returns whether the caller can rely on it."""

    repository = str(index.get("repository") or "")
    commit_sha = str(index.get("commit_sha") or "")
    if not repository or not commit_sha:
        return False
    path = _index_path(repository, commit_sha)
    index["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        index["persisted"] = True
        return True
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        index["persisted"] = False
        return False


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _bounded_add(mapping: dict, key: str, value: dict, limit: int, index: dict, counter: str) -> None:
    if key in mapping:
        mapping[key] = value
        return
    if len(mapping) >= limit:
        index[counter] = int(index.get(counter) or 0) + 1
        return
    mapping[key] = value


def record_listing(
    repository: str,
    commit_sha: str,
    *,
    path: str,
    plan: str,
    entries: list[dict],
    recursive: bool = False,
    tree_truncated: bool = False,
    capped_by_api: bool = False,
    capped_by_reader: bool = False,
) -> tuple[dict, bool]:
    """Record that ``path`` was listed and which children the response named.

    ``path`` is ``""`` for the repository root. ``recursive`` means the tree
    request covered every directory below ``path`` (unless truncated), so those
    directories count as expanded rather than merely named.
    """

    index = load(repository, commit_sha)
    paths = [str(entry.get("path") or "") for entry in entries if isinstance(entry, dict)]
    child_dirs = [
        str(entry.get("path") or "")
        for entry in entries
        if isinstance(entry, dict) and entry.get("type") == "tree" and entry.get("path")
    ]
    complete = not (tree_truncated or capped_by_api or capped_by_reader)
    # A recursive listing that was truncated proves only the directories it
    # actually named; the rest of the tree was never delivered.
    mode = "recursive" if (recursive and not tree_truncated) else "one_level"
    _bounded_add(
        index["directories"],
        path,
        {
            "mode": mode,
            "children": len(paths),
            "child_dirs": child_dirs[:MAX_CHILD_DIRS],
            "complete": complete,
        },
        MAX_DIRECTORIES,
        index,
        "dirs_omitted",
    )
    if mode == "recursive" and not tree_truncated:
        # Every directory below this one arrived in the same payload.
        for child in child_dirs:
            if child in index["directories"]:
                # A directory already listed on its own keeps its own child
                # count; this payload only proves it was covered.
                continue
            _bounded_add(
                index["directories"],
                child,
                {"mode": "recursive", "children": 0, "child_dirs": [], "complete": complete},
                MAX_DIRECTORIES,
                index,
                "dirs_omitted",
            )
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        blob = str(entry.get("path") or "")
        if not blob or blob in index["files"]:
            continue
        _bounded_add(
            index["files"], blob, {"state": "listed", "percent": None}, MAX_FILES, index, "files_omitted"
        )
    if tree_truncated:
        _note(index, f"GitHub truncated the tree response at {repository}@{commit_sha[:7]}; some entries are missing")
    elif capped_by_api:
        _note(index, "The tree listing was capped by the GitHub API; some entries are missing")
    elif capped_by_reader:
        _note(index, "The listing was capped by the reader's entry limit; some entries are missing")
    index["reads"]["listings"] = int(index["reads"].get("listings") or 0) + 1
    save(index)
    return index, bool(index.get("persisted"))


def record_file(
    repository: str,
    commit_sha: str,
    *,
    path: str,
    total_lines: int,
    delivered_end: int | None,
    complete: bool,
    delivered_start: int = 1,
    note: str | None = None,
) -> tuple[dict, bool]:
    """Record how much of one file at this commit the caller has actually seen.

    The percentage measures the delivered span, not its end offset: lines 4-6 of
    a ten-line file are three lines seen, not sixty percent read.
    """

    index = load(repository, commit_sha)
    if not path or not total_lines:
        return index, False
    end = int(delivered_end or 0)
    seen = max(0, end - max(1, int(delivered_start)) + 1)
    percent = 100 if complete else max(0, min(99, round(100 * seen / total_lines)))
    previous = index["files"].get(path) or {}
    state = "complete" if (complete or previous.get("state") == "complete") else "partial"
    best = max(percent, int(previous.get("percent") or 0))
    record = {"state": state, "percent": 100 if complete else best}
    _bounded_add(index["files"], path, record, MAX_FILES, index, "files_omitted")
    if note:
        _note(index, note)
    index["reads"]["files"] = int(index["reads"].get("files") or 0) + 1
    save(index)
    return index, bool(index.get("persisted"))


def _note(index: dict, message: str) -> None:
    if not message:
        return
    if message in index["unresolved"]:
        return
    if len(index["unresolved"]) >= MAX_FILES:
        return
    index["unresolved"].append(message)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _named_directories(directories: dict) -> set[str]:
    """Every directory a listing has named, whether or not it was expanded."""

    named: set[str] = set(directories)
    for entry in directories.values():
        if isinstance(entry, dict):
            for child in entry.get("child_dirs") or []:
                if isinstance(child, str) and child:
                    named.add(child)
    return named


def manifest(index: dict) -> dict:
    """The bounded, JSON-safe view a response carries as its repository snapshot."""

    directories = index.get("directories") or {}
    files = index.get("files") or {}
    expanded = sorted(path for path in directories)
    named = _named_directories(directories)
    unexpanded = sorted(named - set(expanded))
    read_complete = sorted(path for path, entry in files.items() if entry.get("state") == "complete")
    read_partial = sorted(
        (path, int(entry.get("percent") or 0))
        for path, entry in files.items()
        if entry.get("state") == "partial"
    )
    listed_not_read = sorted(path for path, entry in files.items() if entry.get("state") == "listed")
    return {
        "schema": SCHEMA,
        "repository": index.get("repository"),
        "commit_sha": index.get("commit_sha"),
        "updated_at": index.get("updated_at"),
        "directories": {
            "expanded": expanded[:SUMMARY_PATHS],
            "expanded_total": len(expanded),
            "listed_not_expanded": unexpanded[:SUMMARY_PATHS],
            "listed_not_expanded_total": len(unexpanded),
            "omitted": int(index.get("dirs_omitted") or 0),
        },
        "files": {
            "read_complete": read_complete[:SUMMARY_PATHS],
            "read_complete_total": len(read_complete),
            "read_partial": [{"path": path, "percent": percent} for path, percent in read_partial[:SUMMARY_PATHS]],
            "read_partial_total": len(read_partial),
            "listed_not_read": listed_not_read[:SUMMARY_PATHS],
            "listed_not_read_total": len(listed_not_read),
            "omitted": int(index.get("files_omitted") or 0),
        },
        "unresolved": list(index.get("unresolved") or [])[:SUMMARY_PATHS],
        "reads": dict(index.get("reads") or {}),
        "persisted": bool(index.get("persisted")),
    }


def describe(index: dict) -> str | None:
    """One sentence for the material itself: what this commit has covered so far."""

    snapshot = manifest(index)
    directories = snapshot["directories"]
    files = snapshot["files"]

    def counted(total: int, noun: str) -> str:
        return f"{total} {noun}" if total == 1 else f"{total} {noun}s"

    parts: list[str] = []
    if directories["expanded_total"]:
        parts.append(f"{counted(directories['expanded_total'], 'directory')} expanded")
    if directories["listed_not_expanded_total"]:
        parts.append(f"{directories['listed_not_expanded_total']} listed but not expanded")
    if files["read_complete_total"]:
        parts.append(f"{counted(files['read_complete_total'], 'file')} read completely")
    if files["read_partial_total"]:
        parts.append(f"{counted(files['read_partial_total'], 'file')} read partially")
    if files["listed_not_read_total"]:
        parts.append(f"{files['listed_not_read_total']} listed but never read")
    if not parts:
        return None
    count = snapshot["commit_sha"] or ""
    hint = "unexpanded paths are not missing — read their URL to add them"
    return f"Repository index at {count[:7]}: " + ", ".join(parts) + f". {hint}."
