#!/usr/bin/env python3
"""Windows 真机读取验收：read → expand → replay，只用已安装的 shim。

边界：
- CLI 只接受公网 HTTP(S) 源（本地文件与 127.0.0.1 会被 source_unavailable 拒绝），
  所以默认读仓库自己的公开 README；不额外起本地服务。
- 不下载媒体、不起浏览器、不加载 ASR 模型：只验证材料链。
- 每个检查独立产出 {id, name, ok, detail}，一项失败不影响其它项。

用法（真机）：
    python read_acceptance.py --out evidence.json
退出码：存在失败检查则为 1。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

MATERIAL_ID = re.compile(r"^mat-[a-f0-9]{24}$")
REVISION = re.compile(r"^[a-f0-9]{64}$")
DEFAULT_SOURCE = "https://raw.githubusercontent.com/regrevia/Envoy/main/README.md"

READ_BUDGET_CHARS = 1200
EXPAND_BUDGET_CHARS = 800


def _invoke(prefix, argv, cwd=None, timeout=240, env=None):
    """Run `envoy <argv>` and return (rc, stdout, stderr) as text."""
    command = list(prefix) + list(argv)
    # A .cmd shim is not an executable image: CreateProcess needs cmd.exe to run it.
    if os.name == "nt" and command and str(command[0]).lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c"] + command
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", "executable not found: %s" % command[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", "replace"),
        completed.stderr.decode("utf-8", "replace"),
    )


def _loads(text):
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - the detail is the evidence
        raise ValueError("stdout is not JSON: %s" % exc)


def _text_details(receipt):
    """Text modality counters; per-format counters live in ``details``."""
    coverage = receipt.get("coverage") or {}
    modalities = coverage.get("modalities") or {}
    text = modalities.get("text") or {}
    merged = dict(text)
    nested = text.get("details")
    if isinstance(nested, dict):
        merged.update(nested)
    return merged


def _block_text(block):
    content = block.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        return content
    return ""


def _receipt_head(receipt):
    for block in receipt.get("blocks") or []:
        text = _block_text(block).strip()
        if text:
            return text[:120]
    return ""


def check_version(prefix, workdir, evidence):
    code, out, err = _invoke(prefix, ["--version"], cwd=workdir)
    detail = (out or err).strip().splitlines()[:1]
    ok = code == 0 and bool(out.strip()) and "envoy-cli" in out
    evidence.append(
        {"id": "version", "name": "shim runs", "ok": ok, "detail": detail}
    )
    return ok


def check_doctor(prefix, workdir, evidence):
    code, out, err = _invoke(prefix, ["doctor", "--json"], cwd=workdir)
    ok = code == 0
    detail = {}
    if ok:
        try:
            parsed = _loads(out)
            detail = {"keys": sorted(parsed)[:8]}
        except ValueError as exc:
            ok = False
            detail = {"error": str(exc)}
    else:
        detail = {"rc": code, "stderr": err.strip()[:200]}
    evidence.append(
        {"id": "doctor", "name": "doctor --json", "ok": ok, "detail": detail}
    )
    return ok


def run_chain(prefix, workdir, source_url, evidence):
    """read → expand → replay on one public document."""
    request_id = "win-read-%s" % uuid.uuid4().hex[:8]
    read_request = {
        "protocol_version": "envoy.read/1",
        "request_id": request_id,
        "source": {"url": source_url},
        "goal": "Windows 真机验收：区间交付与续读",
        "budget": {
            "max_work_seconds": 90,
            "max_response_chars": READ_BUDGET_CHARS,
            "max_total_response_chars": READ_BUDGET_CHARS,
        },
    }
    read_path = Path(workdir) / "read-request.json"
    read_path.write_text(json.dumps(read_request), encoding="utf-8")

    ready = False
    code, out, err = _invoke(prefix, ["read", "--request-json", str(read_path)], cwd=workdir)
    receipt = {}
    if code == 0:
        try:
            receipt = _loads(out)
            ready = True
        except ValueError as exc:
            err = str(exc)
    if ready:
        receipt = receipt.get("receipt", receipt)
    text = _text_details(receipt) if ready else {}
    material_id = receipt.get("material_id") if ready else None
    revision = receipt.get("source_revision") if ready else None
    next_offset = text.get("next_offset")
    ok = bool(
        ready
        and receipt.get("result") == "partial"
        and isinstance(material_id, str)
        and MATERIAL_ID.match(material_id)
        and isinstance(revision, str)
        and REVISION.match(revision)
        and isinstance(next_offset, int)
        and next_offset > 0
        and text.get("continuation_available") is True
    )
    evidence.append(
        {
            "id": "read_range",
            "name": "read delivers a first range",
            "ok": ok,
            "detail": {
                "rc": code,
                "result": receipt.get("result") if ready else None,
                "material_id": material_id,
                "total_chars": text.get("total_chars"),
                "returned_chars": text.get("returned_chars"),
                "next_offset": next_offset,
                "stderr": err.strip()[:300] if not ok else "",
            },
        }
    )
    if not ok:
        return {"material_id": material_id, "revision": revision, "next_offset": next_offset}

    head = _receipt_head(receipt)
    expand_request = {
        "protocol_version": "envoy.read/1",
        "request_id": "%s-expand" % request_id,
        "material_id": material_id,
        "offset": next_offset,
        "max_chars": EXPAND_BUDGET_CHARS,
        "max_total_response_chars": EXPAND_BUDGET_CHARS,
    }
    expand_path = Path(workdir) / "expand-request.json"
    expand_path.write_text(json.dumps(expand_request), encoding="utf-8")
    code, out, err = _invoke(prefix, ["expand", "--request-json", str(expand_path)], cwd=workdir)
    expand_receipt = {}
    if code == 0:
        try:
            expand_receipt = _loads(out)
        except ValueError as exc:
            err = str(exc)
    expand_receipt = expand_receipt.get("receipt", expand_receipt) if expand_receipt else {}
    blocks = expand_receipt.get("blocks") or []
    range_ids = [str(block.get("id")) for block in blocks if block.get("id")]
    suffix = "-range-%d" % next_offset
    carries_offset = any(block_id.endswith(suffix) for block_id in range_ids)
    new_head = _receipt_head(expand_receipt)
    delivered_new_text = bool(new_head) and new_head != head
    ok = bool(
        code == 0
        and not (expand_receipt.get("errors") or [])
        and range_ids
        and carries_offset
        and delivered_new_text
    )
    evidence.append(
        {
            "id": "expand_continues",
            "name": "expand continues at the absolute offset",
            "ok": ok,
            "detail": {
                "rc": code,
                "offset": next_offset,
                "block_ids": range_ids[:4],
                "carries_offset": carries_offset,
                "delivered_new_text": delivered_new_text,
                "stderr": err.strip()[:300] if not ok else "",
            },
        }
    )

    replay_request = {
        "protocol_version": "envoy.read/1",
        "request_id": "%s-replay" % request_id,
        "material_id": material_id,
        "source_revision": revision,
        "session_id": "win-replay-%s" % uuid.uuid4().hex[:8],
        "max_chars": READ_BUDGET_CHARS,
        "max_total_response_chars": READ_BUDGET_CHARS,
    }
    replay_path = Path(workdir) / "replay-request.json"
    replay_path.write_text(json.dumps(replay_request), encoding="utf-8")
    code, out, err = _invoke(prefix, ["replay", "--request-json", str(replay_path)], cwd=workdir)
    replay_receipt = {}
    if code == 0:
        try:
            replay_receipt = _loads(out)
        except ValueError as exc:
            err = str(exc)
    replay_receipt = replay_receipt.get("receipt", replay_receipt) if replay_receipt else {}
    # Replay deliberately mints a material id for the new session; the revision
    # and the content must be the retained ones, not a fresh fetch.
    replay_id = replay_receipt.get("material_id")
    ok = bool(
        code == 0
        and isinstance(replay_id, str)
        and MATERIAL_ID.match(replay_id)
        and replay_id != material_id
        and replay_receipt.get("source_revision") == revision
        and not (replay_receipt.get("errors") or [])
    )
    evidence.append(
        {
            "id": "replay_reuses_material",
            "name": "replay hands the same material to a new session",
            "ok": ok,
            "detail": {
                "rc": code,
                "source_material_id": material_id,
                "replay_material_id": replay_id,
                "revision_matches": replay_receipt.get("source_revision") == revision,
                "isolated_material": replay_id != material_id,
                "stderr": err.strip()[:300] if not ok else "",
            },
        }
    )
    return {"material_id": material_id, "revision": revision, "next_offset": next_offset}


def check_unicode_home(prefix, workdir, source_url, evidence):
    """A path with a space and non-ASCII characters must keep working."""
    home = Path(workdir) / "验收 目录" / ".envoy"
    home.mkdir(parents=True, exist_ok=True)
    code, out, err = _invoke(
        prefix,
        ["doctor", "--json"],
        cwd=workdir,
        env={"ENVOY_HOME": str(home)},
    )
    ok = code == 0
    detail = {"ENVOY_HOME": str(home), "rc": code}
    if ok:
        try:
            _loads(out)
        except ValueError as exc:
            ok = False
            detail["error"] = str(exc)
    else:
        detail["stderr"] = err.strip()[:200]
    evidence.append(
        {
            "id": "unicode_home",
            "name": "a space + non-ASCII ENVOY_HOME works",
            "ok": ok,
            "detail": detail,
        }
    )
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write the evidence JSON here")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="public HTTP(S) document")
    parser.add_argument(
        "--envoy",
        default=os.environ.get("ENVOY_BIN", "envoy"),
        help="shim path; defaults to env ENVOY_BIN or `envoy` on PATH",
    )
    parser.add_argument(
        "--envoy-prefix",
        default="",
        help="extra argv before the subcommand, e.g. 'uv run --offline envoy'",
    )
    args = parser.parse_args(argv)

    prefix = [args.envoy]
    if args.envoy_prefix.strip():
        prefix = args.envoy_prefix.split() + prefix

    workdir = Path(tempfile.mkdtemp(prefix="envoy-read-acceptance-"))
    evidence = []
    check_version(prefix, workdir, evidence)
    check_doctor(prefix, workdir, evidence)
    chain = run_chain(prefix, workdir, args.source, evidence)
    check_unicode_home(prefix, workdir, args.source, evidence)

    payload = {
        "kind": "envoy-windows-read-acceptance",
        "checks": evidence,
        "failed": [item["id"] for item in evidence if not item["ok"]],
        "material": chain,
        "workdir": str(workdir),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 1 if payload["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
