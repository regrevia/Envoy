#!/usr/bin/env python3
"""Windows 真机读取验收：read → expand → replay，只用已安装的 shim。

边界：
- CLI 只接受公网 HTTP(S) 源（本地文件与 127.0.0.1 会被 source_unavailable 拒绝），
  所以默认读仓库自己的公开 README；不额外起本地服务。
- 不下载媒体、不起浏览器、不加载 ASR 模型：只验证材料链。
- Long PDF: honest page accounting, plus a second range that carries new pages.
- Resume: a fresh process continues an interrupted read on the same material.
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
PDF_SOURCE = (
    "https://raw.githubusercontent.com/regrevia/Envoy/main"
    "/tests/fixtures/pdf/long-120-pages.pdf"
)

READ_BUDGET_CHARS = 1200
EXPAND_BUDGET_CHARS = 800
RESUME_BUDGET_CHARS = 800
# A fixture page holds ~5350 chars, so these budgets cross page borders:
# a 12000-char read ends inside page 3, an 8000-char expand reaches page 5.
PDF_READ_BUDGET_CHARS = 12000
# The cumulative cap is fixed when the material is first read: an expand can
# never get past it, so the read has to authorize the whole document up front.
PDF_READ_TOTAL_CHARS = 48000
PDF_EXPAND_BUDGET_CHARS = 8000
PDF_MAX_EXPANDS = 3

PDF_PAGE_BLOCK = re.compile(r"^blk-pdf-page-(\d+)(?:-range-\d+)?$")


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


def _request_file(workdir, name, payload):
    """Write one request JSON under the acceptance workdir; return its path."""
    path = Path(workdir) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _run_json(prefix, subcommand, request_path, workdir):
    """Run one request-bearing subcommand; return (rc, receipt, stderr)."""
    code, out, err = _invoke(
        prefix, [subcommand, "--request-json", request_path], cwd=workdir
    )
    if code != 0:
        return code, {}, err
    try:
        payload = _loads(out)
    except ValueError as exc:
        return code, {}, str(exc)
    receipt = payload.get("receipt", payload) if isinstance(payload, dict) else {}
    if not isinstance(receipt, dict):
        receipt = {}
    return code, receipt, err


def _read_document(
    prefix, workdir, source_url, budget_chars, tag, goal, total_chars=None
):
    """Issue one read request and return (rc, receipt, stderr)."""
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "win-%s-%s" % (tag, uuid.uuid4().hex[:8]),
        "source": {"url": source_url},
        "goal": goal,
        "budget": {
            "max_work_seconds": 90,
            "max_response_chars": budget_chars,
            "max_total_response_chars": total_chars or budget_chars,
        },
    }
    path = _request_file(workdir, "%s-request.json" % tag, request)
    return _run_json(prefix, "read", path, workdir)


def _delivered_text(receipt):
    """Every block's text in delivery order, concatenated."""
    return "".join(_block_text(block) for block in receipt.get("blocks") or [])


def _first_error(receipt):
    """First error entry as short text, or an empty string."""
    errors = receipt.get("errors") or []
    return str(errors[0])[:200] if errors else ""


def _pdf_pages(blocks):
    """Sorted, unique page numbers carried by delivered PDF block ids."""
    pages = set()
    for block in blocks:
        match = PDF_PAGE_BLOCK.match(str(block.get("id") or ""))
        if match:
            pages.add(int(match.group(1)))
    return sorted(pages)


def _insert_after(evidence, anchor_id, items):
    """Splice ``items`` right after the check named ``anchor_id``."""
    ids = [item["id"] for item in evidence]
    index = ids.index(anchor_id) + 1 if anchor_id in ids else len(evidence)
    evidence[index:index] = items
    return evidence


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




def check_long_pdf_pages(prefix, workdir, pdf_source, evidence):
    """A long PDF must report honest page accounting and deliver new pages."""
    code, receipt, err = _read_document(
        prefix,
        workdir,
        pdf_source,
        PDF_READ_BUDGET_CHARS,
        "pdf-read",
        "Windows acceptance: long PDF page accounting",
        total_chars=PDF_READ_TOTAL_CHARS,
    )
    text = _text_details(receipt)
    material_id = receipt.get("material_id")
    revision = receipt.get("source_revision")
    page_count = text.get("page_count")
    returned_chars = text.get("returned_chars")
    total_chars = text.get("total_chars")
    next_offset = text.get("next_offset")
    pages_after_read = _pdf_pages(receipt.get("blocks") or [])
    material_ok = isinstance(material_id, str) and bool(MATERIAL_ID.match(material_id))
    revision_ok = isinstance(revision, str) and bool(REVISION.match(revision))
    counters_ok = all(
        isinstance(value, int) for value in (page_count, returned_chars, total_chars)
    )
    read_ok = bool(
        code == 0
        and not _first_error(receipt)
        and receipt.get("result") == "partial"
        and material_ok
        and revision_ok
        and counters_ok
        and page_count >= 100
        and len(pages_after_read) >= 2
        and total_chars > returned_chars
        and isinstance(next_offset, int)
        and next_offset > 0
        and text.get("continuation_available") is True
    )
    detail = {
        "rc": code,
        "result": receipt.get("result") if receipt else None,
        "page_count": page_count,
        "next_offset": next_offset,
        "pages_after_read": pages_after_read,
        "pages_after_expand": [],
        "returned_chars": returned_chars,
    }
    if not read_ok:
        # A failed read carries no material id: never make one up.
        detail["stderr"] = (err.strip() or _first_error(receipt))[:300]
        evidence.append(
            {
                "id": "long_pdf_pages",
                "name": "long PDF read accounts pages honestly",
                "ok": False,
                "detail": detail,
            }
        )
        return False
    detail["material_id"] = material_id

    # One PDF expand delivers the rest of the page the offset lands in and stops
    # there, so reaching a later page takes several rounds. Each round is its own
    # process: the only state carried across is material_id plus the offset.
    pages_after_expand = []
    offset = next_offset
    rounds = 0
    expand_ok = True
    expand_rc = None
    stderr = ""
    while rounds < PDF_MAX_EXPANDS:
        rounds += 1
        expand_request = {
            "protocol_version": "envoy.read/1",
            "request_id": "win-pdf-expand-%s" % uuid.uuid4().hex[:8],
            "material_id": material_id,
            "offset": offset,
            "max_chars": PDF_EXPAND_BUDGET_CHARS,
            # The cap is cumulative for the material, not per call: it has to
            # cover everything already delivered, or the expand is refused
            # instead of repeating that text.
            "max_total_response_chars": PDF_READ_TOTAL_CHARS,
        }
        expand_path = _request_file(
            workdir, "pdf-expand-request-%d.json" % rounds, expand_request
        )
        expand_rc, expand_receipt, expand_err = _run_json(
            prefix, "expand", expand_path, workdir
        )
        text_e = _text_details(expand_receipt)
        pages = _pdf_pages(expand_receipt.get("blocks") or [])
        pages_after_expand.extend(
            page for page in pages if page not in pages_after_expand
        )
        returned = text_e.get("returned_chars")
        advanced = (
            isinstance(text_e.get("next_offset"), int)
            and text_e["next_offset"] > offset
        )
        if not (
            expand_rc == 0
            and not _first_error(expand_receipt)
            and isinstance(returned, int)
            and returned > 0
            and advanced
        ):
            expand_ok = False
            stderr = (
                expand_err.strip()
                or _first_error(expand_receipt)
                or "expand did not advance past offset %s" % offset
            )[:300]
            break
        offset = text_e["next_offset"]
        if any(page > max(pages_after_read) for page in pages):
            break
    if expand_ok and not any(
        page > max(pages_after_read) for page in pages_after_expand
    ):
        expand_ok = False
        stderr = "no page beyond %d after %d expand rounds" % (
            max(pages_after_read),
            rounds,
        )
    detail["pages_after_expand"] = pages_after_expand
    detail["expand_rounds"] = rounds
    detail["expand_rc"] = expand_rc
    if not expand_ok:
        detail["stderr"] = stderr
    ok = bool(read_ok and expand_ok)
    evidence.append(
        {
            "id": "long_pdf_pages",
            "name": "long PDF read accounts pages honestly",
            "ok": ok,
            "detail": detail,
        }
    )
    return ok


def check_resume_after_interrupt(prefix, workdir, source_url, evidence):
    """A fresh process must continue an interrupted read, not start over."""
    code, receipt_a, err_a = _read_document(
        prefix,
        workdir,
        source_url,
        RESUME_BUDGET_CHARS,
        "resume-read",
        "Windows acceptance: resume an interrupted read",
    )
    text_a = _delivered_text(receipt_a)
    ids_a = [
        str(block.get("id")) for block in receipt_a.get("blocks") or [] if block.get("id")
    ]
    material_id = receipt_a.get("material_id")
    revision = receipt_a.get("source_revision")
    offset_from = _text_details(receipt_a).get("next_offset")
    material_ok = isinstance(material_id, str) and bool(MATERIAL_ID.match(material_id))
    revision_ok = isinstance(revision, str) and bool(REVISION.match(revision))
    if code != 0:
        problem = "step A read failed with rc %s" % code
    elif not receipt_a:
        problem = "step A read did not return JSON: %s" % err_a.strip()[:200]
    elif _first_error(receipt_a):
        problem = "step A read reported an error: %s" % _first_error(receipt_a)
    elif not material_ok:
        problem = "step A read returned no usable material id"
    elif not revision_ok:
        problem = "step A read returned no usable source revision"
    elif not isinstance(offset_from, int) or offset_from <= 0:
        problem = "step A read returned no positive next_offset"
    elif not text_a:
        problem = "step A read delivered no text"
    else:
        problem = ""
    if problem:
        evidence.append(
            {
                "id": "resume_after_interrupt",
                "name": "a fresh process resumes the interrupted read",
                "ok": False,
                "detail": {
                    "reason": problem,
                    "rc": code,
                    "material_id": material_id if material_ok else None,
                    "revision_kept": False,
                    "offset_from": None,
                    "offset_to": None,
                    "blocks_step_a": len(ids_a),
                    "blocks_step_b": 0,
                    "stderr": err_a.strip()[:300],
                },
            }
        )
        return False

    # A resume passes only the material and the offset: no source, no budget.
    resume_request = {
        "protocol_version": "envoy.read/1",
        "request_id": "win-resume-%s" % uuid.uuid4().hex[:8],
        "material_id": material_id,
        "offset": offset_from,
    }
    resume_path = _request_file(workdir, "resume-expand-request.json", resume_request)
    code_b, receipt_b, err_b = _run_json(prefix, "expand", resume_path, workdir)
    text_b = _delivered_text(receipt_b)
    ids_b = [
        str(block.get("id")) for block in receipt_b.get("blocks") or [] if block.get("id")
    ]
    material_b = receipt_b.get("material_id")
    revision_b = receipt_b.get("source_revision")
    offset_to = _text_details(receipt_b).get("next_offset")
    if not isinstance(offset_to, int):
        offset_to = None
    same_material = isinstance(material_b, str) and material_b == material_id
    # An expand need not echo the revision; absent means it was not changed.
    revision_kept = revision_b is None or revision_b == revision
    changed_text = bool(text_b) and text_b != text_a
    fresh_blocks = bool(ids_b) and not (set(ids_b) & set(ids_a))
    ok = bool(
        code_b == 0
        and not _first_error(receipt_b)
        and same_material
        and changed_text
        and fresh_blocks
    )
    detail = {
        "rc": code_b,
        "material_id": material_id,
        "revision_kept": bool(revision_kept),
        "offset_from": offset_from,
        "offset_to": offset_to,
        "blocks_step_a": len(ids_a),
        "blocks_step_b": len(ids_b),
        "same_material": bool(same_material),
        "changed_text": bool(changed_text),
        "fresh_blocks": bool(fresh_blocks),
    }
    if not ok:
        detail["stderr"] = (err_b.strip() or _first_error(receipt_b))[:300]
    evidence.append(
        {
            "id": "resume_after_interrupt",
            "name": "a fresh process resumes the interrupted read",
            "ok": ok,
            "detail": detail,
        }
    )
    return ok


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
        "--pdf-source",
        default=PDF_SOURCE,
        help="public HTTP(S) PDF used by the page-accounting check",
    )
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
    extra = []
    check_long_pdf_pages(prefix, workdir, args.pdf_source, extra)
    check_resume_after_interrupt(prefix, workdir, args.source, extra)
    # The chain lists both right after expand; keep that order in the report.
    _insert_after(evidence, "expand_continues", extra)
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
