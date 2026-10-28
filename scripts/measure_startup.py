#!/usr/bin/env python3
"""Measure Envoy's local startup baseline without touching the network.

Every case runs the repository's own interpreter
(``packages/envoy-cli/.venv/bin/python``) with ``PYTHONPATH`` pointing at the
two source trees, so the numbers describe the checkout rather than an installed
wheel. Four cases are timed ``--repeat`` times:

* ``envoy --version``
* ``envoy doctor --json`` (allowed to fail; the return code is recorded)
* ``python -c "import envoy_cli.cli"``
* ``python -X importtime -c "import envoy_cli.cli"`` (stderr is parsed)

The markdown table goes to stdout; ``--json`` also writes the full samples so
the numbers can be re-used by the Windows report. The script only imports the
standard library, never installs anything, never opens a socket, and keeps the
whole run under a bounded wall-clock budget.

Usage:
    python scripts/measure_startup.py --repeat 5 \
        --json DEV/lightweight-measurements.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PYTHON = REPO_ROOT / "packages" / "envoy-cli" / ".venv" / "bin" / "python"
ENTRYPOINT = "from envoy_cli.cli import entrypoint; entrypoint()"
IMPORT_CLI = "import envoy_cli.cli"
# A single case gets at most this long; the whole run stops before this limit so
# a hung subprocess can never turn the measurement into an open-ended wait.
CASE_TIMEOUT_SECONDS = 60.0
TOTAL_BUDGET_SECONDS = 115.0
IMPORTTIME_RE = re.compile(
    r"^\s*import time:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*?)\s*$"
)


def _pythonpath() -> str:
    parts = [
        REPO_ROOT / "packages" / "envoy-core" / "src",
        REPO_ROOT / "packages" / "envoy-cli" / "src",
    ]
    injected = [str(part) for part in parts]
    existing = os.environ.get("PYTHONPATH", "")
    return os.pathsep.join(injected + ([existing] if existing else []))


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _pythonpath()
    env.pop("PYTHONHOME", None)
    return env


def _cases() -> list[dict]:
    python = str(CLI_PYTHON)
    return [
        {
            "key": "version",
            "label": "envoy --version",
            "command": [python, "-c", ENTRYPOINT, "--version"],
            "parses_importtime": False,
        },
        {
            "key": "doctor_json",
            "label": "envoy doctor --json",
            "command": [python, "-c", ENTRYPOINT, "doctor", "--json"],
            "parses_importtime": False,
        },
        {
            "key": "import_cli",
            "label": 'python -c "import envoy_cli.cli"',
            "command": [python, "-c", IMPORT_CLI],
            "parses_importtime": False,
        },
        {
            "key": "importtime_cli",
            "label": 'python -X importtime -c "import envoy_cli.cli"',
            "command": [python, "-X", "importtime", "-c", IMPORT_CLI],
            "parses_importtime": True,
        },
    ]


def _run_once(command: list[str], timeout: float) -> dict:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ms": elapsed_ms,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "ms": elapsed_ms,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
        }


def _parse_importtime(stderr: str) -> dict[str, dict[str, int]]:
    """Return exact-module and top-level-module cumulative microseconds."""

    exact: dict[str, int] = {}
    for line in stderr.splitlines():
        match = IMPORTTIME_RE.match(line)
        if not match:
            continue
        cumulative_us = int(match.group(2))
        module = match.group(3).strip()
        if not module:
            continue
        # One module can be printed more than once; keep the larger cumulative.
        exact[module] = max(exact.get(module, 0), cumulative_us)
    top_level: dict[str, int] = {}
    for module, cumulative_us in exact.items():
        root = module.split(".")[0]
        top_level[root] = top_level.get(root, 0) + cumulative_us
    return {"exact": exact, "top_level": top_level}


def _summarize(samples_ms: list[float], returncodes: list[int | None]) -> dict:
    values = sorted(samples_ms)
    return {
        "samples_ms": [round(value, 2) for value in samples_ms],
        "median_ms": round(statistics.median(values), 2),
        "min_ms": round(values[0], 2),
        "max_ms": round(values[-1], 2),
        "returncodes": returncodes,
        "all_succeeded": all(code == 0 for code in returncodes),
    }


def _median_map(runs: list[dict[str, int]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for name in {name for run in runs for name in run}:
        present = [run[name] for run in runs if name in run]
        merged[name] = int(statistics.median(present))
    return merged


def _markdown(report: dict) -> str:
    lines = [
        "# Envoy local startup baseline",
        "",
        f"- Repo: `{report['repo']}`",
        f"- Python: `{report['python']}` ({report['platform']})",
        f"- Repeat: {report['repeat']} per case",
        "",
        "| Case | Median (ms) | Min (ms) | Max (ms) | Return codes |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for case in report["cases"]:
        codes = ", ".join("timeout" if code is None else str(code) for code in case["returncodes"])
        lines.append(
            f"| {case['label']} | {case['median_ms']:.1f} | {case['min_ms']:.1f} "
            f"| {case['max_ms']:.1f} | {codes} |"
        )
    top = report["importtime"]["top_modules"]
    lines += [
        "",
        "## `-X importtime` cumulative by module (top 15)",
        "",
        "| # | Module | Cumulative (ms) | Present in runs |",
        "| ---: | --- | ---: | ---: |",
    ]
    for index, row in enumerate(top, start=1):
        lines.append(
            f"| {index} | `{row['module']}` | {row['cumulative_ms']:.1f} | {row['runs']} |"
        )
    lines += [
        "",
        "Modules are kept at their exact `importtime` name so a line can be compared "
        "with the same package on another machine. The nested `by_top_level` block in "
        "the JSON adds up every submodule's cumulative time and therefore double "
        "counts parents; do not use it to describe wall-clock cost.",
    ]
    return "\n".join(lines) + "\n"


def build_report(repeat: int) -> dict:
    if not CLI_PYTHON.exists():
        raise SystemExit(f"repository interpreter not found: {CLI_PYTHON}")

    deadline = time.perf_counter() + TOTAL_BUDGET_SECONDS
    report_cases: list[dict] = []
    importtime_runs_top: list[dict[str, int]] = []
    importtime_runs_exact: list[dict[str, int]] = []
    truncated = False

    for case in _cases():
        samples: list[float] = []
        returncodes: list[int | None] = []
        for _ in range(repeat):
            remaining = deadline - time.perf_counter()
            if remaining <= 1.0:
                truncated = True
                break
            result = _run_once(case["command"], min(CASE_TIMEOUT_SECONDS, remaining))
            samples.append(result["ms"])
            returncodes.append(result["returncode"])
            if case["parses_importtime"] and not result["timed_out"]:
                parsed = _parse_importtime(result["stderr"])
                if parsed["top_level"]:
                    importtime_runs_top.append(parsed["top_level"])
                    importtime_runs_exact.append(parsed["exact"])
        if not samples:
            truncated = True
            break
        summary = _summarize(samples, returncodes)
        summary.update({"key": case["key"], "label": case["label"], "command": case["command"]})
        report_cases.append(summary)
        if truncated:
            break

    top_median = _median_map(importtime_runs_top)
    exact_median = _median_map(importtime_runs_exact)
    top_modules = [
        {
            "module": module,
            "cumulative_us": cumulative_us,
            "cumulative_ms": round(cumulative_us / 1000.0, 1),
            "runs": sum(1 for run in importtime_runs_exact if module in run),
        }
        for module, cumulative_us in sorted(exact_median.items(), key=lambda item: -item[1])
    ][:15]
    by_top_level = [
        {
            "module": module,
            "cumulative_us": cumulative_us,
            "cumulative_ms": round(cumulative_us / 1000.0, 1),
        }
        for module, cumulative_us in sorted(top_median.items(), key=lambda item: -item[1])
    ][:15]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo": str(REPO_ROOT),
        "branch_hint": "run from a checkout; numbers describe this worktree",
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": f"{platform.system()} {platform.machine()}",
        "repeat": repeat,
        "total_budget_seconds": TOTAL_BUDGET_SECONDS,
        "truncated": truncated,
        "cases": report_cases,
        "importtime": {
            "runs_parsed": len(importtime_runs_top),
            "grouping": "exact importtime module name, median cumulative µs across runs",
            "top_modules": top_modules,
            "by_top_level": by_top_level,
            "by_top_level_note": "sums every submodule's cumulative time; parents and "
            "children overlap, so this block describes import structure, not wall clock",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=5, help="runs per case (default: 5)")
    parser.add_argument("--json", type=Path, help="write the full report as JSON")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    report = build_report(args.repeat)
    markdown = _markdown(report)
    sys.stdout.write(markdown)
    if args.json:
        target = args.json if args.json.is_absolute() else REPO_ROOT / args.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sys.stdout.write(f"\nJSON written to {target}\n")
    if report["truncated"]:
        sys.stderr.write("warning: measurement stopped early at the total time budget\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
