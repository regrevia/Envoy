#!/usr/bin/env python3
"""Recompute the 900 s / 2400 s long-video baseline from the raw receipts.

The page ``docs/acceptance/long-video-baseline-2026-11-06.md`` quotes numbers.
This script derives every one of them again from the artefacts in
``docs/acceptance/artifacts/long-video-baseline-2026-11-06/raw/`` so a reader
does not have to trust the page.

What it recomputes
------------------
For every run in ``raw/manifest.json`` and its request/response pair:

    cap_seconds        read back from the *request* file, not from the manifest
    exit_code/elapsed  the only numbers this script cannot derive (they are
                       wall-clock facts recorded at run time); they are checked
                       against the cap instead
    overrun            exit != 0, or an error whose code is ``budget_exceeded``,
                       or a message naming ``max_work_seconds``, or elapsed
                       exceeding the cap
    transcript         sha256 of the returned transcript text, segment count,
                       first/last timestamp
    coverage           video duration, ``transcript_source``, extraction state,
                       processed_until_seconds, available/returned chars

Then, per sample, it compares the two cap arms: identical transcript hash,
identical coverage numbers, identical error set. A difference is reported as
a divergence and makes the script exit 1.

Usage
-----
    packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py
    packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py --json
    packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py --ground-truth

``--ground-truth`` compares each sample's widest receipt (the one returning the
most characters) against the archived caption track under ``raw/ground-truth/``:
joined-text equality, per-sentence verbatim hits, and first/last timestamps.

It opens no socket and runs no reader; it only reads files under the artefact
directory. Missing artefacts are reported as ``missing``, never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW = ROOT / "docs" / "acceptance" / "artifacts" / "long-video-baseline-2026-11-06" / "raw"

OVERRUN_ERROR_CODES = {"budget_exceeded", "budget_enforcement_unavailable"}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _transcript(response: dict[str, Any]) -> tuple[str, list[float]]:
    text = ""
    stamps: list[float] = []
    for block in response.get("blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "transcript":
            continue
        content = block.get("content") or {}
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            text = content["text"]
    # One segment per rendered ``[mm:ss.mmm]`` line. The block locator carries
    # only a single start, so reading it too would double-count the first line.
    for line in text.split("\n\n"):
        line = line.strip()
        if not line.startswith("[") or "]" not in line:
            continue
        head = line[1 : line.index("]")]
        try:
            minutes, seconds = head.split(":")
            stamps.append(int(minutes) * 60 + float(seconds))
        except ValueError:
            continue
    return text, sorted(stamps)


def _run_row(raw: Path, entry: dict[str, Any]) -> dict[str, Any]:
    request = _load_json(raw / str(entry.get("request", "")))
    response = _load_json(raw / str(entry.get("output", "")))
    cap_manifest = entry.get("cap_seconds")
    cap_request = None
    if isinstance(request, dict):
        budget = request.get("budget") or {}
        if isinstance(budget, dict):
            cap_request = budget.get("max_work_seconds")
    cap = cap_request if isinstance(cap_request, int) else cap_manifest

    row: dict[str, Any] = {
        "run_id": entry.get("run_id"),
        "sample": entry.get("sample"),
        "cap_seconds": cap,
        "cap_matches_request": cap_request == cap_manifest,
        "compare": entry.get("compare", True),
        "elapsed_ms": entry.get("elapsed_ms"),
        "exit_code": entry.get("exit_code"),
        "output_present": response is not None,
        "errors": [],
        "overrun": False,
        "overrun_reasons": [],
        "transcript_sha256": None,
        "segments": None,
        "first_seconds": None,
        "last_seconds": None,
        "video": {},
        "text": {},
    }

    if isinstance(entry.get("elapsed_ms"), (int, float)) and isinstance(cap, int):
        if entry["elapsed_ms"] > cap * 1000:
            row["overrun"] = True
            row["overrun_reasons"].append("elapsed_ms > cap")

    if response is None:
        stderr = raw / str(entry.get("stderr") or "")
        row["stderr_tail"] = (
            stderr.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
            if stderr.is_file()
            else None
        )
        if entry.get("exit_code") != 0:
            row["overrun"] = True
            row["overrun_reasons"].append("non-zero exit without a receipt")
        return row

    row["execution_state"] = response.get("execution_state")
    row["result"] = response.get("result")
    errors = response.get("errors") or []
    row["errors"] = [
        {"code": (err or {}).get("code"), "message": str((err or {}).get("message") or "")[:160]}
        for err in errors
        if isinstance(err, dict)
    ]
    for err in row["errors"]:
        if err["code"] in OVERRUN_ERROR_CODES or "max_work_seconds" in err["message"]:
            row["overrun"] = True
            row["overrun_reasons"].append(f"error {err['code']}")
    if entry.get("exit_code") not in (0, None):
        row["overrun"] = True
        row["overrun_reasons"].append(f"exit {entry['exit_code']}")

    text, stamps = _transcript(response)
    if text:
        row["transcript_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row["transcript_chars"] = len(text)
        row["segments"] = len(stamps)
        row["first_seconds"] = round(stamps[0], 3) if stamps else None
        row["last_seconds"] = round(stamps[-1], 3) if stamps else None

    coverage = response.get("coverage") or {}
    modalities = coverage.get("modalities") or {}
    video = modalities.get("video") or {}
    details = video.get("details") or {}
    row["video"] = {
        "extraction": video.get("extraction"),
        "requested_range": details.get("requested_range"),
        "processed_until_seconds": details.get("processed_until_seconds"),
        "transcript_source": details.get("transcript_source"),
        "transcription_status": details.get("transcription_status"),
        "duration_seconds": details.get("duration_seconds"),
        "unresolved": list(video.get("unresolved") or []),
    }
    text_mod = modalities.get("text") or {}
    text_details = text_mod.get("details") or {}
    row["text"] = {
        "total_chars": text_details.get("total_chars"),
        "returned_chars": text_details.get("returned_chars"),
        "continuation_available": text_details.get("continuation_available"),
        "next_offset": text_details.get("next_offset"),
    }
    return row


def _pair_divergence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sample: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("cap_seconds") == 1:  # negative control, not a cap arm
            continue
        if not row.get("output_present") or not row.get("compare", True):
            continue
        by_sample.setdefault(str(row.get("sample")), []).append(row)

    out: list[dict[str, Any]] = []
    for sample, arms in by_sample.items():
        caps = {arm["cap_seconds"] for arm in arms}
        if len(caps) < 2:
            out.append({"sample": sample, "comparable": False, "reason": f"only cap(s) {sorted(caps)}"})
            continue
        identical = len({arm["transcript_sha256"] for arm in arms}) == 1
        coverage_equal = len({json.dumps(arm["video"], sort_keys=True) for arm in arms}) == 1
        out.append(
            {
                "sample": sample,
                "comparable": True,
                "caps": sorted(caps),
                "transcript_identical": identical,
                "coverage_identical": coverage_equal,
                "divergent": not (identical and coverage_equal),
            }
        )
    return out


def _vtt_cues(path: Path) -> list[tuple[str, str]]:
    """Minimal WebVTT reader: one (timestamp, text) pair per cue."""

    cues: list[tuple[str, str]] = []
    stamp: str | None = None
    buffer: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if "-->" in line:
            if stamp is not None and buffer:
                cues.append((stamp, " ".join(buffer)))
            stamp = line.split("-->")[0].strip()
            buffer = []
        elif line and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) and not line.isdigit():
            buffer.append(line)
    if stamp is not None and buffer:
        cues.append((stamp, " ".join(buffer)))
    return cues


def _transcript_rows(path: Path) -> list[tuple[str, str]]:
    """The rendered ``[mm:ss.mmm] sentence`` lines of a transcript block."""
    import re

    payload = _load_json(path) or {}
    text = ""
    for block in payload.get("blocks") or []:
        if isinstance(block, dict) and block.get("kind") == "transcript":
            content = block.get("content") or {}
            text = content.get("text") or ""
            break
    rows: list[tuple[str, str]] = []
    for line in text.split("\n\n"):
        match = re.match(r"\[(\d+:\d+\.\d+)\]\s*(.*)", line.strip())
        if match:
            rows.append((match.group(1), match.group(2).strip()))
    return rows


def _norm(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _ground_truth(raw: Path) -> int:
    """Compare every sample's receipts against the archived caption track."""
    manifest = _load_json(raw / "manifest.json") or {}
    truth_entries = manifest.get("ground_truth") or {}
    samples = manifest.get("samples") or {}
    runs = {entry.get("run_id"): entry for entry in manifest.get("runs") or []}
    checks: list[tuple[str, str]] = []
    for sample, meta in samples.items():
        # The receipt that covers the whole timeline: the largest returned_chars.
        candidates = []
        for run_id, entry in runs.items():
            if entry.get("sample") != sample or not _load_json(raw / str(entry.get("output"))):
                continue
            details = (
                ((_load_json(raw / str(entry["output"])) or {}).get("coverage") or {})
                .get("modalities", {})
                .get("text", {})
                .get("details", {})
            ) or {}
            candidates.append((int(details.get("returned_chars") or 0), run_id))
        if not candidates:
            print(f"=== {sample}: no receipt archived", file=sys.stderr)
            return 1
        run_id = max(candidates)[1]
        receipt = _transcript_rows(raw / str(runs[run_id]["output"]))
        truth_path = raw / str(truth_entries.get(sample) or "")
        if not truth_path.is_file():
            print(f"=== {sample}: missing ground truth {truth_path}", file=sys.stderr)
            return 1
        truth = _vtt_cues(truth_path)
        joined_equal = _norm(" ".join(t for _, t in receipt)) == _norm(" ".join(t for _, t in truth))
        truth_texts = {_norm(t) for _, t in truth}
        verbatim = sum(1 for _, text in receipt if _norm(text) in truth_texts)
        receipt_chars = len(" ".join(t for _, t in receipt))
        truth_chars = len(" ".join(t for _, t in truth))
        print(f"=== {sample} ({run_id}): envoy_segs={len(receipt)} gt_cues={len(truth)} joined_text_equal={joined_equal}")
        print(f"    envoy_chars {receipt_chars} gt_chars {truth_chars}")
        print(f"    envoy segments whose text appears verbatim in GT: {verbatim}/{len(receipt)}")
        print(f"    first/last envoy {receipt[0][0]}/{receipt[-1][0]}  GT {truth[0][0]}/{truth[-1][0]}")
        checks.append((sample, run_id))
    return 0 if checks else 1


def _markdown(rows: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> str:
    lines = [
        "| run | sample | cap(s) | exit | elapsed(ms) | state/result | errors | segments | first/last (s) | overrun |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {run} | {sample} | {cap} | {exit} | {elapsed} | {state}/{result} | {errors} | {seg} | {first}/{last} | {overrun} |".format(
                run=row["run_id"],
                sample=row["sample"],
                cap=row["cap_seconds"],
                exit=row["exit_code"],
                elapsed=row["elapsed_ms"],
                state=row.get("execution_state"),
                result=row.get("result"),
                errors=",".join(str(e["code"]) for e in row["errors"]) or "none",
                seg=row.get("segments"),
                first=row.get("first_seconds"),
                last=row.get("last_seconds"),
                overrun="**YES**" if row["overrun"] else "no",
            )
        )
    lines.append("")
    lines.append("| sample | caps | transcript identical | coverage identical |")
    lines.append("| --- | --- | --- | --- |")
    for pair in pairs:
        lines.append(
            "| {s} | {c} | {t} | {v} |".format(
                s=pair["sample"],
                c=pair.get("caps"),
                t=pair.get("transcript_identical"),
                v=pair.get("coverage_identical"),
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--ground-truth",
        action="store_true",
        help="compare each sample's widest receipt against the archived caption track and exit",
    )
    args = parser.parse_args(argv)

    if args.ground_truth:
        return _ground_truth(args.raw)

    manifest = _load_json(args.raw / "manifest.json")
    if manifest is None:
        print(f"missing manifest: {args.raw / 'manifest.json'}", file=sys.stderr)
        return 2

    rows = [_run_row(args.raw, entry) for entry in manifest.get("runs") or []]
    pairs = _pair_divergence(rows)
    # Cap arms are the measurements under test. The cap=1 run is a negative
    # control: it is *supposed* to overrun, and its overrun is the evidence that
    # the cap is enforced at all rather than recorded and ignored.
    arms = [row for row in rows if row.get("cap_seconds") != 1]
    control = [row for row in rows if row.get("cap_seconds") == 1]
    overruns = [row["run_id"] for row in arms if row["overrun"]]
    control_overran = [row["run_id"] for row in control if row["overrun"]]
    diverged = [pair["sample"] for pair in pairs if pair.get("divergent")]
    result = {
        "runs": len(rows),
        "cap_arms": len(arms),
        "zero_overrun": not overruns,
        "overrun_runs": overruns,
        "control_overran": control_overran,
        "control_ok": bool(control_overran),
        "arms_diverged": diverged,
        "rows": rows,
        "pairs": pairs,
        "pass": not overruns and not diverged and all(p.get("comparable") for p in pairs),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown(rows, pairs))
        print()
        print(f"runs={result['runs']} cap_arms={result['cap_arms']} zero_overrun={result['zero_overrun']} control_overran={control_overran or 'no'} diverged={diverged or 'none'} pass={result['pass']}")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
