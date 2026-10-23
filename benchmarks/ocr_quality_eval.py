#!/usr/bin/env python3
"""Score manually reviewed OCR outputs without emitting source text."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _edit_distance(reference: list[str], recognized: list[str]) -> int:
    previous = list(range(len(recognized) + 1))
    for ref_index, ref_item in enumerate(reference, start=1):
        current = [ref_index]
        for got_index, got_item in enumerate(recognized, start=1):
            current.append(min(
                current[-1] + 1,
                previous[got_index] + 1,
                previous[got_index - 1] + (ref_item != got_item),
            ))
        previous = current
    return previous[-1]


def _sample_metrics(record: dict[str, Any]) -> dict[str, Any]:
    reference = _normalize(record["reference_text"])
    recognized = _normalize(record["recognized_text"])
    reference_chars = list(reference)
    recognized_chars = list(recognized)
    char_edits = _edit_distance(reference_chars, recognized_chars)
    cjk_sample = bool(_CJK.search(reference))
    reference_words = reference.split()
    recognized_words = recognized.split()
    word_edits = _edit_distance(reference_words, recognized_words)
    return {
        "sample_id": record["sample_id"],
        "source_id": record["source_id"],
        "source_sha256": record["source_sha256"],
        "backend": record["backend"],
        "reference_characters": len(reference_chars),
        "recognized_characters": len(recognized_chars),
        "character_edits": char_edits,
        "cer": char_edits / len(reference_chars),
        "wer": (
            None
            if cjk_sample or not reference_words
            else word_edits / len(reference_words)
        ),
    }


def _validate_record(value: Any, line_number: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"Line {line_number}: record must be a JSON object")
    required = ("sample_id", "source_id", "backend", "source_sha256", "reference_text", "recognized_text")
    record = {}
    for field in required:
        item = value.get(field)
        if not isinstance(item, str):
            raise ValueError(f"Line {line_number}: {field} must be a string")
        record[field] = item
    for field in ("sample_id", "source_id", "backend"):
        record[field] = record[field].strip()
        if not record[field]:
            raise ValueError(f"Line {line_number}: {field} must not be empty")
    record["source_sha256"] = record["source_sha256"].strip().lower()
    if not _SHA256.fullmatch(record["source_sha256"]):
        raise ValueError(f"Line {line_number}: source_sha256 must be 64 hexadecimal characters")
    record["reference_text"] = _normalize(record["reference_text"])
    record["recognized_text"] = _normalize(record["recognized_text"])
    if not record["reference_text"]:
        raise ValueError(f"Line {line_number}: reference_text must not be empty")
    return record


def _aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    total_reference_chars = sum(item["reference_characters"] for item in samples)
    total_char_edits = sum(item["character_edits"] for item in samples)
    wer_samples = [item["wer"] for item in samples if item["wer"] is not None]
    return {
        "sample_count": len(samples),
        "micro_cer": total_char_edits / total_reference_chars if total_reference_chars else None,
        "macro_cer": statistics.mean(item["cer"] for item in samples) if samples else None,
        "macro_wer": statistics.mean(wer_samples) if wer_samples else None,
        "wer_sample_count": len(wer_samples),
    }


def evaluate(path: Path) -> dict[str, Any]:
    records = []
    seen_ids = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {line_number}: invalid JSON ({exc.msg})") from exc
        record = _validate_record(value, line_number)
        if record["sample_id"] in seen_ids:
            raise ValueError(f"Line {line_number}: Duplicate sample_id {record['sample_id']!r}")
        seen_ids.add(record["sample_id"])
        records.append(_sample_metrics(record))
    if not records:
        raise ValueError("OCR evaluation input contains no samples")

    by_backend: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in records:
        by_backend[sample["backend"]].append(sample)
    return {
        "schema_version": "envoy.ocr-quality-eval/1",
        "metric_notes": {
            "normalization": "Unicode NFC; consecutive whitespace collapsed",
            "cer": "Levenshtein character edits divided by reference code points",
            "wer": "Whitespace-token WER; null for references containing CJK characters",
            "report_privacy": "Reference and recognized text are never copied to the report",
        },
        **_aggregate(records),
        "backend_summary": {
            backend: _aggregate(samples)
            for backend, samples in sorted(by_backend.items())
        },
        "samples": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, type=Path, help="UTF-8 JSONL with reference and OCR text")
    parser.add_argument("--output", type=Path, help="Optional report path; stdout is used by default")
    args = parser.parse_args()
    try:
        report = evaluate(args.records)
        serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
    except (OSError, ValueError) as exc:
        print(f"envoy OCR evaluation: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
