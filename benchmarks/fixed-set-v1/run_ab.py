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
    env = (
        (json.loads(settings.read_text(encoding="utf-8")).get("env") or {})
        if settings.exists()
        else {}
    )
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


SHARED_MODEL_CACHE = os.environ.get("AB_MODEL_CACHE", "/tmp/AB3/model-cache")


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
        # ASR/OCR weights are not a cache: whisper-large-v3-mlx alone is 2.9 GB.
        # Leaving them under the per-profile XDG_CACHE_HOME made every one of the
        # 20 profiles a fresh 3 GB download, which is both wasteful and a wall
        # clock penalty that has nothing to do with the task. Weights live in one
        # host directory; only per-run material stays isolated per profile.
        models = Path(SHARED_MODEL_CACHE)
        models.mkdir(parents=True, exist_ok=True)
        env["HF_HOME"] = str(models)
        env["HUGGINGFACE_HUB_CACHE"] = str(models / "hub")
        env["TORCH_HOME"] = str(models / "torch")
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


def prepare_profile(
    root: Path, package_id: str, arm: str, *, token: str, gateway: str, keep: bool = False
) -> Path:
    """Create an isolated Claude Code profile for one package and one arm.

    ``keep=True`` reuses an existing profile in place. That is required for a
    resumed package: wiping the profile would delete the transcript the next
    ``--resume`` needs and the turn would silently start a fresh session.
    """
    profile = root / "profiles" / f"{package_id}" / arm
    if profile.exists() and not keep:
        shutil.rmtree(profile)
    for name in ("home", "cache", "config", "tmp", "workdir", "claude-config"):
        (profile / name).mkdir(parents=True, exist_ok=True)
    envoy_home = Path(os.path.expanduser("~/.envoy"))
    if arm == "envoy" and (envoy_home / "asr.json").exists():
        (profile / "home" / ".envoy").mkdir(parents=True, exist_ok=True)
        shutil.copy2(envoy_home / "asr.json", profile / "home" / ".envoy" / "asr.json")
    write_settings(profile, gateway, with_envoy=(arm == "envoy"))
    installed = arm == "envoy" and any(
        "envoy@envoy" in path.read_text(encoding="utf-8", errors="ignore")
        for path in (profile / "claude-config").rglob("*")
        if path.is_file() and path.stat().st_size < 2_000_000
    )
    if arm == "envoy" and not installed:
        install_envoy(profile, token, gateway)
    return profile


def verify_isolation(profile: Path, arm: str) -> None:
    config = profile / "claude-config"
    # Only configuration surfaces count. Transcripts and session state are the
    # *result* of a run: a baseline answer that quotes a page mentioning Envoy,
    # or a resumed profile that still holds yesterday's transcript, would trip
    # the check without any Envoy capability being present.
    skip = ("projects", "sessions", "session-env", "history", "todos", "logs", "shell-snapshots", "statsig", "file-history")
    blobs = []
    for path in config.rglob("*"):
        if not path.is_file() or path.stat().st_size >= 2_000_000:
            continue
        if path.suffix == ".jsonl" or any(part in skip for part in path.parts):
            continue
        try:
            blobs.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    text = "\n".join(blobs).lower()
    if arm != "envoy":
        if "envoy" in text or "envoy-cli" in text or "mcp__envoy" in text:
            raise SystemExit(f"arm isolation broken: baseline profile mentions envoy in {profile}")
        if (profile / "home" / ".envoy").exists():
            raise SystemExit(f"arm isolation broken: {arm} profile has an envoy home")
    else:
        if "envoy@envoy" not in text and not any(
            "envoy" in str(path).lower() for path in config.rglob("*")
        ):
            raise SystemExit(
                f"arm isolation broken: envoy profile is missing the plugin in {profile}"
            )


FFMPEG_ARM_HINT = """

This machine has no reader plugin. `yt-dlp` and `ffmpeg`/`ffprobe` are on PATH:
download the media yourself and inspect it (extract frames to look at, pull the
audio track to transcribe) if that is how you would answer without a reader."""


def turn_prompt(draft: dict, role: str, arm: str) -> str:
    urls = [revision["url"] for revision in draft["source_revisions"].values()]
    text = next(turn["text"] for turn in draft["turns"] if turn["turn_id"] == role)
    body = f"{text}\n\nDeliverable: {draft['deliverable']}\n\nSources:\n" + "\n".join(
        f"- {url}" for url in urls
    )
    return body + FFMPEG_ARM_HINT if arm == "ffmpeg" else body


def wait_for_gateway(gateway: str, seconds: int = 180) -> None:
    """Block until the inference gateway accepts connections.

    A turn started while the local proxy is restarting burns its whole timeout and
    records "Unable to connect to API (ConnectionRefused)", which says nothing
    about the arm under test. Wait for the socket instead of measuring the outage.
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + seconds
    probe = gateway.rstrip("/") + "/health/liveliness"
    while True:
        try:
            with urllib.request.urlopen(probe, timeout=10) as response:
                response.read(64)
                return
        except urllib.error.HTTPError:
            return  # an answer, even an error page, means the listener is up
        except Exception:
            if time.time() >= deadline:
                return  # let the turn record its own failure
            time.sleep(5)


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
    session_uuid: str | None = None,
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
    session_uuid = session_uuid or str(uuid.uuid5(uuid.NAMESPACE_URL, metadata["session_id"]))
    wait_for_gateway(gateway)
    command = [
        "claude",
        "-p",
        turn_prompt(draft, role, metadata["arm"]),
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
        # Which wall-clock cap this row was cut with. The arms do not share one
        # cap any more (the baseline keeps the 900 s control limit, the envoy arm
        # gets 2400 s for long video), so a "blocked" row is only interpretable
        # next to the cap that produced it.
        "turn_timeout_seconds": timeout,
        "is_error": bool(payload.get("is_error")) or code != 0,
        "reported_model": payload.get("model") or usage.get("model") or "",
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "result_chars": len(payload.get("result") or ""),
        "result_sha256": hashlib.sha256((payload.get("result") or "").encode("utf-8")).hexdigest(),
        "answer": payload.get("result") or "",
        "stderr_tail": (stderr or "")[-600:],
        "stdout_tail": (stdout or "")[-600:],
        "started_at": now_iso(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--roles",
        default="",
        help="comma list of turn roles to run; empty runs all three. Used to keep "
        "an extra arm small instead of paying for the whole chain.",
    )
    parser.add_argument("--drafts", type=Path, default=FIXED_SET / "task-drafts")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--model", default="anthropic/DeepSeek-V4.1-Flash")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument(
        "--envoy-timeout",
        type=int,
        default=0,
        help=(
            "wall-clock cap for the envoy arm when it differs from --timeout. "
            "A long video analysis is not a hung turn: cutting it at the "
            "baseline cap measures the cap, not the tool. 0 keeps one cap for "
            "both arms."
        ),
    )
    parser.add_argument(
        "--retry-capped",
        action="store_true",
        help="treat a 900 s / capped row as unfinished so it runs again (use after raising a cap)",
    )
    parser.add_argument("--limit", type=int, default=0, help="run at most N packages (smoke test)")
    parser.add_argument("--package", action="append", default=[], help="only these package ids")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip turns already present in <out>/run-log.jsonl (same run id)",
    )
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
    # Resume: a turn already recorded for this run is never executed twice, so a
    # shard that was killed mid-package continues where it stopped instead of
    # appending duplicate rows for the same (package, arm, role).
    done: set[tuple[str, str, str]] = set()
    attempts: dict[tuple[str, str, str], int] = {}
    session_for: dict[tuple[str, str], str] = {}
    if args.resume and (root / "run-log.jsonl").is_file():
        for line in (root / "run-log.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["package_id"], row["arm"], row["role"])
            attempts[key] = attempts.get(key, 0) + 1
            # A turn is final only when the CLI finished (0) or hit the wall-clock
            # cap (124, recorded as blocked). Any other exit code is an
            # infrastructure failure - a dead gateway returns 1 in ~3 minutes,
            # and a session id left behind by the interrupted attempt makes the
            # CLI answer "Session ID ... is already in use" in 0.4 s - and that
            # key must be attempted again instead of counted.
            # A 0 with an empty result is not a usable turn: the CLI stopped
            # after a tool call without writing a final message, or the answer
            # was an injected "API Error"/"Request timed out" line. Those keys
            # stay open so --resume retries them instead of counting them as
            # completed evidence.
            capped = int(row.get("exit_code") or 0) == 124
            usable = (not capped or not args.retry_capped) and (
                capped or int(row.get("result_chars") or 0) > 0
            )
            if int(row.get("exit_code") or 0) in {0, 124} and usable:
                done.add(key)
                if row.get("session_uuid"):
                    session_for[(row["package_id"], row["arm"])] = row["session_uuid"]
    answers = (root / "answers.jsonl").open("a", encoding="utf-8")
    runlog = (root / "run-log.jsonl").open("a", encoding="utf-8")

    for item in packages:
        draft = drafts[item["package_id"]]
        for arm in item["arms"]:
            with_envoy = arm == "envoy"
            profile = prepare_profile(
                root,
                item["package_id"],
                arm,
                token=token,
                gateway=args.gateway,
                keep=args.resume,
            )
            verify_isolation(profile, arm)
            session_id = item["session_ids"][arm]
            roles = [r.strip() for r in args.roles.split(",") if r.strip()] or list(TURN_ROLES)
            for role in roles:
                if (item["package_id"], arm, role) in done:
                    print(f"{item['package_id']}/{arm}/{role}: already recorded, skipped")
                    continue
                base_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, session_id))
                if role == "initial":
                    # Claude Code refuses a session id that already exists in the
                    # profile ("Session ID ... is already in use") and it tracks
                    # ids across `projects/`, `sessions/` and `session-env/`, so a
                    # killed attempt leaves the id unusable. Any deterministic id
                    # therefore collides after the first interruption; the id is
                    # recorded in the run log, so it does not need to be derivable.
                    session_uuid = str(uuid.uuid4())
                else:
                    # Resume only works against a session that actually exists.
                    # Without a completed initial row the deterministic id was
                    # usually created by the interrupted attempt, and reusing it
                    # is exactly what makes the CLI answer "already in use".
                    session_uuid = session_for.get((item["package_id"], arm)) or str(uuid.uuid4())
                metadata = {
                    "run_id": run_id,
                    "package_id": item["package_id"],
                    "arm": arm,
                    "session_id": session_id,
                    "turn_correlation_id": item["turn_correlation_ids"][arm][role],
                }
                transient = (
                    "Connection closed mid-response",
                    "ConnectionRefused",
                    "Unable to connect",
                    "API Error",
                    "Request timed out",
                    "overloaded_error",
                    "socket hang up",
                    "ECONNRESET",
                    "rate_limit",
                    "is already in use",
                )
                record = None
                for attempt in range(3):
                    if role == "initial" and attempt:
                        # A failed initial turn may still have registered its
                        # session id, so the retry needs an id of its own.
                        session_uuid = str(uuid.uuid4())
                    record = run_turn(
                        profile,
                        draft,
                        role,
                        token=token,
                        gateway=args.gateway,
                        with_envoy=with_envoy,
                        model=args.model,
                        timeout=(args.envoy_timeout or args.timeout) if with_envoy else args.timeout,
                        metadata=metadata,
                        session_uuid=session_uuid,
                    )
                    if int(record["exit_code"]) == 124:
                        break
                    if int(record["exit_code"]) == 0 and int(record.get("result_chars") or 0) > 0:
                        break
                    haystack = "".join(
                        str(record.get(field) or "")
                        for field in ("answer", "stderr_tail")
                    )
                    # An exit 0 with no final text is retried the same way as a
                    # transport error: nothing usable was produced, so the turn
                    # is not evidence yet.
                    empty_result = int(record.get("exit_code") or 0) == 0 and int(
                        record.get("result_chars") or 0
                    ) == 0
                    # A resumed turn can hit "Session ID ... is already in use"
                    # while the previous CLI process is still releasing it. The
                    # same id works again a moment later, so the retry waits
                    # instead of burning the attempt immediately.
                    if "is already in use" in haystack and attempt < 2:
                        print(
                            f"{item['package_id']}/{arm}/{role}: session id still held, "
                            f"waiting before retry",
                            flush=True,
                        )
                        time.sleep(8)
                        continue
                    if not empty_result and not any(marker in haystack for marker in transient):
                        break
                    print(
                        f"{item['package_id']}/{arm}/{role}: attempt {attempt + 1} hit an "
                        f"infrastructure error, retrying",
                        flush=True,
                    )
                if role == "initial":
                    session_for[(item["package_id"], arm)] = session_uuid
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
