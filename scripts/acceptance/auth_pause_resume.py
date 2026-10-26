#!/usr/bin/env python3
"""Acceptance: one login pause per site, one verification, resume only the paused items.

The read layer is stubbed (a real login wall cannot be scripted against a live
site), but the pause bookkeeping, the single verification, the per-reason retry,
the lease and the cross-session discovery all run for real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))

PROFILE = "https://www.douyin.com/user/acceptance"
TOTAL, PAUSED = 20, 3


def _candidates() -> list[dict]:
    return [
        {
            "candidate_id": f"cand-{index:02d}",
            "source_url": f"https://www.douyin.com/video/{index:012d}",
            "title": f"第 {index} 条",
            "platform": "douyin",
            "profile_url": PROFILE,
        }
        for index in range(1, TOTAL + 1)
    ]


class _Block:
    def model_dump(self, mode="json"):
        return {"kind": "paragraph", "text": "结算与转写正文"}


class _Ready:
    result = "ready"
    material_id = "mat-acceptance"
    source_revision = "rev-1"
    expires_at = None
    errors = []

    def __init__(self, request):
        self.request_id = request.request_id
        self.blocks = [_Block()]


def main() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")

    work = Path(tempfile.mkdtemp(prefix="envoy-acceptance-"))
    os.environ["ENVOY_LOCAL_DATA_DIR"] = str(work / "data")
    os.environ["ENVOY_AUTH_DIR"] = str(work / "auth")
    os.environ["ENVOY_BROWSER_PROFILE_DIR"] = str(work / "profile")

    from envoy_cli import authflow, auth_window, cli, collect as collect_module, reader
    from envoy_core.schemas import ErrorCode, ReadRequest

    reads: list[str] = []

    def fake_discover(*args, **kwargs):
        return {
            "profile_url": PROFILE,
            "platform": "douyin",
            "candidates": _candidates(),
            "discovery_revision": "disc-acceptance",
        }

    def verified() -> bool:
        return any(a.get("status") == "ok" for a in authflow.list_auth_actions())

    def fake_read(request):
        url = str(request.source.url)
        reads.append(url)
        if url.rsplit("/", 1)[-1].replace("0", "") and int(url.rsplit("/", 1)[-1]) > TOTAL - PAUSED:
            if not verified():
                return reader._blocked(request, ErrorCode.AUTH_REQUIRED, "该站点需要登录", True)
        return _Ready(request)

    collect_module.checkpoint.discover_profile = fake_discover
    collect_module.read_public_page = fake_read

    out = work / "pack.md"
    first = collect_module.collect(PROFILE, str(out), limit=TOTAL, collection_id="accept-col", run_id="accept-run")

    check("17 条完成", first["completed"] == TOTAL - PAUSED, f"completed={first['completed']}")
    pause = first.get("auth_pause") or {}
    check("3 条因同一站点暂停", len(pause.get("candidates") or []) == PAUSED, f"paused={len(pause.get('candidates') or [])}")
    check("同一站点只读一次就停", len(reads) == TOTAL - PAUSED + 1, f"reads={len(reads)}")
    action_id = str(pause.get("action_id") or "")
    check("暂停有可恢复 id", action_id.startswith("auth-"), action_id)

    auth_window.verify_source = lambda url, timeout_ms=45000: {
        "page_access": True, "media_access": True, "headless_reusable": True, "reason": "readable",
    }
    verified_rate = cli.main(["auth", "verify", "--action-id", action_id, "--json"])
    check("一次验证通过", verified_rate == 0, f"exit={verified_rate}")
    check("暂停标记为已完成", authflow.load_auth_action(action_id)["status"] == "ok")

    second = collect_module.collect(
        PROFILE, str(out), limit=TOTAL, collection_id="accept-col", run_id="accept-run",
        retry_reason="auth",
    )
    resumed = len(reads) - (TOTAL - PAUSED + 1)
    check("只续跑那 3 条", resumed == PAUSED, f"reads_now={resumed}")
    check("20 条全部完成", second["completed"] == TOTAL, f"completed={second['completed']}")

    env = {**os.environ, "PYTHONPATH": f"{ROOT/'packages/envoy-core/src'}:{ROOT/'packages/envoy-cli/src'}"}
    listed = subprocess.run(
        [
            sys.executable, "-c",
            "from envoy_cli.cli import main; raise SystemExit(main(['collect','--list','--json']))",
        ],
        capture_output=True, text=True, env=env, timeout=120,
    )
    try:
        runs = json.loads(listed.stdout)["runs"]
    except (ValueError, KeyError):
        runs = []
    match = [run for run in runs if run.get("collection_id") == "accept-col"]
    check("重新打开仍能找到任务", bool(match), f"exit={listed.returncode}")
    if match:
        check("任务进度已保存", match[0]["completed"] == TOTAL, f"completed={match[0]['completed']}")

    elapsed = time.monotonic()
    print(f"\n{sum(1 for _, ok in checks if ok)}/{len(checks)} passed · {work}")
    _ = elapsed
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
