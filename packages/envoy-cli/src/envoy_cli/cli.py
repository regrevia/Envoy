from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from envoy_core import __version__ as core_version
from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

from . import __version__
from .reader import PublicPageError, expand_material, read_public_page


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="envoy", description="Read public links into Envoy materials")
    parser.add_argument("--version", action="version", version=f"envoy-cli {__version__} (core {core_version})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    read = subparsers.add_parser("read", help="read a public link")
    read.add_argument("--request-json", required=True, help="read an envoy.read/1 request from this path or '-' for stdin")

    expand = subparsers.add_parser("expand", help="read the next range from a local material")
    expand.add_argument("--request-json", required=True, help="read an expansion request from this path or '-' for stdin")

    doctor = subparsers.add_parser("doctor", help="check the independent Skill runtime")
    doctor.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def main(argv: Optional[list[str]] = None, stdin: Optional[str] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "doctor":
        result = {
            "tool": "envoy",
            "cli_version": __version__,
            "core_version": core_version,
            "protocol_version": "envoy.read/1",
            "network_reader": "public_html",
            "credential_access": "disabled",
            "website_backend_required": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "Envoy Skill ready")
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
        else:
            request = ReadRequest.model_validate_json(payload)
            expand_request = {}
            response = read_public_page(request)
        print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False))
        return 0 if response.result.value in {"ready", "partial"} else 2
    except PublicPageError as exc:
        print(json.dumps({
            "protocol_version": "envoy.read/1",
            "request_id": expand_request.request_id if "expand_request" in locals() else "read",
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
