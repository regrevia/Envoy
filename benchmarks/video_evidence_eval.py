#!/usr/bin/env python3
"""Repeatable lexical/time-location evaluation for retained video subtitles.

This offline harness measures Envoy retrieval, not host model answer quality.
Actual host token counts are included only when supplied in a usage JSON file.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source_dir in (
    ROOT / "packages" / "envoy-core" / "src",
    ROOT / "packages" / "envoy-cli" / "src",
):
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

from envoy_cli import reader
from envoy_core.schemas.read_v1 import MaterialSearchRequest


CASES = (
    {
        "id": "dodge-cue",
        "query": "When should I dodge?",
        "expected": "The boss raises its left arm before the strike; dodge after the red flash.",
    },
    {
        "id": "phase-change",
        "query": "What is the tell for the second phase?",
        "expected": "The second phase begins when the arena lights turn blue.",
    },
)

CAPTIONS = (
    (2.0, 5.0, "The first phase starts with a slow horizontal sweep."),
    (8.0, 11.0, "The boss raises its left arm before the strike; dodge after the red flash."),
    (18.0, 21.0, "After the sweep, wait for the recovery animation before attacking."),
    (42.0, 45.0, "The second phase begins when the arena lights turn blue."),
    (49.0, 53.0, "Keep distance until the blue rings disappear from the floor."),
)


def _build_fixture_text() -> tuple[str, list[dict]]:
    parts = []
    spans = []
    cursor = 0
    for start_seconds, end_seconds, caption in CAPTIONS:
        line = f"[{reader._format_video_time(start_seconds)}] {caption}"
        start_offset = cursor
        cursor += len(line)
        parts.append(line)
        spans.append({
            "start_offset": start_offset,
            "end_offset": cursor,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
        })
        cursor += 2
    return "\n\n".join(parts), spans


def _load_host_usage(path: Path | None) -> dict:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Host usage JSON must be an object keyed by benchmark case id")
    normalized = {}
    for case in CASES:
        item = value.get(case["id"])
        if not isinstance(item, dict):
            continue
        input_tokens = item.get("input_tokens")
        output_tokens = item.get("output_tokens")
        if (
            not isinstance(input_tokens, int) or input_tokens < 0
            or not isinstance(output_tokens, int) or output_tokens < 0
        ):
            continue
        normalized[case["id"]] = {
            "provider": str(item.get("provider") or ""),
            "model": str(item.get("model") or ""),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    return normalized


def run_benchmark(materials_dir: Path, host_usage_file: Path | None = None) -> dict:
    materials_dir.mkdir(parents=True, exist_ok=True)
    text, spans = _build_fixture_text()
    source_url = "https://video.example.invalid/public-demo"
    source_revision = reader.hashlib.sha256(text.encode("utf-8")).hexdigest()
    material_id = reader._material_id(source_url, source_revision, "envoy-video-eval")
    original_data_dir = reader.os.environ.get("ENVOY_LOCAL_DATA_DIR")
    reader.os.environ["ENVOY_LOCAL_DATA_DIR"] = str(materials_dir)
    try:
        reader._store_material(material_id, {
            "source_kind": "video",
            "source_url": source_url,
            "source_revision": source_revision,
            "session_id": "envoy-video-eval",
            "text": text,
            "title_text": "Fixed video evidence benchmark",
            "video_spans": spans,
            "budget_state": {
                "max_total_response_chars": 12_000,
                "max_total_response_tokens": 3_000,
                "max_expansion_rounds": 3,
                "returned_chars": 0,
                "returned_tokens": 0,
                "expansion_rounds": 0,
                "returned_ranges": [],
            },
        })
        results = []
        for case in CASES:
            started = time.perf_counter()
            response = reader.search_material(MaterialSearchRequest.model_validate({
                "request_id": f"eval-{case['id']}",
                "material_id": material_id,
                "session_id": "envoy-video-eval",
                "query": case["query"],
                "max_results": 3,
                "max_chars": 1500,
            }))
            elapsed_ms = (time.perf_counter() - started) * 1000
            content = "\n".join(block.content.text for block in response.blocks)
            located_blocks = [
                {
                    "text": block.content.text,
                    "locator_type": block.locator.type if block.locator else None,
                    "start_seconds": getattr(block.locator, "start_seconds", None),
                    "end_seconds": getattr(block.locator, "end_seconds", None),
                }
                for block in response.blocks
            ]
            expected_locator_found = any(
                case["expected"] in item["text"] and item["locator_type"] == "video_time"
                for item in located_blocks
            )
            details = response.coverage.modalities.get("text")
            details = details.details if details else {}
            results.append({
                "case_id": case["id"],
                "status": response.result.value,
                "expected_phrase_found": case["expected"] in content,
                "expected_phrase_has_video_locator": expected_locator_found,
                "returned_chars": len(content),
                "estimated_returned_tokens": int(details.get("returned_tokens") or 0),
                "locators": [
                    {key: value for key, value in item.items() if key != "text"}
                    for item in located_blocks
                ],
                "tool_calls": 1,
                "latency_ms": round(elapsed_ms, 3),
            })
        usage = _load_host_usage(host_usage_file)
        observed_cases = [usage[item["case_id"]] for item in results if item["case_id"] in usage]
        baseline_tokens = reader._estimated_tokens("Fixed video evidence benchmark\n" + text)
        return {
            "schema_version": "envoy.video-evidence-eval/1",
            "benchmark_scope": "offline retrieval only; no answer model is invoked",
            "host_usage_status": "actual" if len(observed_cases) == len(CASES) else "unknown",
            "host_usage_cases_observed": len(observed_cases),
            "host_usage_cases_expected": len(CASES),
            "host_actual_input_tokens": (
                sum(item["input_tokens"] for item in observed_cases)
                if len(observed_cases) == len(CASES) else None
            ),
            "host_actual_output_tokens": (
                sum(item["output_tokens"] for item in observed_cases)
                if len(observed_cases) == len(CASES) else None
            ),
            "host_usage_provider_models": sorted({
                f"{item['provider']}/{item['model']}" for item in observed_cases
            }),
            "tool_calls": sum(item["tool_calls"] for item in results),
            "coverage_rate": sum(item["expected_phrase_has_video_locator"] for item in results) / len(results),
            "returned_chars": sum(item["returned_chars"] for item in results),
            "estimated_returned_tokens": sum(item["estimated_returned_tokens"] for item in results),
            "full_scan_baseline_chars": len(text) * len(CASES),
            "full_scan_baseline_estimated_tokens": baseline_tokens * len(CASES),
            "latency_ms_median": round(statistics.median(item["latency_ms"] for item in results), 3),
            "cases": results,
        }
    finally:
        if original_data_dir is None:
            reader.os.environ.pop("ENVOY_LOCAL_DATA_DIR", None)
        else:
            reader.os.environ["ENVOY_LOCAL_DATA_DIR"] = original_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials-dir", type=Path, default=Path.home() / ".envoy" / "eval-materials")
    parser.add_argument("--host-usage-file", type=Path)
    args = parser.parse_args()
    try:
        report = run_benchmark(args.materials_dir, args.host_usage_file)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
