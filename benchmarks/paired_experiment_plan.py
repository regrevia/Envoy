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
_CACHE_ISOLATION_STATUSES = {
    "verified_disabled",
    "verified_isolated",
    "per_request_salt",
    "cache_confounded",
    "unknown",
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
        source_manifest_sha256 = item.get("source_manifest_sha256")
        task_manifest_sha256 = item.get("task_manifest_sha256")
        for field, digest in (
            ("source_manifest_sha256", source_manifest_sha256),
            ("task_manifest_sha256", task_manifest_sha256),
        ):
            if digest is not None and (
                not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            ):
                raise ValueError(f"Line {line_number}: {field} must be a SHA-256 hex digest")
        seen_packages.add(package_id)
        package_record = {
            "package_id": package_id,
            "source_ids": normalized_sources,
            "source_class": source_class,
            "task_manifest_id": _safe_component(task_manifest_id, "task_manifest_id"),
        }
        if source_manifest_sha256 is not None:
            package_record["source_manifest_sha256"] = source_manifest_sha256.lower()
        if task_manifest_sha256 is not None:
            package_record["task_manifest_sha256"] = task_manifest_sha256.lower()
        packages.append(package_record)
    if not packages:
        raise ValueError("packages input contains no packages")
    return packages


def build_plan(
    packages: list[dict[str, Any]],
    seed: int,
    cache_isolation_status: str = "unknown",
    confirmatory_fixed_20: bool = False,
) -> dict[str, Any]:
    if cache_isolation_status not in _CACHE_ISOLATION_STATUSES:
        raise ValueError("unsupported cache_isolation_status")
    if confirmatory_fixed_20:
        required_strata = {
            "bilibili",
            "douyin",
            "xiaohongshu",
            "wechat",
            "cross_platform",
        }
        if len(packages) != 20:
            raise ValueError("fixed confirmatory set requires exactly 20 packages")
        for package in packages:
            if not package.get("source_manifest_sha256") or not package.get("task_manifest_sha256"):
                raise ValueError(
                    f"{package['package_id']}: fixed confirmatory package requires source_manifest_sha256 and task_manifest_sha256"
                )
        counts: dict[str, int] = {}
        all_sources = []
        for package in packages:
            source_class = package.get("source_class", "unclassified")
            counts[source_class] = counts.get(source_class, 0) + 1
            all_sources.extend(package.get("source_ids", []))
        if set(counts) != required_strata or any(counts.get(stratum) != 4 for stratum in required_strata):
            raise ValueError("fixed confirmatory set requires four packages in each approved stratum")
        if len(all_sources) < 40 or len(set(all_sources)) != len(all_sources):
            raise ValueError("fixed confirmatory set requires at least 40 unique source IDs")
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
            "source_manifest_sha256": package.get("source_manifest_sha256"),
            "task_manifest_sha256": package.get("task_manifest_sha256"),
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
        "cache_isolation_status": (
            "per_request_salt"
            if confirmatory_fixed_20 and cache_isolation_status == "unknown"
            else cache_isolation_status
        ),
        "confirmatory_minimum_met": (
            len(planned) >= 20
            and len(planned) * 3 >= 40
            and len(unique_sources) >= 20
        ),
        "a_first_count": a_first_count,
        "b_first_count": len(planned) - a_first_count,
        "cache_policy": (
            "per_request_salt; verify from completed authoritative request ledger"
            if cache_isolation_status == "per_request_salt"
            else "caller must record disabled_or_isolated; otherwise cache-confounded"
        ),
        "evaluation_profile": (
            "fixed_confirmatory_20"
            if confirmatory_fixed_20
            else "custom_or_pilot"
        ),
        "packages": planned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--cache-isolation-status",
        choices=tuple(sorted(_CACHE_ISOLATION_STATUSES - {"verified_per_request_salt"})),
        default="unknown",
    )
    parser.add_argument(
        "--confirmatory-fixed-20",
        action="store_true",
        help="require exactly 20 packages, four per approved stratum, and 40 unique sources",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(
            _load_packages(args.packages),
            args.seed,
            cache_isolation_status=args.cache_isolation_status,
            confirmatory_fixed_20=args.confirmatory_fixed_20,
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
