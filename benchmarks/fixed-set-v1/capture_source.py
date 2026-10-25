#!/usr/bin/env python3
"""Capture one public URL into a content-addressed material snapshot.

The fixed-set freeze gate only accepts a source revision whose SHA-256 names a
file in ``materials/`` and whose reference facts are verbatim substrings of it.
This helper produces exactly that pair:

* ``materials/<sha256>.txt`` - the captured text blocks, joined the same way
  the intake snapshots were produced (blocks separated by a blank line).
* ``<capture-dir>/<tag>.json`` - provenance for the operator: URL, result,
  block inventory, resource count, elapsed time, notices and errors.

It never records cookies, prompts or credentials. ``ENVOY_BROWSER_PROFILE_DIR``
is honoured by the browser, so several captures may run in parallel with
distinct profile directories.

Usage:
    python3 capture_source.py URL TAG --goal "..." [--capture-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CAPTURE_DIR = Path("/tmp/envoy-fixed-set-captures")


def block_texts(payload: dict) -> list[str]:
    texts: list[str] = []
    for block in payload.get("blocks") or []:
        content = block.get("content") or {}
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("tag")
    parser.add_argument("--goal", default="提取该公开来源的正文要点、关键论据与可复核事实")
    parser.add_argument("--capture-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--materials", type=Path, default=ROOT / "materials")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--max-work-seconds",
        type=int,
        default=600,
        help="envoy_read media budget; video sources need 600 to survive audio download + local ASR",
    )
    args = parser.parse_args(argv)

    args.capture_dir.mkdir(parents=True, exist_ok=True)
    args.materials.mkdir(parents=True, exist_ok=True)

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "envoy_read",
                "arguments": {
                    "source": {"url": args.url},
                    "goal": args.goal,
                    "budget": {"max_work_seconds": args.max_work_seconds},
                },
            },
        },
    ]
    request_path = args.capture_dir / f"{args.tag}.request.jsonl"
    request_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n", encoding="utf-8")

    started = time.time()
    completed = subprocess.run(
        [os.environ.get("ENVOY_BIN", "envoy"), "envoy-mcp" if False else "mcp"],
        stdin=request_path.open("rb"),
        capture_output=True,
        timeout=args.timeout,
    )
    elapsed = round(time.time() - started, 1)
    (args.capture_dir / f"{args.tag}.stderr.txt").write_text(
        completed.stderr.decode("utf-8", "replace"), encoding="utf-8"
    )

    payload: dict = {}
    is_error = None
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") != 2:
            continue
        result = message.get("result") or {}
        is_error = result.get("isError")
        content = result.get("content") or [{}]
        raw = content[0].get("text", "{}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"result": "unparsed", "raw": raw[:2000]}

    texts = block_texts(payload)
    body = "\n\n".join(texts)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    snapshot = None
    if body.strip():
        snapshot_path = args.materials / f"{digest}.txt"
        snapshot_path.write_text(body, encoding="utf-8")
        snapshot = str(snapshot_path)

    record = {
        "tag": args.tag,
        "url": args.url,
        "goal": args.goal,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": elapsed,
        "mcp_exit_code": completed.returncode,
        "is_error": is_error,
        "result": payload.get("result"),
        "protocol_version": payload.get("protocol_version"),
        "source_revision": payload.get("source_revision"),
        "material_id": payload.get("material_id"),
        "execution_state": payload.get("execution_state"),
        "block_kinds": [block.get("kind") for block in payload.get("blocks") or []],
        "text_chars": len(body),
        "snapshot": snapshot,
        "snapshot_sha256": digest if snapshot else None,
        "resource_count": len(payload.get("resources") or []),
        "coverage": payload.get("coverage"),
        "needs": payload.get("needs"),
        "notices": payload.get("notices"),
        "errors": payload.get("errors"),
        "head": body[:400],
    }
    (args.capture_dir / f"{args.tag}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: record[key] for key in (
        "tag", "url", "result", "text_chars", "snapshot_sha256", "elapsed_seconds", "block_kinds",
    )}, ensure_ascii=False))
    return 0 if record["result"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    sys.exit(main())
