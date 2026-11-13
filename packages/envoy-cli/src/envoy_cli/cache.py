"""Report and reclaim the local storage Envoy keeps for materials and caches.

Materials expire on read (``reader._material_root`` drops anything past its
``.expires`` deadline and trims the cache to ``MAX_CACHED_MATERIALS``), but two
kinds of data never were reclaimed: the OCR download and result caches, and the
idempotency ledgers, both of which keep growing because nothing ever deletes
them. ``envoy cache status`` reports what is on disk and ``envoy cache prune``
deletes the reclaimable part.

The isolated browser profile is mostly Chromium's own HTTP and code caches,
which are regenerable and grow without limit: on the measured macOS host they
were 830 MB of an 858 MB profile. Prune removes exactly those cache directories
and leaves cookies, logins and local storage alone, so neither a Chromium
download nor a sign-in is repeated; while a browser on that profile is alive,
nothing inside it is touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from . import storage

MATERIAL_DIR = "materials"
FRAMES_PREFIX = "frames-"
OCR_CACHE_DIRS = ("ocr-cache", "ocr-inputs")
MEDIA_PREFIX = "video-"
BROWSER_PROFILE_ENV = "ENVOY_BROWSER_PROFILE_DIR"
# Shared derivation caches that live beside the material root. They are not
# retained reads: a commit-pinned GitHub index stays useful for days, while an
# HTTP body entry is only a shortcut until its freshness window turns it into a
# revalidation. Past these ages they are pure disk, so ``prune`` reclaims them.
GITHUB_INDEX_DIR = "github-index"
HTTP_CACHE_DIR = "http-cache"
GITHUB_INDEX_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
HTTP_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
# Chromium's regenerable caches. Everything else in the profile — Cookies,
# Login Data, Local Storage, IndexedDB — is a session and is never deleted here.
BROWSER_CACHE_DIRS = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/Service Worker/CacheStorage",
    "GraphiteDawnCache",
    "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache",
)

_MATERIAL_ID = re.compile(r"mat-[a-f0-9]{24}")


def data_root() -> Path:
    return storage.resolve_material_root()


def envoy_home() -> Path:
    return Path.home() / ".envoy"


def _size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_dir():
            total = 0
            for item in path.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    total += item.lstat().st_size
            return total
        return path.lstat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _text_float(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _operation_expiry(path: Path) -> float:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return float(record.get("expires_at") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _material_state(root: Path, now: float) -> tuple[set[str], list[Path]]:
    """Return the live material ids and the files that may be deleted.

    The rules mirror ``reader._material_root``: a material is live only when its
    ``.expires`` file is readable, still in the future, and backed by a real
    ``.json``; everything else is an orphan or an expired entry.
    """

    live: set[str] = set()
    stale: set[Path] = set()
    for expiry in root.glob("mat-*.expires"):
        material_id = expiry.name.removesuffix(".expires")
        if not _MATERIAL_ID.fullmatch(material_id):
            continue
        material = root / f"{material_id}.json"
        if expiry.is_symlink():
            stale.add(expiry)
            continue
        expires_at = _text_float(expiry)
        if (
            expires_at is None
            or now >= expires_at
            or material.is_symlink()
            or not material.is_file()
        ):
            stale.add(expiry)
            stale.add(material)
            continue
        live.add(material_id)
    for material in root.glob("mat-*.json"):
        material_id = material.stem
        if _MATERIAL_ID.fullmatch(material_id) and material_id not in live:
            stale.add(material)
    return live, sorted(stale)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def _remove(path: Path, root: Path, dry_run: bool) -> dict:
    size = _size(path)
    is_dir = path.is_dir() and not path.is_symlink()
    entry = {
        "kind": "dir" if is_dir else "file",
        "path": str(path),
        "bytes": size,
    }
    if dry_run or not _is_inside(path, root):
        return entry
    try:
        if is_dir:
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    return entry


def _summary(paths: list[Path]) -> dict:
    return {"count": len(paths), "bytes": sum(_size(path) for path in paths)}


def _cached_files(directory: Path) -> list[Path]:
    """Regular files in a flat cache directory, ignoring nested directories."""

    if not directory.is_dir():
        return []
    try:
        return sorted(
            item
            for item in directory.iterdir()
            if not (item.is_dir() and not item.is_symlink())
        )
    except OSError:
        return []


def _last_write(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _expired_cached_files(
    directory: Path,
    current: float,
    max_age: float,
) -> list[Path]:
    return [
        path for path in _cached_files(directory)
        if current - _last_write(path) >= max_age
    ]


def browser_profile_root(home: Path | None = None) -> Path:
    """The profile Envoy's isolated browser uses, honoring its env override.

    ``home`` is Envoy's own home (``~/.envoy``), the same directory the rest of
    this module inspects; without it the default location is used.
    """

    configured = os.getenv(BROWSER_PROFILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return (home or (Path.home() / ".envoy")) / "browser-profile"


def browser_cache_paths(profile: Path) -> list[Path]:
    return [profile / relative for relative in BROWSER_CACHE_DIRS]


def _browser_profile_in_use(profile: Path) -> bool:
    """True while a Chromium is running on this profile.

    Chromium keeps ``SingletonLock`` as a symlink to ``<hostname>-<pid>`` on
    POSIX; deleting a live HTTP cache under a running browser is not worth the
    risk, so any readable lock whose pid is alive counts as in use.
    """

    lock = profile / "SingletonLock"
    try:
        target = os.readlink(lock)
    except (OSError, AttributeError):
        target = str(lock) if lock.exists() else ""
    pid_text = str(target).rsplit("-", 1)[-1]
    if not pid_text.isdigit():
        return bool(pid_text)
    from . import process_control

    return process_control.pid_alive(int(pid_text))


def browser_cache_state(
    profile: Path | None = None, home: Path | None = None
) -> dict:
    profile = profile or browser_profile_root(home)
    paths = [path for path in browser_cache_paths(profile) if path.exists()]
    return {
        "profile": str(profile),
        "present": profile.is_dir(),
        "in_use": _browser_profile_in_use(profile) if profile.is_dir() else False,
        **_summary(paths),
        "paths": [str(path) for path in paths],
    }


def clear_browser_cache(
    profile: Path | None = None,
    *,
    home: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Delete Chromium's regenerable caches, keeping every stored session."""

    state = browser_cache_state(profile, home)
    if state["in_use"]:
        return {
            "profile": state["profile"],
            "dry_run": dry_run,
            "skipped_in_use": True,
            "removed": [],
            "freed_bytes": 0,
        }
    removed: list[dict] = []
    for path in browser_cache_paths(Path(state["profile"])):
        if not path.exists():
            continue
        removed.append({**_remove(path, path.parent, dry_run), "path": str(path)})
    return {
        "profile": state["profile"],
        "dry_run": dry_run,
        "skipped_in_use": False,
        "removed": removed,
        "freed_bytes": sum(entry["bytes"] for entry in removed),
    }


def _other_entries(home: Path, materials: Path) -> list[dict]:
    if not home.is_dir():
        return []
    entries = []
    for item in sorted(home.iterdir()):
        if item.name == MATERIAL_DIR or item == materials:
            continue
        entries.append({"name": item.name, "bytes": _size(item)})
    return entries



#: Why an entry survives a prune. These are the only outcomes a walk can
#: produce for an entry it looked at, so ``cache status`` describes a prune with
#: the same words instead of counting things the prune would never see.
KEPT_UNEXPIRED = "未过期"
KEPT_LOCKED = "被锁"
KEPT_UNREADABLE = "读取失败"


def _scandir(directory: Path) -> list[Path] | None:
    """Sorted children of a directory, or ``None`` when it cannot be read."""

    try:
        return sorted(directory.iterdir())
    except OSError:
        return None


def _kept(paths: list[Path]) -> dict:
    present = [path for path in paths if path.exists()]
    return {"files": len(present), "bytes": sum(_size(path) for path in present)}


def _entries(paths: list[Path], root: Path) -> list[dict]:
    """Describe what a prune would remove from ``paths``, without removing it."""

    return [
        _remove(path, root, True)
        for path in paths
        if path.exists() or path.is_symlink()
    ]


def _plan_row(
    path: Path,
    entries: list[dict],
    *,
    expired: bool,
    kept: list[Path] | None = None,
) -> dict:
    skipped: dict[str, dict] = {}
    if kept:
        skipped[KEPT_UNEXPIRED] = _kept(kept)
    return {
        "path": str(path),
        "expired": expired,
        "entries": entries,
        "skipped": skipped,
    }


def _public_row(row: dict) -> dict:
    """The half of a plan row a status report may show: no entry paths."""

    public = {
        "path": row["path"],
        "expired": row["expired"],
        "files": row.get("count", len(row["entries"])),
        "bytes": row.get("bytes", sum(entry["bytes"] for entry in row["entries"])),
        "kept_files": sum(bucket["files"] for bucket in row["skipped"].values()),
        "skipped": {reason: dict(bucket) for reason, bucket in row["skipped"].items()},
    }
    if "in_use" in row:
        public["in_use"] = row["in_use"]
    return public


def _discard(entry: dict, root: Path, dry_run: bool) -> str | None:
    """Remove one planned entry, or return why it stayed.

    A dry run changes nothing, so it has nothing to report as kept. A real run
    checks the path afterwards: a file another process holds open is exactly
    the case the skip reasons exist for, and claiming bytes that were not freed
    would make the prune report lie about itself.
    """

    path = Path(entry["path"])
    if dry_run:
        return None
    _remove(path, root, dry_run)
    if not (path.exists() or path.is_symlink()):
        return None
    try:
        path.stat()
    except OSError:
        # Still there but no longer resolvable: the entry itself is unreadable.
        return KEPT_UNREADABLE
    return KEPT_LOCKED


def _enumerate_cache(root: Path, current: float, home: Path | None) -> dict:
    """Walk the cache once and return both the status numbers and a prune plan.

    ``cache status`` and ``cache prune`` used to walk the cache separately and
    could disagree about what is reclaimable. Neither counts on its own any
    more: this walk decides what an entry is, how big it is, whether it is
    expired and why a kept one is kept. ``categories`` keeps the historical
    shape the status report prints; ``plan`` holds the entries a prune acts on.
    """

    unreadable: list[str] = []
    plan: dict[str, dict] = {}

    children = _scandir(root)
    if children is None:
        if root.is_dir():
            unreadable.append(str(root))
        children = []
        live: set[str] = set()
        stale: list[Path] = []
    else:
        live, stale = _material_state(root, current)

    material_files = [
        root / f"{material_id}{suffix}"
        for material_id in sorted(live)
        for suffix in (".json", ".expires")
    ]
    plan["materials"] = _plan_row(
        root,
        _entries(sorted(stale), root),
        expired=bool(stale),
        kept=material_files,
    )

    frames = [item for item in children if item.name.startswith(FRAMES_PREFIX)]
    live_frames = [
        item
        for item in frames
        if not item.is_symlink() and item.name.removeprefix(FRAMES_PREFIX) in live
    ]
    orphan_frames = [item for item in frames if item not in live_frames]
    plan["frames"] = _plan_row(
        root,
        _entries(orphan_frames, root),
        expired=bool(orphan_frames),
        kept=live_frames,
    )

    ocr: dict[str, dict] = {}
    for name in OCR_CACHE_DIRS:
        directory = root / name
        items = _scandir(directory)
        if items is None:
            if directory.is_dir():
                unreadable.append(str(directory))
            items = []
        key = name.replace("-", "_")
        ocr[key] = {"count": len(items), "bytes": sum(_size(item) for item in items)}
        plan[key] = _plan_row(directory, _entries(items, root), expired=bool(items))

    media = [item for item in children if item.name.startswith(MEDIA_PREFIX)]
    live_media: list[Path] = []
    for path in media:
        # Kept media lives directly under the material root, beside the material
        # files, and carries its own ``.expires`` because it is reclaimed much
        # earlier than the text it belongs to. Only a real, populated, unexpired
        # directory of a live material is a keeper; anything else is an orphan,
        # a symlink to unlink but never follow, or a spent lease.
        if path.is_symlink() or not path.is_dir():
            continue
        expires_at = _text_float(path / ".expires")
        if expires_at is not None and current >= expires_at:
            continue
        try:
            populated = any(entry.name != ".expires" for entry in path.iterdir())
        except OSError:
            unreadable.append(str(path))
            continue
        if populated and path.name.removeprefix(MEDIA_PREFIX) in live:
            live_media.append(path)
    orphan_media = [path for path in media if path not in live_media]
    plan["video_media"] = _plan_row(
        root,
        _entries(orphan_media, root),
        expired=bool(orphan_media),
        kept=live_media,
    )

    operations = [
        item
        for item in children
        if item.name.startswith("operation-") and item.name.endswith(".json")
    ]
    expired_operations = [
        path for path in operations if 0 < _operation_expiry(path) <= current
    ]
    operation_entries: list[dict] = []
    for operation in expired_operations:
        operation_entries.append(_remove(operation, root, True))
        lock = operation.with_suffix(".lock")
        if lock.exists() or lock.is_symlink():
            operation_entries.append(_remove(lock, root, True))
    plan["operations"] = _plan_row(
        root,
        operation_entries,
        expired=bool(expired_operations),
        kept=[path for path in operations if path not in expired_operations],
    )

    # Shared derivation caches are rebuilt on the next read, so age is their
    # only lifetime question. Their lock files go with the entry they guard.
    shared: dict[str, dict] = {}
    for key, name, max_age in (
        ("github_index", GITHUB_INDEX_DIR, GITHUB_INDEX_MAX_AGE_SECONDS),
        ("http_cache", HTTP_CACHE_DIR, HTTP_CACHE_MAX_AGE_SECONDS),
    ):
        directory = root / name
        listed = _scandir(directory)
        if listed is None:
            if directory.is_dir():
                unreadable.append(str(directory))
            listed = []
        files = [
            item for item in listed if not (item.is_dir() and not item.is_symlink())
        ]
        expired = [
            path for path in files if current - _last_write(path) >= max_age
        ]
        shared[key] = {
            "count": len(files),
            "bytes": sum(_size(path) for path in files),
            "expired_count": len(expired),
        }
        plan[key] = _plan_row(
            directory,
            _entries(expired, root),
            expired=bool(expired),
            kept=[path for path in files if path not in expired],
        )

    if home is None:
        # Reading a profile the caller never named would reach into the real
        # home; prune only reports on the profile it was given, so an unnamed
        # one stays out of scope here too.
        browser = {
            "profile": "",
            "present": False,
            "in_use": False,
            "count": 0,
            "bytes": 0,
        }
    else:
        browser = browser_cache_state(home=home)
    plan["browser_cache"] = {
        "path": browser["profile"],
        "expired": False,
        "count": browser["count"],
        "bytes": browser["bytes"],
        "entries": [],
        "skipped": {},
        "in_use": browser["in_use"],
    }

    categories = {
        "materials": {
            "count": len(material_files),
            "bytes": sum(_size(path) for path in material_files),
            "expired_count": len(stale),
            "expired_bytes": sum(_size(path) for path in stale),
        },
        "frames": {
            "count": len(live_frames),
            "bytes": sum(_size(path) for path in live_frames),
            "orphan_count": len(orphan_frames),
            "orphan_bytes": sum(_size(path) for path in orphan_frames),
        },
        **ocr,
        "video_media": {
            "count": len(live_media),
            "bytes": sum(_size(path) for path in live_media),
            "orphan_count": len(orphan_media),
            "orphan_bytes": sum(_size(path) for path in orphan_media),
        },
        "operations": {
            "count": len(operations),
            "bytes": sum(_size(path) for path in operations),
            "expired_count": len(expired_operations),
            "expired_bytes": sum(_size(path) for path in expired_operations),
        },
        **shared,
        "browser_cache": browser,
    }

    others: list[dict] = []
    if home is not None:
        try:
            others = _other_entries(home, root)
        except OSError:
            unreadable.append(str(home))

    # The browser profile already appears whole under ``other``; its cache
    # category is the reclaimable part of it, so counting both would lie. Its
    # plan row stays out of the reclaimable totals as well: whether it can be
    # freed at all depends on a running browser, which ``in_use`` reports.
    local = [key for key in plan if key != "browser_cache"]
    total = sum(
        category["bytes"]
        for key, category in categories.items()
        if key != "browser_cache"
    )
    total += sum(entry["bytes"] for entry in others)
    return {
        "total_bytes": total,
        "reclaimable_files": sum(len(plan[key]["entries"]) for key in local),
        "reclaimable_bytes": sum(
            entry["bytes"] for key in local for entry in plan[key]["entries"]
        ),
        "categories": categories,
        "plan": plan,
        "other": others,
        "unreadable": unreadable,
    }


def cache_status(
    root: Path | None = None,
    *,
    now: float | None = None,
    home: Path | None = None,
) -> dict:
    """Report the size of every local cache category without deleting anything."""

    root = root or data_root()
    home = home or envoy_home()
    current = time.time() if now is None else now
    inventory = _enumerate_cache(root, current, home)
    return {
        "materials_root": str(root),
        "envoy_home": str(home),
        "total_bytes": inventory["total_bytes"],
        "reclaimable_files": inventory["reclaimable_files"],
        "reclaimable_bytes": inventory["reclaimable_bytes"],
        "categories": inventory["categories"],
        # The same rows ``cache prune`` acts on, without the entries themselves:
        # a caller can predict a prune from the status, category by category.
        "reclaimable": {
            key: _public_row(row) for key, row in inventory["plan"].items()
        },
        "other": inventory["other"],
        "unreadable": inventory["unreadable"],
    }


def prune_cache(
    root: Path | None = None,
    *,
    dry_run: bool = False,
    now: float | None = None,
    home: Path | None = None,
) -> dict:
    """Delete expired materials, orphan frames, OCR caches and spent ledgers."""

    root = root or data_root()
    current = time.time() if now is None else now
    # ``home`` decides whether the browser profile is in scope at all: pruning
    # without one must not reach into a profile the caller did not name.
    inventory = _enumerate_cache(root, current, home)
    browser = None
    if home is not None:
        browser = clear_browser_cache(home=home, dry_run=dry_run)
    removed: list[dict] = []
    if browser and not browser["skipped_in_use"]:
        removed.extend(browser["removed"])

    categories: dict[str, dict] = {}
    for key, row in inventory["plan"].items():
        if key == "browser_cache":
            # ``clear_browser_cache`` owns that category; its plan row exists so
            # ``cache status`` can predict exactly what it frees.
            continue
        removed_files = 0
        removed_bytes = 0
        skipped = {reason: dict(bucket) for reason, bucket in row["skipped"].items()}
        for entry in row["entries"]:
            reason = _discard(entry, root, dry_run)
            if reason is None:
                removed.append(entry)
                removed_files += 1
                removed_bytes += entry["bytes"]
                continue
            bucket = skipped.setdefault(reason, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += entry["bytes"]
        categories[key] = {
            "path": row["path"],
            "expired": row["expired"],
            "removed_files": removed_files,
            "removed_bytes": removed_bytes,
            "kept_files": sum(bucket["files"] for bucket in skipped.values()),
            "kept_bytes": sum(bucket["bytes"] for bucket in skipped.values()),
            "skipped": skipped,
        }

    return {
        "materials_root": str(root),
        "dry_run": dry_run,
        "removed": removed,
        "freed_bytes": sum(entry["bytes"] for entry in removed),
        "categories": categories,
        "unreadable": inventory["unreadable"],
        "browser_cache": browser,
    }


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


STATUS_LABELS = {
    "materials": "materials",
    "frames": "video frames",
    "ocr_inputs": "ocr downloads",
    "ocr_cache": "ocr results",
    "video_media": "raw video media",
    "operations": "operation ledgers",
    "browser_cache": "browser cache",
}


def render_status(report: dict) -> str:
    lines = [f"materials root: {report['materials_root']}"]
    for key, label in STATUS_LABELS.items():
        category = report["categories"][key]
        note = ""
        if category.get("expired_count"):
            note = (
                f"  ({category['expired_count']} expired, "
                f"{human_bytes(category['expired_bytes'])})"
            )
        elif category.get("orphan_count"):
            note = (
                f"  ({category['orphan_count']} orphaned, "
                f"{human_bytes(category['orphan_bytes'])})"
            )
        elif key == "browser_cache":
            note = "  (in use)" if category.get("in_use") else "  (reclaimable)"
        lines.append(
            f"  {label:<18} {category['count']:>4} items  "
            f"{human_bytes(category['bytes']):>9}{note}"
        )
    for entry in report["other"]:
        lines.append(f"  {entry['name']:<18} {'':>10}  {human_bytes(entry['bytes']):>9}")
    lines.append(
        f"  {'reclaimable':<18} {report['reclaimable_files']:>4} items  "
        f"{human_bytes(report['reclaimable_bytes']):>9}"
    )
    lines.append(f"  {'total':<18} {'':>10}  {human_bytes(report['total_bytes']):>9}")
    return "\n".join(lines)


def render_prune(report: dict) -> str:
    if report["dry_run"]:
        head = (
            f"would remove {len(report['removed'])} entries "
            f"({human_bytes(report['freed_bytes'])})"
        )
    else:
        head = (
            f"removed {len(report['removed'])} entries "
            f"({human_bytes(report['freed_bytes'])} freed)"
        )
    lines = [head]
    categories = report.get("categories") or {}
    for key, label in STATUS_LABELS.items():
        category = categories.get(key)
        if not category or not category["removed_files"]:
            continue
        note = ""
        locked = category["skipped"].get(KEPT_LOCKED)
        if locked:
            note = f"  ({locked['files']} locked)"
        lines.append(
            f"  {label:<18} {category['removed_files']:>4} items  "
            f"{human_bytes(category['removed_bytes']):>9}{note}"
        )
    browser = report.get("browser_cache") or {}
    if browser.get("removed"):
        lines.append(
            f"  {'browser cache':<18} {len(browser['removed']):>4} items  "
            f"{human_bytes(browser['freed_bytes']):>9}"
        )
    return "\n".join(lines)
