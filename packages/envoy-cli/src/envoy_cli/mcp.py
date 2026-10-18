from __future__ import annotations

import json
import sys
from typing import Any, Iterable, TextIO

from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

from . import __version__
from .reader import PublicPageError, expand_material, read_public_page


CURRENT_PROTOCOL_VERSION = "2026-07-28"
MODERN_PROTOCOL_VERSIONS = (CURRENT_PROTOCOL_VERSION,)
LEGACY_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
    "2024-10-07",
)
SUPPORTED_PROTOCOL_VERSIONS = (*MODERN_PROTOCOL_VERSIONS, *LEGACY_PROTOCOL_VERSIONS)
PROTOCOL_VERSION_META = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"

SERVER_INFO = {"name": "envoy", "version": __version__}
SERVER_CAPABILITIES = {"tools": {}}

TOOLS = [
    {
        "name": "envoy_read",
        "description": "Read a public link into compact, source-located Envoy materials.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["protocol_version", "request_id", "source"],
            "properties": {
                "protocol_version": {"type": "string", "const": "envoy.read/1"},
                "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "session_id": {"type": ["string", "null"], "maxLength": 128},
                "source": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "minLength": 1, "maxLength": 8192},
                        "kind": {
                            "type": "string",
                            "enum": ["auto", "article"],
                            "default": "auto",
                        },
                        "label": {"type": ["string", "null"], "maxLength": 1000},
                    },
                },
                "goal": {"type": "string", "maxLength": 20_000},
                "budget": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "max_response_chars": {
                            "type": "integer",
                            "minimum": 256,
                            "maximum": 1_000_000,
                            "default": 12_000,
                        },
                        "max_visual_inputs": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 10_000,
                            "default": 12,
                        },
                        "max_download_bytes": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 10_737_418_240,
                            "default": 268_435_456,
                        },
                    },
                },
                "retention": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "derived_ttl_seconds": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 31_536_000,
                            "default": 86_400,
                        },
                    },
                },
            },
        },
    },
    {
        "name": "envoy_expand",
        "description": "Read the next range from a previously returned Envoy material.",
        "inputSchema": ExpandRequest.model_json_schema(),
    },
    {
        "name": "envoy_doctor",
        "description": "Report local Envoy capabilities without reading credentials.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
]


class MCPProtocolSession:
    def __init__(self):
        self.mode: str | None = None
        self.legacy_protocol_version: str | None = None


def _result(request_id: Any, payload: dict, modern: bool, cacheable: bool = False) -> dict:
    result = dict(payload)
    if modern:
        result["resultType"] = "complete"
        result["_meta"] = {SERVER_INFO_META: SERVER_INFO}
        if cacheable:
            result["ttlMs"] = 3_600_000
            result["cacheScope"] = "public"
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: Any,
    code: int,
    message: str,
    data: dict | None = None,
) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _request_version(params: dict) -> tuple[str | None, dict | None]:
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return None, None
    version = metadata.get(PROTOCOL_VERSION_META)
    if not isinstance(version, str):
        return None, metadata
    return version, metadata


def _modern_error(request_id: Any, version: str | None, params: dict, session: MCPProtocolSession):
    if version is None:
        return _error(request_id, -32602, "Modern MCP requests require protocol version metadata")
    if version not in MODERN_PROTOCOL_VERSIONS:
        return _error(request_id, -32022, "Unsupported protocol version", {
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": version,
        })
    metadata = params.get("_meta")
    if not isinstance(metadata, dict) or not isinstance(metadata.get(CLIENT_CAPABILITIES_META), dict):
        return _error(request_id, -32602, "Modern MCP requests require client capabilities metadata")
    client_info = metadata.get(CLIENT_INFO_META)
    if client_info is not None and not isinstance(client_info, dict):
        return _error(request_id, -32602, "Client info metadata must be an object")
    session.mode = "modern"
    return None


def handle_message(message: dict, session: MCPProtocolSession | None = None) -> dict | None:
    session = session or MCPProtocolSession()
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid JSON-RPC request")

    request_id = message.get("id")
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32600, "Request params must be an object")
    if request_id is None:
        return None
    if method == "notifications/initialized":
        return None

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        if requested_version is None:
            selected_version = LEGACY_PROTOCOL_VERSIONS[0]
        elif requested_version in LEGACY_PROTOCOL_VERSIONS:
            selected_version = requested_version
        else:
            return _error(request_id, -32602, "Unsupported legacy protocol version", {
                "supported": list(LEGACY_PROTOCOL_VERSIONS),
                "requested": requested_version,
            })
        session.mode = "legacy"
        session.legacy_protocol_version = selected_version
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": selected_version,
                "capabilities": SERVER_CAPABILITIES,
                "serverInfo": SERVER_INFO,
            },
        }

    request_version, _metadata = _request_version(params)
    if method == "server/discover" or request_version is not None:
        protocol_error = _modern_error(request_id, request_version, params, session)
        if protocol_error is not None:
            return protocol_error
        if method == "server/discover":
            return _result(request_id, {
                "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
                "capabilities": SERVER_CAPABILITIES,
            }, modern=True, cacheable=True)
    elif session.mode == "modern" and session.legacy_protocol_version is None:
        return _error(request_id, -32600, "Modern MCP requests require protocol version metadata")
    elif session.mode != "legacy" and session.legacy_protocol_version is None:
        return _error(request_id, -32602, "Request must use modern metadata or initialize a legacy session")

    modern_request = method == "server/discover" or request_version in MODERN_PROTOCOL_VERSIONS
    if method == "tools/list":
        return _result(
            request_id,
            {"tools": TOOLS},
            modern=modern_request,
            cacheable=modern_request,
        )
    if method != "tools/call":
        return _error(request_id, -32601, f"Method not found: {method}")

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "Tool arguments must be an object")
    try:
        if name == "envoy_read":
            payload = read_public_page(ReadRequest.model_validate(arguments)).model_dump(mode="json")
        elif name == "envoy_expand":
            payload = expand_material(ExpandRequest.model_validate(arguments)).model_dump(mode="json")
        elif name == "envoy_doctor":
            payload = {
                "tool": "envoy",
                "cli_version": __version__,
                "protocol_version": "envoy.read/1",
                "network_reader": "public_html",
                "credential_access": "disabled",
                "website_backend_required": False,
            }
        else:
            return _error(request_id, -32602, f"Unknown tool: {name}")
    except PublicPageError as exc:
        payload = {
            "protocol_version": "envoy.read/1",
            "request_id": arguments.get("request_id", "mcp"),
            "execution_state": "completed",
            "result": "blocked",
            "errors": [{"code": exc.code.value, "message": str(exc)}],
        }
    except (TypeError, ValueError) as exc:
        return _error(request_id, -32602, str(exc))
    return _result(request_id, {
        "content": [{
            "type": "text",
            "text": json.dumps(payload, ensure_ascii=False),
        }],
        "structuredContent": payload,
        "isError": payload.get("result") == "blocked",
    }, modern=modern_request)


def run_lines(lines: Iterable[dict]) -> int:
    session = MCPProtocolSession()
    for message in lines:
        response = handle_message(message, session)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def run_stream(input_stream: TextIO, output_stream: TextIO) -> int:
    session = MCPProtocolSession()
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            output_stream.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            output_stream.flush()
            continue
        response = handle_message(message, session)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    return 0


def main() -> int:
    return run_stream(sys.stdin, sys.stdout)
