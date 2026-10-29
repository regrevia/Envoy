from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from envoy_core import __version__ as core_version
from envoy_core.schemas.read_v1 import (
    ExpandRequest,
    LocalVideoReadRequest,
    MaterialReplayRequest,
    MaterialSearchRequest,
    ReadRequest,
    VisualObservationRequest,
)

from .capabilities import capability_catalog
from . import browser as browser_runtime
from . import cache as cache_storage
from . import install_state
from . import storage as state_storage
from .build_info import BUILD_CHANNEL, BUILD_DIRTY, BUILD_REVISION, BUILD_VERSION
from .hooks import SUPPORTED_HOSTS, install_codex_hooks, run_prompt_hook, hook_status, render_hook_status
from .cookies import cookies_status
from .media import (
    MediaCapabilityMissing,
    cached_asr_models,
    media_runtime_status,
    prepare_asr_model,
    selected_asr_model,
)
from .ocr import (
    DEFAULT_OCR_BACKEND,
    OCR_PROFILES,
    available_ocr_backends,
    extract_image_ocr,
    ocr_runtime_status,
)
from .package_install import install_media_dependencies
from . import __version__
from . import mcp
from .collect import collect
from .collections import (
    CollectionBudget,
    CollectionError,
    create_collection,
    discover_profile,
    get_collection,
    list_collections,
    update_collection,
)
from .local_video import read_local_video
from .reader import (
    PublicPageError,
    expand_material,
    replay_material,
    read_public_page,
    record_visual_observation,
    search_material,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="envoy", description="Read public links into Envoy materials")
    parser.add_argument("--version", action="version", version=f"envoy-cli {__version__} (core {core_version})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read = subparsers.add_parser("read", help="read a public link")
    read.add_argument("--request-json", required=True, help="read an envoy.read/1 request from this path or '-' for stdin")

    local_video = subparsers.add_parser("read-local-video", help="transcribe and inspect a local video file")
    local_video.add_argument(
        "--request-json",
        required=True,
        help="read an envoy.read/1 local-video request from this path or '-' for stdin",
    )

    observe = subparsers.add_parser(
        "observe-visual",
        help="persist one host inspection observation for a delivered visual resource",
    )
    observe.add_argument(
        "--request-json",
        required=True,
        help="read an envoy.observe/1 request from this path or '-' for stdin",
    )

    expand = subparsers.add_parser("expand", help="read the next range from a local material")
    expand.add_argument("--request-json", required=True, help="read an expansion request from this path or '-' for stdin")

    replay = subparsers.add_parser("replay", help="replay retained evidence into a new host session")
    replay.add_argument("--request-json", required=True, help="read an envoy.read/1 replay request from this path or '-' for stdin")

    search = subparsers.add_parser("search", help="find relevant passages in a local material")
    search.add_argument("--request-json", required=True, help="read an envoy.search/1 request from this path or '-' for stdin")

    discover = subparsers.add_parser("discover", help="discover bounded public Douyin, Bilibili or YouTube playlist candidates")
    discover.add_argument("--url", required=True)
    discover.add_argument("--limit", type=int, default=20)
    discover.add_argument("--cursor")
    discover.add_argument("--max-work-seconds", type=int, default=60)
    checkpoint = subparsers.add_parser(
        "collection-checkpoint",
        help="create, read, or advance a bounded collection run",
    )
    checkpoint.add_argument(
        "--request-json",
        required=True,
        help="read one collection checkpoint request from this path or '-' for stdin",
    )

    collect_parser = subparsers.add_parser(
        "collect",
        help="enumerate a Douyin, Bilibili or YouTube playlist into one Markdown source pack",
    )
    collect_parser.add_argument("--url")
    collect_parser.add_argument("--out")
    collect_parser.add_argument(
        "--list",
        action="store_true",
        help="list resumable collection runs and their progress",
    )
    collect_parser.add_argument("--limit", type=int, default=10)
    collect_parser.add_argument("--goal", default="")
    collect_parser.add_argument("--collection-id")
    collect_parser.add_argument("--run-id")
    collect_parser.add_argument("--max-work-seconds", type=int, default=120)
    collect_parser.add_argument("--retry-blocked", action="store_true")
    collect_parser.add_argument(
        "--retry-reason",
        default=None,
        help="retry only unfinished items whose reason contains this word (for example auth)",
    )
    collect_parser.add_argument("--json", action="store_true")

    doctor = subparsers.add_parser("doctor", help="check the independent Skill runtime")
    doctor.add_argument("--json", action="store_true", help="print machine-readable output")
    doctor.add_argument(
        "--verify-asr",
        action="store_true",
        help="verify the selected checkpoint and run a short Whisper inference (may take time and memory)",
    )
    browser = subparsers.add_parser("browser", help="manage the isolated browser runtime")
    browser_commands = browser.add_subparsers(dest="browser_command", required=True)
    browser_commands.add_parser("setup", help="install the isolated Chromium browser")
    browser_prune = browser_commands.add_parser(
        "prune", help="delete browser downloads the installed Playwright no longer uses"
    )
    browser_prune.add_argument("--dry-run", action="store_true", help="report without deleting")
    browser_prune.add_argument("--json", action="store_true")
    asr = subparsers.add_parser("asr", help="prepare local speech recognition")
    asr_commands = asr.add_subparsers(dest="asr_command", required=True)
    asr_setup = asr_commands.add_parser("setup", help="download and verify one local Whisper model")
    asr_setup.add_argument(
        "--model",
        default="base",
        choices=[
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large", "large-v1", "large-v2",
            "large-v3", "large-v3-turbo",
        ],
    )
    asr_status = asr_commands.add_parser("status", help="show which local Whisper model reads use")
    asr_status.add_argument("--json", action="store_true", help="print machine-readable output")
    asr_install = asr_commands.add_parser(
        "install",
        help="install Whisper and its large dependencies using the fastest reachable package index",
    )
    asr_install.add_argument(
        "--model",
        default="base",
        choices=[
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large", "large-v1", "large-v2",
            "large-v3", "large-v3-turbo",
        ],
    )
    hook = subparsers.add_parser("hook", help="run a host integration hook")
    hook_commands = hook.add_subparsers(dest="hook_command", required=True)
    hook_prompt = hook_commands.add_parser(
        "prompt",
        help="inject Envoy link routing when a user prompt contains a public URL",
    )
    hook_prompt.add_argument("--host", required=True, choices=SUPPORTED_HOSTS)
    hook_prompt.add_argument(
        "--language",
        default=None,
        choices=("auto", "zh", "en"),
        help="routing notice language (default: prompt auto-detection)",
    )
    hook_install = hook_commands.add_parser(
        "install",
        help="install or remove a host hook file (Codex user-level hooks)",
    )
    hook_install.add_argument("--host", default="codex", choices=("codex",))
    hook_install.add_argument("--uninstall", action="store_true")
    hook_status_parser = hook_commands.add_parser(
        "status",
        help="report whether a host hook is installed and trusted",
    )
    hook_status_parser.add_argument("--host", default="codex", choices=SUPPORTED_HOSTS)
    hook_status_parser.add_argument("--json", action="store_true")
    ocr = subparsers.add_parser("ocr", help="inspect or exercise local OCR backends")
    ocr_commands = ocr.add_subparsers(dest="ocr_command", required=True)
    ocr_status = ocr_commands.add_parser("status", help="report OCR backend availability")
    ocr_status.add_argument("--json", action="store_true")
    ocr_test = ocr_commands.add_parser("test", help="OCR one local image and print the result")
    ocr_test.add_argument("image")
    ocr_test.add_argument("--backend", default=DEFAULT_OCR_BACKEND)
    ocr_test.add_argument("--language", default="auto")
    ocr_test.add_argument("--profile", default="balanced", choices=sorted(OCR_PROFILES))
    cache = subparsers.add_parser("cache", help="report or reclaim local material storage")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    cache_status = cache_commands.add_parser(
        "status",
        help="report stored materials, caches and their disk usage",
    )
    cache_status.add_argument("--json", action="store_true")
    cache_prune = cache_commands.add_parser(
        "prune",
        help="delete expired materials, orphaned frames, OCR caches and spent ledgers",
    )
    cache_prune.add_argument("--dry-run", action="store_true", help="report without deleting")
    cache_prune.add_argument("--json", action="store_true")
    auth = subparsers.add_parser(
        "auth",
        help="copy a local browser's session for a site that hides content behind a login",
    )
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_import = auth_commands.add_parser(
        "import",
        help="copy cookies for one domain from a local browser",
    )
    auth_import.add_argument(
        "--from",
        dest="source",
        required=True,
        choices=("chrome", "edge", "brave", "vivaldi", "chromium", "safari"),
    )
    auth_import.add_argument(
        "--domain",
        action="append",
        default=[],
        help="domain to copy, repeatable (for example douyin.com)",
    )
    auth_import.add_argument("--url", default=None, help="copy cookies for this link's site")
    auth_import.add_argument("--json", action="store_true")
    auth_status = auth_commands.add_parser(
        "status", help="report imported cookies and pauses waiting for a person"
    )
    auth_status.add_argument("--json", action="store_true")
    auth_handoff = auth_commands.add_parser(
        "handoff",
        help="turn the opt-in credential handoff on or off (off by default)",
    )
    auth_handoff.add_argument(
        "state",
        nargs="?",
        default="status",
        choices=("status", "on", "off"),
        help="status (default), on, or off",
    )
    auth_handoff.add_argument("--json", action="store_true")
    auth_open = auth_commands.add_parser(
        "open",
        help="open one visible window so a person can sign in, then check the link",
    )
    auth_open.add_argument("--action-id", default=None)
    auth_open.add_argument("--url", default=None)
    auth_open.add_argument("--timeout", type=int, default=300)
    auth_open.add_argument("--json", action="store_true")
    auth_shot = auth_commands.add_parser(
        "shot",
        help="open the sign-in page in a background browser and save a picture of it",
    )
    auth_shot.add_argument("--action-id", default=None)
    auth_shot.add_argument("--url", default=None)
    auth_shot.add_argument("--timeout", type=int, default=300)
    auth_shot.add_argument("--visible", action="store_true")
    auth_shot.add_argument("--json", action="store_true")
    auth_verify = auth_commands.add_parser(
        "verify", help="check whether the original link is readable now"
    )
    auth_verify.add_argument("--action-id", default=None)
    auth_verify.add_argument("--url", default=None)
    auth_verify.add_argument("--json", action="store_true")
    auth_resume = auth_commands.add_parser(
        "resume", help="pick up a paused task from its resume token"
    )
    auth_resume.add_argument("--token", required=True)
    auth_resume.add_argument("--json", action="store_true")
    auth_clear = auth_commands.add_parser(
        "clear", help="delete the login state Envoy saved for a site"
    )
    auth_clear.add_argument("--host", default=None)
    auth_clear.add_argument("--all", action="store_true")
    auth_clear.add_argument("--json", action="store_true")
    mcp_parser = subparsers.add_parser("mcp", help="run the stdio MCP bridge")
    mcp_parser.add_argument(
        "--toolset",
        choices=mcp.TOOLSET_NAMES,
        default=None,
        help=(
            "static tool declarations to expose: core (read/expand/search/"
            "observe_visual) or full (all tools). Default: core, or "
            "ENVOY_MCP_TOOLSET when set to full."
        ),
    )
    return parser


def _auth_action_for(args):
    from . import authflow

    action_id = getattr(args, "action_id", None)
    if action_id:
        return authflow.load_auth_action(action_id)
    url = getattr(args, "url", None)
    if url:
        return authflow.create_auth_action(url)
    return None


def _emit(payload: dict, as_json: bool, lines: list[str]) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for line in lines:
            print(line)


def _run_auth_shot(args) -> int:
    from . import auth_session

    url = getattr(args, "url", None)
    action_id = getattr(args, "action_id", None)
    if not action_id and not url:
        print("envoy auth shot: pass --action-id or --url", file=sys.stderr)
        return 2
    try:
        payload = auth_session.start(
            str(url or ""),
            action_id=action_id or None,
            timeout_seconds=int(args.timeout),
            visible=bool(getattr(args, "visible", False)),
        )
    except (ValueError, RuntimeError) as exc:
        print(f"envoy auth shot: {exc}", file=sys.stderr)
        return 2
    lines = [
        f"{payload.get('host') or payload.get('source_url')}: sign-in page saved as a picture",
        f"picture: {payload.get('screen_path') or '(waiting for the first frame)'}",
        f"the browser stays in the background until {payload.get('timeout_seconds')}s pass",
        f"next: envoy auth verify --action-id {payload.get('action_id')} after the person signs in",
    ]
    _emit(payload, bool(getattr(args, "json", False)), lines)
    return 0 if payload.get("screen_path") else 1


def _run_auth_open(args) -> int:
    from . import authflow, auth_window

    action = _auth_action_for(args)
    if not action:
        print("envoy auth open: pass --action-id or --url", file=sys.stderr)
        return 2
    if action.get("status") == "canceled":
        print("envoy auth open: this request was canceled", file=sys.stderr)
        return 2
    authflow.update_auth_action(
        action["action_id"], status="verifying", attempts=int(action.get("attempts") or 0) + 1
    )
    print(f"Opening {action.get('host') or action['source_url']} in a browser window…")
    print("Sign in there. Envoy checks the original link when the window closes or the wait ends.")
    try:
        window = auth_window.open_login_window(
            str(action["source_url"]), timeout_seconds=int(args.timeout)
        )
    except auth_window.AuthWindowError as exc:
        authflow.update_auth_action(action["action_id"], status="failed")
        print(f"envoy auth open: {exc}", file=sys.stderr)
        return 2
    verification = auth_window.verify_source(str(action["source_url"]))
    updated = authflow.record_verification(action["action_id"], verification) or action
    payload = {
        "action_id": updated["action_id"],
        "status": updated.get("status"),
        "window": window,
        "verification": verification,
        "resume_token": updated.get("resume_token"),
    }
    lines = [
        f"{action.get('host') or action['source_url']}: {verification.get('reason')}",
    ]
    if verification.get("headless_reusable"):
        lines.append("The link is readable again. Continue the paused task.")
    elif verification.get("page_access"):
        lines.append("The page loads, but the media is still missing. Try a different sign-in or another source.")
    else:
        lines.append("Still blocked. Nothing was resumed.")
    _emit(payload, bool(args.json), lines)
    return 0 if verification.get("headless_reusable") else 1


def _run_auth_verify(args) -> int:
    from . import authflow, auth_window

    action = _auth_action_for(args)
    if not action:
        print("envoy auth verify: pass --action-id or --url", file=sys.stderr)
        return 2
    verification = auth_window.verify_source(str(action["source_url"]))
    updated = authflow.record_verification(action["action_id"], verification) or action
    payload = {
        "action_id": updated["action_id"],
        "status": updated.get("status"),
        "verification": verification,
        "resume_token": updated.get("resume_token"),
    }
    _emit(
        payload,
        bool(args.json),
        [f"{verification.get('reason')} (status {updated.get('status')})"],
    )
    return 0 if verification.get("headless_reusable") else 1


def _run_auth_resume(args) -> int:
    from . import authflow, auth_window

    action = authflow.resolve_resume_token(args.token)
    if not action:
        print("envoy auth resume: unknown token", file=sys.stderr)
        return 2
    verification = auth_window.verify_source(str(action["source_url"]))
    updated = authflow.record_verification(action["action_id"], verification) or action
    payload = {
        "action_id": updated["action_id"],
        "status": updated.get("status"),
        "source_url": updated.get("source_url"),
        "purpose": updated.get("purpose"),
        "verification": verification,
        "resume_token": updated.get("resume_token"),
    }
    _emit(
        payload,
        bool(args.json),
        [
            f"{updated.get('host')}: {verification.get('reason')}",
            f"Resume the task for {updated.get('source_url')}.",
        ],
    )
    return 0 if verification.get("headless_reusable") else 1


def _run_browser_prune(args) -> int:
    report = browser_runtime.prune_browser_versions(dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if report["path"] is None:
        print("browser   no Playwright cache on this host")
        return 0
    verb = "would remove" if report["dry_run"] else "removed"
    if not report["removed"]:
        print(f"browser   nothing stale in {report['path']}")
        return 0
    for item in report["removed"]:
        print(f"browser   {verb} {item['name']} ({item['bytes'] / 1_048_576:.0f} MB)")
    print(f"browser   {verb} {len(report['removed'])} download(s), {report['freed_mb']} MB")
    return 0


def _run_auth(args) -> int:
    from . import cookies as cookie_store

    if args.auth_command == "shot":
        return _run_auth_shot(args)
    if args.auth_command == "open":
        return _run_auth_open(args)
    if args.auth_command == "verify":
        return _run_auth_verify(args)
    if args.auth_command == "resume":
        return _run_auth_resume(args)
    if args.auth_command == "import":
        try:
            report = cookie_store.import_cookies(args.source, args.domain, args.url)
        except cookie_store.CookieImportError as exc:
            print(f"envoy auth import: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"Imported {report['imported']} cookies for "
                f"{', '.join(report['domains'])} from {report['source']}"
            )
        return 0
    if args.auth_command == "handoff":
        from . import features

        if args.state in ("on", "off"):
            report = features.set_credential_handoff(args.state == "on")
        else:
            report = features.credential_handoff_status()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            state = "on" if report["enabled"] else "off"
            print(f"Credential handoff: {state} (source: {report['source']})")
            if report["enabled"]:
                print(
                    "Sign-in pages can be opened for the user in the chat and a "
                    "browser session can be reused; nothing leaves this machine."
                )
            else:
                print(
                    "Off: no sign-in page is opened and no stored session is used. "
                    "Turn on with `envoy auth handoff on`."
                )
            print(f"config: {report['config_path']}")
        return 0
    if args.auth_command == "status":
        from . import authflow, profile_state

        report = cookie_store.cookies_status()
        report["profile"] = profile_state.profile_state_status()
        report["pauses"] = authflow.auth_status()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            if report.get("imported"):
                print(
                    f"{report['imported']} imported cookies for "
                    f"{', '.join(report['domains'] or [])} from {report['source']}"
                )
            else:
                print("No imported cookies. Run `envoy auth import --from chrome --domain <site>`.")
            profile = report["profile"]
            print(
                f"Saved sign-in profile: {profile['cookie_rows']} cookie rows at {profile['profile']}"
            )
            for pause in report["pauses"]["pending"]:
                print(
                    f"Waiting on a person: {pause.get('host')} "
                    f"({pause['action_id']}) → envoy auth open --action-id {pause['action_id']}"
                )
            if not report["pauses"]["pending"]:
                print("No pauses waiting on a person.")
        return 0

    from . import authflow, profile_state

    target = getattr(args, "host", None)
    if target:
        result = profile_state.clear_site_cookies(target)
    else:
        result = profile_state.clear_profile_state()
    removed_cookies = cookie_store.clear_imported_cookies()
    invalidated = authflow.invalidate_actions(host=target)
    payload = {
        **result,
        "imported_cookies_removed": removed_cookies,
        "pauses_invalidated": invalidated,
    }
    _emit(
        payload,
        bool(getattr(args, "json", False)),
        [
            f"Deleted Envoy's saved sign-in for {target}" if target
            else "Deleted the saved sign-in profile Envoy kept locally",
            f"Pauses invalidated: {invalidated}",
        ],
    )
    return 0


def main(argv: Optional[list[str]] = None, stdin: Optional[str] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "discover":
        try:
            print(json.dumps(discover_profile(
                args.url,
                limit=args.limit,
                cursor=args.cursor,
                max_work_seconds=args.max_work_seconds,
            ), ensure_ascii=False))
            return 0
        except CollectionError as exc:
            print(json.dumps({
                "result": "blocked",
                "error": {"code": "source_unavailable", "message": str(exc)},
            }, ensure_ascii=False))
            return 2
    if args.command == "collection-checkpoint":
        try:
            if args.request_json == "-":
                raw = stdin if stdin is not None else sys.stdin.read()
            else:
                with open(args.request_json, "r", encoding="utf-8") as request_file:
                    raw = request_file.read()
            payload = json.loads(raw)
            action = str(payload.get("action") or "")
            collection_id = str(payload.get("collection_id") or "")
            run_id = str(payload.get("run_id") or "")
            budget_data = payload.get("budget") or {}
            if action == "create":
                data = create_collection(
                    collection_id,
                    run_id,
                    str(payload.get("profile_url") or ""),
                    operation_id=str(payload.get("operation_id") or ""),
                    budget=CollectionBudget(**budget_data) if budget_data else None,
                )
            elif action == "get":
                data = get_collection(collection_id, run_id)
            else:
                if payload.get("expected_revision") is None or not payload.get("operation_id"):
                    raise CollectionError(
                        "Mutations require expected_revision and operation_id"
                    )
                data = update_collection(
                    collection_id,
                    run_id=run_id,
                    expected_revision=int(payload["expected_revision"]),
                    operation_id=str(payload["operation_id"]),
                    action=action,
                    candidate_ids=payload.get("candidate_ids"),
                    candidate_id_value=payload.get("candidate_id"),
                    item_state=payload.get("item_state"),
                    material_id=payload.get("material_id"),
                    source_revision=payload.get("source_revision"),
                    expires_at=payload.get("expires_at"),
                    evidence_available=payload.get("evidence_available"),
                    pending_resource_ids=payload.get("pending_resource_ids"),
                    observed_resource_ids=payload.get("observed_resource_ids"),
                    usage=payload.get("usage"),
                    budget=CollectionBudget(**budget_data) if budget_data else None,
                )
            print(json.dumps(
                {"tool": "envoy_collection_checkpoint", "action": action, **data},
                ensure_ascii=False,
            ))
            return 0
        except (CollectionError, ValueError) as exc:
            print(json.dumps({
                "result": "blocked",
                "error": {"code": "collection_error", "message": str(exc)},
            }, ensure_ascii=False))
            return 2
    if args.command == "collect" and args.list:
        runs = list_collections()
        if args.json:
            print(json.dumps({"runs": runs}, ensure_ascii=False))
        elif not runs:
            print("没有可续跑的采集任务")
        else:
            for run in runs:
                print(
                    f"{run['collection_id']} · {run['platform']} · "
                    f"{run['completed']}/{run['selected']} 完成 · "
                    f"{run['blocked']} 待登录 · {run['failed']} 失败 · {run['updated_at']}"
                )
            print("续跑：envoy collect --url <profile> --out <文件> --retry-reason auth")
        return 0
    if args.command == "collect":
        if not args.url or not args.out:
            print(json.dumps({
                "result": "blocked",
                "error": {"code": "usage", "message": "collect needs --url and --out, or --list"},
            }, ensure_ascii=False))
            return 2
        try:
            summary = collect(
                args.url,
                args.out,
                limit=args.limit,
                goal=args.goal,
                collection_id=args.collection_id,
                run_id=args.run_id,
                max_work_seconds=args.max_work_seconds,
                retry_blocked=args.retry_blocked,
                retry_reason=args.retry_reason,
            )
        except CollectionError as exc:
            print(json.dumps({
                "result": "blocked",
                "error": {"code": "collection_error", "message": str(exc)},
            }, ensure_ascii=False))
            return 2
        if args.json:
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(f"Wrote {summary['output']}")
            print(
                f"{summary['platform']} · 已完成 {summary['completed']}/{summary['selected']}"
                f" · 本次读取 {summary['read_now']} · 跳过 {summary['skipped']}"
            )
            if summary["pending"]:
                print(f"未完成 {len(summary['pending'])} 条，状态保存在 {summary['sources']}")
            print(f"续跑：envoy collect --url {summary['profile_url']} --out {summary['output']}")
        return 0
    if args.command == "doctor":
        runtime = media_runtime_status(verify_asr=args.verify_asr)
        runtime["browser"] = browser_runtime.browser_runtime_status()
        runtime["ocr"] = available_ocr_backends()
        runtime["install"] = install_state.install_report()
        result = {
            "tool": "envoy",
            "cli_version": __version__,
            "core_version": core_version,
            "build": {
                "channel": BUILD_CHANNEL,
                "version": BUILD_VERSION,
                "source_revision": BUILD_REVISION,
                "source_dirty": BUILD_DIRTY,
                **(
                    {"source_checkout": str(Path(__file__).resolve().parents[2])}
                    if BUILD_CHANNEL == "source-checkout"
                    else {}
                ),
            },
            "protocol_version": "envoy.read/1",
            "network_reader": "public_static_and_supported_media_extractors",
            "credential_access": "isolated_envoy_browser_profile_only",
            "website_backend_required": False,
            "capabilities": capability_catalog(),
            "runtime": runtime,
        }
        print(
            json.dumps(result, ensure_ascii=False, indent=2)
            if args.json
            else _doctor_text(runtime)
        )
        if args.verify_asr and runtime["asr_operational"] != "verified":
            return 2
        return 0
    if args.command == "cache":
        if args.cache_command == "status":
            report = cache_storage.cache_status()
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(cache_storage.render_status(report))
            return 0
        report = cache_storage.prune_cache(dry_run=args.dry_run, home=cache_storage.envoy_home())
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(cache_storage.render_prune(report))
        return 0
    if args.command == "auth":
        return _run_auth(args)
    if args.command == "browser":
        if getattr(args, "browser_command", None) == "prune":
            return _run_browser_prune(args)
        return browser_runtime.setup_browser()
    if args.command == "mcp":
        return mcp.main(toolset=args.toolset)
    if args.command == "ocr":
        if args.ocr_command == "status":
            status = ocr_runtime_status()
            if args.json:
                print(json.dumps(status, ensure_ascii=False, indent=2))
            else:
                backend = status["auto_backend"] or "none"
                print(f"auto backend: {backend}")
                for name, info in status["backends"].items():
                    state = "available" if info["available"] else "missing"
                    print(f"  {name:<10} {state:<10} {info['engine_version'] or info['install_hint']}")
            return 0
        try:
            result = extract_image_ocr(
                args.image,
                profile=args.profile,
                backend=args.backend,
                language=args.language,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({
            "backend": result["backend"],
            "language": result["language"],
            "profile": result["profile"],
            "regions": len(result["regions"]),
            "text": result["text"],
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "hook":
        if args.hook_command == "status":
            report = hook_status(args.host)
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                print(render_hook_status(report))
            if report.get("error"):
                return 2
            return 1 if report.get("ok") is False else 0
        if args.hook_command == "install":
            install = install_codex_hooks(uninstall=args.uninstall)
            print(json.dumps(install, ensure_ascii=False))
            # A malformed ~/.codex/hooks.json is reported, not overwritten; make
            # that visible to scripts instead of exiting successfully.
            return 2 if install.get("error") else 0
        payload = stdin if stdin is not None else sys.stdin.read()
        return run_prompt_hook(args.host, payload, language=args.language)
    if args.command == "asr":
        try:
            if args.asr_command == "status":
                runtime = media_runtime_status()
                model = selected_asr_model()
                cached = cached_asr_models()
                payload = {
                    "model": model,
                    "cached": cached,
                    "engine": runtime.get("asr_engine"),
                    "ready": bool(runtime.get("asr_installed") and runtime.get("asr_model_available")),
                    "next_step": runtime.get("asr_next_step"),
                }
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                else:
                    lines = [
                        f"model      {model}",
                        f"engine     {payload['engine'] or 'missing: run envoy asr install'}",
                        f"cached     {', '.join(cached) or 'none'}",
                    ]
                    if not payload["ready"] and payload["next_step"]:
                        lines.append(f"next       {payload['next_step']}")
                    print("\n".join(lines))
                return 0 if payload["ready"] else 2
            if args.asr_command == "install":
                install = install_media_dependencies(model=args.model)
                result = {
                    **install,
                    **prepare_asr_model(args.model),
                }
                print(json.dumps({"status": "ready", **result}, ensure_ascii=False))
                return 0
            result = prepare_asr_model(args.model)
        except MediaCapabilityMissing as exc:
            print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
            return 2
        except Exception as exc:
            print(json.dumps({
                "status": "failed",
                "error": f"Could not prepare local ASR model ({type(exc).__name__}): {exc}",
            }, ensure_ascii=False))
            return 2
        print(json.dumps({"status": "ready", **result}, ensure_ascii=False))
        return 0
    try:
        if args.request_json == "-":
            payload = stdin if stdin is not None else sys.stdin.read()
        else:
            with open(args.request_json, "r", encoding="utf-8") as request_file:
                payload = request_file.read()
        if args.command == "expand":
            expand_request = ExpandRequest.model_validate_json(payload)
            response = expand_material(expand_request)
            operation_request = expand_request
        elif args.command == "replay":
            operation_request = MaterialReplayRequest.model_validate_json(payload)
            response = replay_material(operation_request)
        elif args.command == "search":
            operation_request = MaterialSearchRequest.model_validate_json(payload)
            response = search_material(operation_request)
        elif args.command == "read-local-video":
            operation_request = LocalVideoReadRequest.model_validate_json(payload)
            response = read_local_video(operation_request)
        elif args.command == "observe-visual":
            observation_request = VisualObservationRequest.model_validate_json(payload)
            source_url = (
                mcp.validate_observation_source_url(observation_request.source_url)
                if observation_request.source_url
                else None
            )
            persistence = record_visual_observation(
                observation_request.material_id,
                source_url,
                observation_request.resource_id,
                observation_request.source_block_id,
                observation_request.observation,
                observation_request.locator_quote,
                observation_request.session_id,
            )
            print(json.dumps({
                "protocol_version": "envoy.observe/1",
                "request_id": observation_request.request_id,
                "session_id": observation_request.session_id,
                "material_id": observation_request.material_id,
                "result": "ready" if persistence.get("persisted") else "blocked",
                "execution_state": "completed",
                "observation_persistence": persistence,
            }, ensure_ascii=False))
            return 0 if persistence.get("persisted") else 2
        else:
            request = ReadRequest.model_validate_json(payload)
            operation_request = request
            response = read_public_page(request)
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False))
        return 0 if response.result.value in {"ready", "partial"} else 2
    except PublicPageError as exc:
        print(json.dumps({
            "protocol_version": "envoy.read/1",
            "request_id": operation_request.request_id if "operation_request" in locals() else "read",
            "execution_state": "completed",
            "result": "blocked",
            "errors": [{
                "code": exc.code.value,
                "message": str(exc),
                **({"details": exc.details} if exc.details else {}),
            }],
        }, ensure_ascii=False))
        return 2
    except state_storage.StateUnavailable as exc:
        print(json.dumps({
            "error": "state_not_writable",
            "message": str(exc),
            "action_required": True,
            "actions": [{
                "kind": "set_writable_state_dir",
                "command": f"ENVOY_LOCAL_DATA_DIR={state_storage.fallback_root()} envoy read --request-json <file>",
                "effect": "Keeps retained material in a writable directory instead of failing the read",
            }],
        }, ensure_ascii=False))
        return 2
    except OSError as exc:
        if exc.errno in state_storage.UNUSABLE_ERRNOS:
            print(json.dumps({
                "error": "state_not_writable",
                "message": (
                    f"Envoy cannot write {exc.filename or state_storage.preferred_root()}: {exc.strerror or exc}. "
                    "Point ENVOY_LOCAL_DATA_DIR at a writable directory, or allow writes to the default location."
                ),
                "action_required": True,
                "actions": [{
                    "kind": "set_writable_state_dir",
                    "command": f"ENVOY_LOCAL_DATA_DIR={state_storage.fallback_root()} envoy read --request-json <file>",
                    "effect": "Keeps retained material in a writable directory instead of failing the read",
                }],
            }, ensure_ascii=False))
            return 2
        print(json.dumps({"error": "invalid_request", "message": str(exc)}, ensure_ascii=False))
        return 1
    except ValueError as exc:
        print(json.dumps({"error": "invalid_request", "message": str(exc)}, ensure_ascii=False))
        return 1


def entrypoint() -> None:
    raise SystemExit(main())


def _doctor_text(runtime: dict) -> str:
    lines = ["Envoy Skill ready"]
    install = runtime.get("install") or {}
    status = install.get("status")
    if status == install_state.STATUS_PINNED:
        lines.append(f"runtime    pinned: {install.get('pinned_runtime')}")
    elif status == install_state.STATUS_SHADOWED:
        lines.append(
            f"runtime    pinned {install.get('pinned_runtime')}; "
            f"PATH runs {install.get('path_envoy') or 'another envoy'}"
        )
        lines.append(f"           {install.get('next_action')}")
    elif install.get("next_action"):
        lines.append(f"runtime    {status.replace('_', ' ')}: {install.get('next_action')}")
    if runtime.get("asr_installed"):
        state = (
            "verified"
            if runtime.get("asr_model_verified")
            else "on disk, unverified"
            if runtime.get("asr_model_file_present")
            else "missing: run envoy asr setup"
        )
        lines.append(f"whisper    {runtime.get('asr_model')} ({state})")
    else:
        lines.append("whisper    missing: run envoy asr install")
    media_tools = [
        name for name in ("ffmpeg", "ffprobe") if runtime.get(name)
    ]
    missing_tools = [name for name in ("ffmpeg", "ffprobe") if not runtime.get(name)]
    lines.append(
        f"media      {', '.join(media_tools) or 'none'}"
        + (f"; missing {', '.join(missing_tools)}" if missing_tools else "")
    )
    browser = runtime.get("browser") or {}
    footprint = browser.get("disk") or {}
    total_mb = footprint.get("total_mb") or 0
    detail = f" ({total_mb:g} MB on disk)" if total_mb else ""
    lines.append(
        f"browser    {'ready' if browser.get('ready') else 'missing: run envoy browser setup'}"
        f"{detail if browser.get('ready') or total_mb else ''}"
    )
    imported = cookies_status().get("imported") or 0
    lines.append(
        f"cookies    {imported} imported"
        if imported
        else "cookies    none imported; envoy auth import --from chrome --url <link>"
    )
    return "\n".join(lines)
