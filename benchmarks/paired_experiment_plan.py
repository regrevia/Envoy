#!/usr/bin/env python3
"""Create a deterministic, isolated A/B execution plan for agent packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "envoy.paired-experiment-plan/2"
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_SOURCE_CLASSES = {
    "bilibili",
    "douyin",
    "xiaohongshu",
    "wechat",
    "cross_platform",
    "robustness",
    "qasper",
}


def _safe_component(value: str, field: str) -> str:
    normalized = _SAFE_ID.sub("-", value.strip()).strip("-")
    if not normalized:
        raise ValueError(f"{field} must contain a usable identifier")
    return normalized[:80]


def _load_packages(path: Path) -> list[dict[str, Any]]:
    packages = []
    seen_packages: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {line_number}: invalid JSON ({exc.msg})") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Line {line_number}: package must be a JSON object")
        package_id = item.get("package_id")
        source_ids = item.get("source_ids")
        if not isinstance(package_id, str) or not package_id.strip():
            raise ValueError(f"Line {line_number}: package_id must be a non-empty string")
        package_id = _safe_component(package_id, "package_id")
        if package_id in seen_packages:
            raise ValueError(f"Line {line_number}: duplicate package_id {package_id!r}")
        if not isinstance(source_ids, list) or any(
            not isinstance(source, str) or not source.strip()
            for source in source_ids
        ):
            raise ValueError(f"Line {line_number}: source_ids must be a list of non-empty strings")
        normalized_sources = [_safe_component(source, "source_id") for source in source_ids]
        if len(normalized_sources) < 2:
            raise ValueError(f"Line {line_number}: each package needs at least two source_ids")
        if len(normalized_sources) != len(set(normalized_sources)):
            raise ValueError(f"Line {line_number}: duplicate source_id within package {package_id!r}")
        source_class = item.get("source_class", "unclassified")
        if source_class not in _SOURCE_CLASSES | {"unclassified"}:
            raise ValueError("source_class must be one of the supported benchmark strata")
        task_manifest_id = item.get("task_manifest_id", f"manifest-{package_id}")
        if not isinstance(task_manifest_id, str) or not task_manifest_id.strip():
            raise ValueError("task_manifest_id must be a non-empty string")
        seen_packages.add(package_id)
        packages.append({
            "package_id": package_id,
            "source_ids": normalized_sources,
            "source_class": source_class,
            "task_manifest_id": _safe_component(task_manifest_id, "task_manifest_id"),
        })
    if not packages:
        raise ValueError("packages input contains no packages")
    return packages


def build_plan(
    packages: list[dict[str, Any]],
    seed: int,
    cache_isolation_status: str = "unknown",
) -> dict[str, Any]:
    if cache_isolation_status not in {
        "verified_disabled",
        "verified_isolated",
        "cache_confounded",
        "unknown",
    }:
        raise ValueError("unsupported cache_isolation_status")
    rng = random.Random(seed)
    manifest_bytes = json.dumps(
        packages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    package_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    run_id = f"envoy-ab-{seed}-{package_manifest_sha256[:12]}"
    ordered = list(packages)
    rng.shuffle(ordered)
    first_arms = ["baseline"] * ((len(ordered) + 1) // 2)
    first_arms.extend(["envoy"] * (len(ordered) // 2))
    rng.shuffle(first_arms)
    planned = []
    for index, (package, first_arm) in enumerate(zip(ordered, first_arms), start=1):
        batch_component = f"pkg-{index:04d}-{package['package_id']}"
        profiles = {
            "baseline": f"profiles/{batch_component}/baseline",
            "envoy": f"profiles/{batch_component}/envoy",
        }
        planned.append({
            "sequence": index,
            "package_id": package["package_id"],
            "source_ids": package["source_ids"],
            "source_class": package["source_class"],
            "task_manifest_id": package["task_manifest_id"],
            "first_arm": first_arm,
            "arms": [first_arm, "envoy" if first_arm == "baseline" else "baseline"],
            "profiles": profiles,
            "session_ids": {
                arm: f"{run_id}-{batch_component}-{arm}-session"
                for arm in ("baseline", "envoy")
            },
            "turn_correlation_ids": {
                arm: {
                    role: f"{run_id}-{batch_component}-{arm}-{role}"
                    for role in ("initial", "followup-1", "followup-2")
                }
                for arm in ("baseline", "envoy")
            },
            "same_package_session": True,
            "reset_between_packages": True,
        })
    a_first_count = sum(item["first_arm"] == "baseline" for item in planned)
    unique_sources = {
        source_id
        for package in packages
        for source_id in package["source_ids"]
    }
    source_class_counts: dict[str, int] = {}
    for package in packages:
        source_class = str(package.get("source_class") or "unclassified")
        source_class_counts[source_class] = source_class_counts.get(source_class, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "seed": seed,
        "package_count": len(planned),
        "task_count": len(planned) * 3,
        "unique_source_count": len(unique_sources),
        "source_class_package_counts": dict(sorted(source_class_counts.items())),
        "package_manifest_sha256": package_manifest_sha256,
        "cache_isolation_status": cache_isolation_status,
        "confirmatory_minimum_met": (
            len(planned) >= 20
            and len(planned) * 3 >= 40
            and len(unique_sources) >= 20
        ),
        "a_first_count": a_first_count,
        "b_first_count": len(planned) - a_first_count,
        "cache_policy": "caller must record disabled_or_isolated; otherwise cache-confounded",
        "packages": planned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--cache-isolation-status",
        choices=("verified_disabled", "verified_isolated", "cache_confounded", "unknown"),
        default="unknown",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(
            _load_packages(args.packages),
            args.seed,
            cache_isolation_status=args.cache_isolation_status,
        )
        serialized = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
    except (OSError, ValueError) as exc:
        print(f"envoy paired experiment plan: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
