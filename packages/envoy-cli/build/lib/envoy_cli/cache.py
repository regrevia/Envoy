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
    live, stale = _material_state(root, current) if root.is_dir() else (set(), [])

    frames = sorted(root.glob("frames-*")) if root.is_dir() else []
    live_frames = [path for path in frames if path.name.removeprefix(FRAMES_PREFIX) in live]
    orphan_frames = [path for path in frames if path not in live_frames]

    ocr = {}
    for name in OCR_CACHE_DIRS:
        directory = root / name
        items = sorted(directory.iterdir()) if directory.is_dir() else []
        ocr[name.replace("-", "_")] = _summary(items)

    materials = root / MATERIAL_DIR
    media = sorted(materials.glob(f"{MEDIA_PREFIX}*")) if materials.is_dir() else []
    live_media = []
    for path in media:
        material_id = path.name.removeprefix(MEDIA_PREFIX)
        try:
            populated = path.is_dir() and not path.is_symlink() and any(path.iterdir())
        except OSError:
            populated = False
        if material_id in live and populated:
            live_media.append(path)
    orphan_media = [path for path in media if path not in live_media]

    operations = sorted(root.glob("operation-*.json")) if root.is_dir() else []
    expired_operations = [
        path for path in operations if 0 < _operation_expiry(path) <= current
    ]

    material_files = [path for path in stale]
    live_material_files = [
        root / f"{material_id}{suffix}" for material_id in sorted(live)
        for suffix in (".json", ".expires")
    ]

    categories = {
        "materials": {
            **_summary(live_material_files),
            "expired_count": len(stale),
            "expired_bytes": sum(_size(path) for path in material_files),
        },
        "frames": {
            **_summary(live_frames),
            "orphan_count": len(orphan_frames),
            "orphan_bytes": sum(_size(path) for path in orphan_frames),
        },
        **ocr,
        "video_media": {
            **_summary(live_media),
            "orphan_count": len(orphan_media),
            "orphan_bytes": sum(_size(path) for path in orphan_media),
        },
        "operations": {
            **_summary(operations),
            "expired_count": len(expired_operations),
            "expired_bytes": sum(_size(path) for path in expired_operations),
        },
        "browser_cache": browser_cache_state(home=home),
    }
    others = _other_entries(home, root)
    # The browser profile already appears whole under ``other``; its cache
    # category is the reclaimable part of it, so counting both would lie.
    total = sum(
        category["bytes"]
        for key, category in categories.items()
        if key != "browser_cache"
    )
    total += sum(entry["bytes"] for entry in others)
    return {
        "materials_root": str(root),
        "envoy_home": str(home),
        "total_bytes": total,
        "categories": categories,
        "other": others,
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
    browser = clear_browser_cache(home=home, dry_run=dry_run) if home is not None else None
    removed: list[dict] = []
    if browser and not browser["skipped_in_use"]:
        removed.extend(browser["removed"])
    if not root.is_dir():
        return {
            "materials_root": str(root),
            "dry_run": dry_run,
            "removed": removed,
            "freed_bytes": sum(entry["bytes"] for entry in removed),
            "browser_cache": browser,
        }

    live, stale = _material_state(root, current)
    for path in stale:
        removed.append(_remove(path, root, dry_run))

    for frame in sorted(root.glob(f"{FRAMES_PREFIX}*")):
        if frame.is_symlink():
            removed.append(_remove(frame, root, dry_run))
        elif frame.name.removeprefix(FRAMES_PREFIX) not in live:
            removed.append(_remove(frame, root, dry_run))

    materials = root / MATERIAL_DIR
    if materials.is_dir():
        for media in sorted(materials.glob(f"{MEDIA_PREFIX}*")):
            material_id = media.name.removeprefix(MEDIA_PREFIX)
            try:
                populated = media.is_dir() and not media.is_symlink() and any(media.iterdir())
            except OSError:
                populated = False
            if material_id not in live or not populated:
                removed.append(_remove(media, root, dry_run))

    for name in OCR_CACHE_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir()):
            removed.append(_remove(item, root, dry_run))

    for operation in sorted(root.glob("operation-*.json")):
        if 0 < _operation_expiry(operation) <= current:
            removed.append(_remove(operation, root, dry_run))
            removed.append(_remove(operation.with_suffix(".lock"), root, dry_run))

    return {
        "materials_root": str(root),
        "dry_run": dry_run,
        "removed": removed,
        "freed_bytes": sum(entry["bytes"] for entry in removed),
        "browser_cache": browser,
    }


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def render_status(report: dict) -> str:
    lines = [f"materials root: {report['materials_root']}"]
    labels = {
        "materials": "materials",
        "frames": "video frames",
        "ocr_inputs": "ocr downloads",
        "ocr_cache": "ocr results",
        "video_media": "raw video media",
        "operations": "operation ledgers",
        "browser_cache": "browser cache",
    }
    for key, label in labels.items():
        category = report["categories"][key]
        note = ""
        if category.get("expired_count"):
            note = f"  ({category['expired_count']} expired, {human_bytes(category['expired_bytes'])})"
        elif category.get("orphan_count"):
            note = f"  ({category['orphan_count']} orphaned, {human_bytes(category['orphan_bytes'])})"
        elif key == "browser_cache":
            note = "  (in use)" if category.get("in_use") else "  (reclaimable)"
        lines.append(
            f"  {label:<18} {category['count']:>4} items  {human_bytes(category['bytes']):>9}{note}"
        )
    for entry in report["other"]:
        lines.append(f"  {entry['name']:<18} {'':>10}  {human_bytes(entry['bytes']):>9}")
    lines.append(f"  {'total':<18} {'':>10}  {human_bytes(report['total_bytes']):>9}")
    return "\n".join(lines)


def render_prune(report: dict) -> str:
    if report["dry_run"]:
        return (
            f"would remove {len(report['removed'])} entries "
            f"({human_bytes(report['freed_bytes'])})"
        )
    return (
        f"removed {len(report['removed'])} entries "
        f"({human_bytes(report['freed_bytes'])} freed)"
    )
