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
    MaterialSearchRequest,
    ReadRequest,
)

from .capabilities import capability_catalog
from . import browser as browser_runtime
from .build_info import BUILD_CHANNEL, BUILD_DIRTY, BUILD_REVISION, BUILD_VERSION
from .media import MediaCapabilityMissing, media_runtime_status, prepare_asr_model
from .ocr import available_ocr_backends
from .package_install import install_media_dependencies
from . import __version__
from . import mcp
from .collections import CollectionError, discover_profile
from .local_video import read_local_video
from .reader import PublicPageError, expand_material, read_public_page, search_material


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

    expand = subparsers.add_parser("expand", help="read the next range from a local material")
    expand.add_argument("--request-json", required=True, help="read an expansion request from this path or '-' for stdin")

    search = subparsers.add_parser("search", help="find relevant passages in a local material")
    search.add_argument("--request-json", required=True, help="read an envoy.search/1 request from this path or '-' for stdin")

    discover = subparsers.add_parser("discover", help="discover bounded public Douyin profile candidates")
    discover.add_argument("--url", required=True)
    discover.add_argument("--limit", type=int, default=20)
    discover.add_argument("--cursor")
    discover.add_argument("--max-work-seconds", type=int, default=60)

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
    subparsers.add_parser("mcp", help="run the stdio MCP bridge")
    return parser


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
    if args.command == "doctor":
        runtime = media_runtime_status(verify_asr=args.verify_asr)
        runtime["browser"] = browser_runtime.browser_runtime_status()
        runtime["ocr"] = available_ocr_backends()
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
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "Envoy Skill ready")
        if args.verify_asr and runtime["asr_operational"] != "verified":
            return 2
        return 0
    if args.command == "browser":
        return browser_runtime.setup_browser()
    if args.command == "mcp":
        return mcp.main()
    if args.command == "asr":
        try:
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
        elif args.command == "search":
            operation_request = MaterialSearchRequest.model_validate_json(payload)
            response = search_material(operation_request)
        elif args.command == "read-local-video":
            operation_request = LocalVideoReadRequest.model_validate_json(payload)
            response = read_local_video(operation_request)
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
            "errors": [{"code": exc.code.value, "message": str(exc)}],
        }, ensure_ascii=False))
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": "invalid_request", "message": str(exc)}, ensure_ascii=False))
        return 1


def entrypoint() -> None:
    raise SystemExit(main())
