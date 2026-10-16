#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_DIRECTORIES = ("readable", "knowledge_base", "training", "agent", "indexes")
REQUIRED_FILES = (
    "README.md",
    "delivery_manifest.json",
    "readable/index.md",
    "knowledge_base/rag_corpus.jsonl",
    "training/instruction_train.jsonl",
    "agent/import_manifest.json",
    "indexes/chunk_index.json",
    "indexes/source_index.json",
    "indexes/timeline_index.json",
    "indexes/quality_report.json",
)
REQUIRED_COMPATIBLE_TARGETS = (
    "generic_rag_jsonl",
    "markdown_knowledge_base",
    "agent_skill_context",
    "instruction_tuning_seed",
)


def _load_json(path: Path, errors: list[str]):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing json file: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid json file: {path}: {exc}")
    return {}


def _count_jsonl(path: Path, errors: list[str]):
    if not path.is_file():
        errors.append(f"missing jsonl file: {path}")
        return 0
    count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid jsonl row: {path}:{line_number}: {exc}")
            continue
        count += 1
    return count


def validate_delivery_package(root: Path):
    errors: list[str] = []
    root = root.resolve()
    if not root.is_dir():
        return {"status": "failed", "errors": [f"package root is not a directory: {root}"]}

    for relative_dir in REQUIRED_DIRECTORIES:
        if not (root / relative_dir).is_dir():
            errors.append(f"missing directory: {relative_dir}")
    for relative_file in REQUIRED_FILES:
        if not (root / relative_file).is_file():
            errors.append(f"missing file: {relative_file}")

    manifest = _load_json(root / "delivery_manifest.json", errors)
    import_manifest = _load_json(root / "agent" / "import_manifest.json", errors)
    chunk_index = _load_json(root / "indexes" / "chunk_index.json", errors)
    source_index = _load_json(root / "indexes" / "source_index.json", errors)
    quality_report = _load_json(root / "indexes" / "quality_report.json", errors)

    if manifest.get("schema_version") != "envoy.delivery.v1":
        errors.append("delivery_manifest.json schema_version must be envoy.delivery.v1")
    if import_manifest.get("schema_version") != "envoy.agent.import.v1":
        errors.append("agent/import_manifest.json schema_version must be envoy.agent.import.v1")

    compatible_targets = import_manifest.get("compatible_targets")
    if compatible_targets != list(REQUIRED_COMPATIBLE_TARGETS):
        errors.append("agent/import_manifest.json compatible_targets is incomplete or out of order")

    target_profiles = import_manifest.get("target_profiles")
    if not isinstance(target_profiles, dict):
        errors.append("agent/import_manifest.json target_profiles must be an object")
        target_profiles = {}
    for target in REQUIRED_COMPATIBLE_TARGETS:
        profile = target_profiles.get(target)
        if not isinstance(profile, dict):
            errors.append(f"missing target profile: {target}")
            continue
        primary_file = profile.get("primary_file")
        if isinstance(primary_file, str) and primary_file and not (root / primary_file).is_file():
            errors.append(f"target profile primary_file does not exist: {target}: {primary_file}")
        for key in ("metadata_index", "quality_index", "source_index", "chunk_index", "dataset_card"):
            relative_path = profile.get(key)
            if isinstance(relative_path, str) and relative_path and not (root / relative_path).is_file():
                errors.append(f"target profile {key} does not exist: {target}: {relative_path}")
        supporting_files = profile.get("supporting_files")
        if isinstance(supporting_files, list):
            for relative_path in supporting_files:
                if isinstance(relative_path, str) and relative_path and not (root / relative_path).is_file():
                    errors.append(f"target profile supporting file does not exist: {target}: {relative_path}")

    import_entries = import_manifest.get("recommended_import_order")
    if not isinstance(import_entries, list) or not import_entries:
        errors.append("agent/import_manifest.json recommended_import_order must be a non-empty list")
    else:
        for entry in import_entries:
            if not isinstance(entry, dict):
                errors.append("recommended_import_order entries must be objects")
                continue
            relative_path = entry.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                errors.append("recommended_import_order entry missing path")
                continue
            if not (root / relative_path).is_file():
                errors.append(f"recommended import path does not exist: {relative_path}")

    chunk_count = int(chunk_index.get("chunk_count") or 0)
    chunks = chunk_index.get("chunks")
    if not isinstance(chunks, list):
        errors.append("indexes/chunk_index.json chunks must be a list")
        chunks = []
    if chunk_count != len(chunks):
        errors.append("indexes/chunk_index.json chunk_count does not match chunks length")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            errors.append("chunk index entry must be an object")
            continue
        for key in ("chunk_id", "title", "source_name", "reference_text", "path"):
            if not chunk.get(key):
                errors.append(f"chunk index entry missing {key}")
        relative_path = chunk.get("path")
        if isinstance(relative_path, str) and not (root / relative_path).is_file():
            errors.append(f"chunk file does not exist: {relative_path}")

    source_count = int(source_index.get("source_count") or 0)
    sources = source_index.get("sources")
    if not isinstance(sources, list):
        errors.append("indexes/source_index.json sources must be a list")
        sources = []
    if source_count != len(sources):
        errors.append("indexes/source_index.json source_count does not match sources length")

    rag_row_count = _count_jsonl(root / "knowledge_base" / "rag_corpus.jsonl", errors)
    training_row_count = _count_jsonl(root / "training" / "instruction_train.jsonl", errors)
    partial_chunk_count = int(quality_report.get("partial_chunk_count") or 0)

    payload = {
        "status": "failed" if errors else "ok",
        "root": str(root),
        "schema_version": manifest.get("schema_version"),
        "import_schema_version": import_manifest.get("schema_version"),
        "compatible_targets": compatible_targets if isinstance(compatible_targets, list) else [],
        "target_profile_count": len(target_profiles),
        "source_count": source_count,
        "chunk_count": chunk_count,
        "rag_row_count": rag_row_count,
        "training_row_count": training_row_count,
        "partial_chunk_count": partial_chunk_count,
        "errors": errors,
    }
    return payload


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Validate an Envoy delivery package directory.")
    parser.add_argument("package_root", help="Path to the generated delivery package root.")
    args = parser.parse_args(argv)
    result = validate_delivery_package(Path(args.package_root))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
