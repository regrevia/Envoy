#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from probe_media_file import probe_media_file


SCHEMA_VERSION = "envoy.media.collection_probe.v1"
MEDIA_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".webm",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
    ".sz",
}


def _media_files(root: Path):
    return sorted(
        [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS],
        key=lambda path: str(path),
    )


def _summary_item(probe: dict):
    return {
        "path": probe.get("path"),
        "duration_seconds": probe.get("duration_seconds"),
        "format_name": probe.get("format_name"),
        "reason": probe.get("reason"),
        "failed_checks": probe.get("failed_checks", []),
        "can_extract_text": bool(probe.get("can_extract_text")),
    }


def probe_media_collection(root: Path):
    if not root.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "missing_directory",
            "root": str(root),
        }

    files = _media_files(root)
    probes = [probe_media_file(path) for path in files]
    usable = [_summary_item(probe) for probe in probes if probe.get("status") == "ok"]
    rejected = [_summary_item(probe) for probe in probes if probe.get("status") != "ok"]
    total_duration = sum(float(item.get("duration_seconds") or 0.0) for item in usable)
    status = "ok" if usable and not rejected else "partial" if usable else "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "root": str(root),
        "summary": {
            "total_files": len(files),
            "usable_files": len(usable),
            "rejected_files": len(rejected),
            "usable_duration_seconds": round(total_duration, 3),
        },
        "recommended_next_video": usable[0]["path"] if usable else None,
        "usable_inputs": usable,
        "rejected_inputs": rejected,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Probe a media directory before long-video delivery ingestion.")
    parser.add_argument("root", help="Directory containing media files.")
    args = parser.parse_args(argv)
    result = probe_media_collection(Path(args.root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
