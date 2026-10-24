"""Report and reclaim the local storage Envoy keeps for materials and caches.

Materials expire on read (``reader._material_root`` drops anything past its
``.expires`` deadline and trims the cache to ``MAX_CACHED_MATERIALS``), but two
kinds of data never were reclaimed: the OCR download and result caches, and the
idempotency ledgers, both of which keep growing because nothing ever deletes
them. ``envoy cache status`` reports what is on disk and ``envoy cache prune``
deletes the reclaimable part; the isolated browser profile is reported but never
touched, because removing it forces a fresh Chromium download.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

MATERIAL_DIR = "materials"
FRAMES_PREFIX = "frames-"
OCR_CACHE_DIRS = ("ocr-cache", "ocr-inputs")

_MATERIAL_ID = re.compile(r"mat-[a-f0-9]{24}")


def data_root() -> Path:
    configured = os.getenv("ENVOY_LOCAL_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".envoy" / MATERIAL_DIR


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
        "operations": {
            **_summary(operations),
            "expired_count": len(expired_operations),
            "expired_bytes": sum(_size(path) for path in expired_operations),
        },
    }
    others = _other_entries(home, root)
    total = sum(category["bytes"] for category in categories.values())
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
) -> dict:
    """Delete expired materials, orphan frames, OCR caches and spent ledgers."""

    root = root or data_root()
    current = time.time() if now is None else now
    removed: list[dict] = []
    if not root.is_dir():
        return {
            "materials_root": str(root),
            "dry_run": dry_run,
            "removed": removed,
            "freed_bytes": 0,
        }

    live, stale = _material_state(root, current)
    for path in stale:
        removed.append(_remove(path, root, dry_run))

    for frame in sorted(root.glob(f"{FRAMES_PREFIX}*")):
        if frame.is_symlink():
            removed.append(_remove(frame, root, dry_run))
        elif frame.name.removeprefix(FRAMES_PREFIX) not in live:
            removed.append(_remove(frame, root, dry_run))

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
        "operations": "operation ledgers",
    }
    for key, label in labels.items():
        category = report["categories"][key]
        note = ""
        if category.get("expired_count"):
            note = f"  ({category['expired_count']} expired, {human_bytes(category['expired_bytes'])})"
        elif category.get("orphan_count"):
            note = f"  ({category['orphan_count']} orphaned, {human_bytes(category['orphan_bytes'])})"
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
