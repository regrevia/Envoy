#!/usr/bin/env python3
"""Run the paired A/B experiment plan with two isolated Claude Code profiles.

Each plan package gets its own profile root containing a private HOME, config
directory, cache directory and working directory per arm. The baseline arm has
no Envoy plugin, MCP server or material cache; the envoy arm installs the plugin
from the local marketplace checkout and launches with ENVOY_MCP_TOOLSET=full.

Every turn injects a unique cache salt plus correlation metadata through
CLAUDE_CODE_EXTRA_BODY, so the inference proxy can attribute each request to
(run_id, package_id, arm, session_id, turn_correlation_id) without ever reading
prompt or answer bodies. Only the salt digest is retained.

Usage:
    python3 run_ab.py --plan plan.json --drafts task-drafts --out runs/2026-10-25 \
        --gateway http://127.0.0.1:4000 --limit 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXED_SET = ROOT / "benchmarks" / "fixed-set-v1"
TURN_ROLES = ("initial", "followup-1", "followup-2")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def salt_digest(salt: str) -> str:
    return hashlib.sha256(salt.encode("utf-8")).hexdigest()


def load_gateway_token() -> str:
    settings = Path(os.path.expanduser("~/.claude/settings.json"))
    env = (json.loads(settings.read_text(encoding="utf-8")).get("env") or {}) if settings.exists() else {}
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
    if not token:
        raise SystemExit("no gateway token available in the local Claude settings")
    return token


def write_settings(profile: Path, gateway: str, with_envoy: bool) -> None:
    config = profile / "claude-config"
    config.mkdir(parents=True, exist_ok=True)
    settings = {
        "env": {
            "ANTHROPIC_BASE_URL": gateway,
            "ANTHROPIC_MODEL": "anthropic/DeepSeek-V4.1-Flash",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "anthropic/DeepSeek-V4.1-Flash",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "anthropic/DeepSeek-V4.1-Flash",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "anthropic/DeepSeek-V4.1-Flash",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
        },
        "enabledPlugins": {"envoy@envoy": True} if with_envoy else {},
    }
    (config / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def install_envoy(profile: Path, token: str, gateway: str) -> None:
    env = child_env(profile, token, gateway, with_envoy=False)
    for command in (
        ["claude", "plugin", "marketplace", "add", str(ROOT)],
        ["claude", "plugin", "install", "envoy@envoy"],
    ):
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
        (profile / "install.log").open("a", encoding="utf-8").write(
            f"$ {' '.join(command)}\nexit={result.returncode}\n{result.stdout}{result.stderr}\n"
        )
        if result.returncode != 0:
            raise SystemExit(f"plugin setup failed: {' '.join(command)}: {result.stderr[-400:]}")


def child_env(profile: Path, token: str, gateway: str, *, with_envoy: bool) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "HOME": str(profile / "home"),
        "CLAUDE_CONFIG_DIR": str(profile / "claude-config"),
        "XDG_CACHE_HOME": str(profile / "cache"),
        "XDG_CONFIG_HOME": str(profile / "config"),
        "TMPDIR": str(profile / "tmp"),
        "ANTHROPIC_BASE_URL": gateway,
        "ANTHROPIC_AUTH_TOKEN": token,
        "ANTHROPIC_MODEL": "anthropic/DeepSeek-V4.1-Flash",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
    }
    if with_envoy:
        env["ENVOY_MCP_TOOLSET"] = "full"
        # The isolated HOME hides the Playwright browser cache, which makes every
        # browser-backed read fail with "headless browser failed to start".
        browsers = os.environ.get("ENVOY_PLAYWRIGHT_BROWSERS_PATH")
        if not browsers:
            for candidate in (
                Path.home() / "Library/Caches/ms-playwright",
                Path.home() / ".cache/ms-playwright",
            ):
                if candidate.is_dir():
                    browsers = str(candidate)
                    break
        if browsers:
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
        chrome = os.environ.get("ENVOY_CHROME_PATH")
        if chrome:
            env["CHROME_PATH"] = chrome
    return env


def prepare_profile(root: Path, package_id: str, arm: str, *, token: str, gateway: str) -> Path:
    profile = root / "profiles" / f"{package_id}" / arm
    if profile.exists():
        shutil.rmtree(profile)
    for name in ("home", "cache", "config", "tmp", "workdir", "claude-config"):
        (profile / name).mkdir(parents=True, exist_ok=True)
    envoy_home = Path(os.path.expanduser("~/.envoy"))
    if arm == "envoy" and (envoy_home / "asr.json").exists():
        (profile / "home" / ".envoy").mkdir(parents=True, exist_ok=True)
        shutil.copy2(envoy_home / "asr.json", profile / "home" / ".envoy" / "asr.json")
    write_settings(profile, gateway, with_envoy=(arm == "envoy"))
    if arm == "envoy":
        install_envoy(profile, token, gateway)
    return profile


def verify_isolation(profile: Path, arm: str) -> None:
    config = profile / "claude-config"
    blobs = []
    for path in config.rglob("*"):
        if path.is_file() and path.stat().st_size < 2_000_000:
            try:
                blobs.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    text = "\n".join(blobs).lower()
    if arm == "baseline":
        if "envoy" in text or "envoy-cli" in text or "mcp__envoy" in text:
            raise SystemExit(f"arm isolation broken: baseline profile mentions envoy in {profile}")
        if (profile / "home" / ".envoy").exists():
            raise SystemExit("arm isolation broken: baseline profile has an envoy home")
    else:
        if "envoy@envoy" not in text and not any("envoy" in str(path).lower() for path in config.rglob("*")):
            raise SystemExit(f"arm isolation broken: envoy profile is missing the plugin in {profile}")


def turn_prompt(draft: dict, role: str) -> str:
    urls = [revision["url"] for revision in draft["source_revisions"].values()]
    text = next(turn["text"] for turn in draft["turns"] if turn["turn_id"] == role)
    return (
        f"{text}\n\nDeliverable: {draft['deliverable']}\n\nSources:\n"
        + "\n".join(f"- {url}" for url in urls)
    )


def run_turn(
    profile: Path,
    draft: dict,
    role: str,
    *,
    token: str,
    gateway: str,
    with_envoy: bool,
    model: str,
    timeout: int,
    metadata: dict,
) -> dict:
    salt = f"{metadata['turn_correlation_id']}-{uuid.uuid4()}"
    env = child_env(profile, token, gateway, with_envoy=with_envoy)
    env["ANTHROPIC_MODEL"] = model
    env["CLAUDE_CODE_EXTRA_BODY"] = json.dumps(
        {
            "cache_salt": salt,
            "metadata": {
                "run_id": metadata["run_id"],
                "package_id": metadata["package_id"],
                "arm": metadata["arm"],
                "session_id": metadata["session_id"],
                "turn_correlation_id": metadata["turn_correlation_id"],
                "cache_salt_sha256": salt_digest(salt),
            },
        }
    )
    session_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, metadata["session_id"]))
    command = [
        "claude",
        "-p",
        turn_prompt(draft, role),
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model,
    ]
    if role == "initial":
        command += ["--session-id", session_uuid]
    else:
        command += ["--resume", session_uuid]
    started = time.time()
    proc = subprocess.Popen(
        command,
        env=env,
        cwd=profile / "workdir",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        # `claude` spawns children; kill the whole process group or the run leaks.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        stdout, stderr, code = (stdout or ""), f"timeout after {timeout}s", 124
    elapsed = round(time.time() - started, 1)
    payload = {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and "result" in candidate:
            payload = candidate
            break
    usage = payload.get("usage") or {}
    return {
        "role": role,
        "session_uuid": session_uuid,
        "salt_sha256": salt_digest(salt),
        "exit_code": code,
        "elapsed_seconds": elapsed,
        "is_error": bool(payload.get("is_error")) or code != 0,
        "reported_model": payload.get("model") or usage.get("model") or "",
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "result_chars": len(payload.get("result") or ""),
        "result_sha256": hashlib.sha256((payload.get("result") or "").encode("utf-8")).hexdigest(),
        "answer": payload.get("result") or "",
        "stderr_tail": (stderr or "")[-600:],
        "started_at": now_iso(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--drafts", type=Path, default=FIXED_SET / "task-drafts")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--model", default="anthropic/DeepSeek-V4.1-Flash")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--limit", type=int, default=0, help="run at most N packages (smoke test)")
    parser.add_argument("--package", action="append", default=[], help="only these package ids")
    args = parser.parse_args(argv)

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    run_id = plan["run_id"]
    packages = plan["packages"]
    if args.package:
        packages = [item for item in packages if item["package_id"] in set(args.package)]
    if args.limit:
        packages = packages[: args.limit]
    drafts = {}
    for path in sorted(args.drafts.glob("*.json")):
        draft = json.loads(path.read_text(encoding="utf-8"))
        drafts[draft["package_id"]] = draft
    token = os.environ.get("AB_GATEWAY_TOKEN") or load_gateway_token()
    root = args.out
    root.mkdir(parents=True, exist_ok=True)
    answers = (root / "answers.jsonl").open("a", encoding="utf-8")
    runlog = (root / "run-log.jsonl").open("a", encoding="utf-8")

    for item in packages:
        draft = drafts[item["package_id"]]
        for arm in item["arms"]:
            with_envoy = arm == "envoy"
            profile = prepare_profile(root, item["package_id"], arm, token=token, gateway=args.gateway)
            verify_isolation(profile, arm)
            session_id = item["session_ids"][arm]
            for role in TURN_ROLES:
                metadata = {
                    "run_id": run_id,
                    "package_id": item["package_id"],
                    "arm": arm,
                    "session_id": session_id,
                    "turn_correlation_id": item["turn_correlation_ids"][arm][role],
                }
                record = run_turn(
                    profile,
                    draft,
                    role,
                    token=token,
                    gateway=args.gateway,
                    with_envoy=with_envoy,
                    model=args.model,
                    timeout=args.timeout,
                    metadata=metadata,
                )
                answers.write(
                    json.dumps(
                        {
                            **metadata,
                            "role": role,
                            "session_uuid": record["session_uuid"],
                            "answer": record["answer"],
                            "elapsed_seconds": record["elapsed_seconds"],
                            "is_error": record["is_error"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                answers.flush()
                record.pop("answer")
                runlog.write(json.dumps({**metadata, **record}, ensure_ascii=False) + "\n")
                runlog.flush()
                print(
                    f"{item['package_id']}/{arm}/{role}: exit={record['exit_code']} "
                    f"{record['elapsed_seconds']}s tokens={record['input_tokens']}/{record['output_tokens']}",
                    flush=True,
                )
    answers.close()
    runlog.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
