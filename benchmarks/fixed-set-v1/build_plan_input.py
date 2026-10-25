#!/usr/bin/env python3
"""Build the packages JSONL that paired_experiment_plan.py consumes.

Reads the frozen manifest plus the package drafts and emits one JSON object per
package with the source/task manifest digests the confirmatory plan requires.

Usage:
    python3 build_plan_input.py --manifest manifest.json --drafts task-drafts --out packages.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--drafts", type=Path, default=ROOT / "task-drafts")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not manifest.get("manifest_sha256"):
        parser.error("manifest.json is not frozen (no manifest_sha256)")
    drafts = {}
    for path in sorted(args.drafts.glob("*.json")):
        draft = json.loads(path.read_text(encoding="utf-8"))
        drafts[draft["package_id"]] = draft

    rows = []
    seen_sources: set[str] = set()
    for package in manifest["packages"]:
        package_id = package["package_id"]
        draft = drafts.get(package_id)
        if draft is None:
            parser.error(f"{package_id}: manifest lists a package with no draft")
        source_ids = [f"{package_id}-{source_id}" for source_id in draft["source_ids"]]
        for source_id in source_ids:
            if source_id in seen_sources:
                parser.error(f"duplicate source id across packages: {source_id}")
            seen_sources.add(source_id)
        task_payload = {
            "deliverable": draft["deliverable"],
            "rubric_version": draft["rubric_version"],
            "turns": draft["turns"],
            "reference_facts": draft["reference_facts"],
        }
        rows.append(
            {
                "package_id": package_id,
                "source_class": package["stratum"],
                "source_ids": source_ids,
                "task_manifest_id": f"manifest-{package_id}",
                "source_manifest_sha256": digest(draft["source_revisions"]),
                "task_manifest_sha256": digest(task_payload),
            }
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} package(s), {len(seen_sources)} unique source(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
