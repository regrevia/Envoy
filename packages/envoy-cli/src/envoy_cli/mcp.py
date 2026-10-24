from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Iterable, TextIO
from urllib.parse import urlparse

from envoy_core.schemas.read_v1 import (
    ExpandRequest,
    LocalVideoReadRequest,
    MaterialReplayRequest,
    MaterialSearchRequest,
    ReadRequest,
    PROTOCOL_VERSION,
    VisualObservationHistoryRequest,
    VisualObservationRequest,
)

from . import __version__
from .browser import browser_runtime_status
from .build_info import BUILD_CHANNEL, BUILD_DIRTY, BUILD_REVISION, BUILD_VERSION
from .capabilities import capability_catalog
from .collections import (
    CollectionBudget,
    CollectionError,
    CollectionRevisionConflict,
    create_collection,
    discover_profile,
    get_collection,
    update_collection,
)
from .local_video import read_local_video
from .media import media_runtime_status
from .ocr import available_ocr_backends
from .reader import (
    PublicPageError,
    _redact_url,
    expand_material,
    read_public_page,
    record_visual_observation,
    replay_material,
    read_visual_observations,
    search_material,
)

_LOGGER = logging.getLogger(__name__)


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
LOCAL_VIDEO_SCHEMA = LocalVideoReadRequest.model_json_schema()
LOCAL_VIDEO_SCHEMA["required"] = list(dict.fromkeys([
    *LOCAL_VIDEO_SCHEMA.get("required", []),
    "idempotency_key",
]))

TOOLS = [
    {
        "name": "envoy_read_local_video",
        "description": (
            "Read a user-selected local video file with bounded local Whisper ASR "
            "and optional timestamped frames. Requires explicit idempotency_key "
            "for crash recovery. Never transmits the source file; its absolute "
            "path is not returned. Envoy extracts evidence and does not interpret it."
        ),
        "inputSchema": LOCAL_VIDEO_SCHEMA,
    },
    {
        "name": "envoy_discover",
        "description": (
            "Discover a bounded page of public Douyin video and image-note "
            "candidates. Returns titles and metadata as selection hints only; "
            "does not download media, run ASR, extract frames, or decide relevance."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["profile_url"],
            "properties": {
                "profile_url": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "cursor": {"type": ["string", "null"]},
                "max_work_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 60},
            },
        },
    },
    {
        "name": "envoy_collection_checkpoint",
        "description": (
            "Create, retrieve, or revision-checkpoint a bounded collection run. "
            "Operation IDs make repeated updates idempotent. Work state and "
            "evidence availability are reported separately."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "collection_id", "run_id"],
            "properties": {
                "action": {"type": "string", "enum": [
                    "create", "get", "select", "item_state", "record_evidence",
                    "discovery_snapshot", "record_handoff", "record_observation",
                    "record_usage", "set_cursor",
                ]},
                "collection_id": {"type": "string"},
                "run_id": {"type": "string"},
                "profile_url": {"type": ["string", "null"]},
                "expected_revision": {"type": ["integer", "null"], "minimum": 1},
                "operation_id": {"type": ["string", "null"]},
                "candidate_ids": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 100},
                "candidate_id": {"type": ["string", "null"]},
                "pending_resource_ids": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 12},
                "observed_resource_ids": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 12},
                "item_state": {"type": ["string", "null"], "enum": [
                    "selected", "processing", "completed", "partial", "blocked", "skipped", "failed", None,
                ]},
                "material_id": {"type": ["string", "null"]},
                "source_revision": {"type": ["string", "null"]},
                "expires_at": {"type": ["number", "null"]},
                "evidence_available": {"type": ["boolean", "null"]},
                "usage": {"type": ["object", "null"], "additionalProperties": False, "properties": {
                    "scanned_pages": {"type": "integer", "minimum": 0},
                    "scanned_candidates": {"type": "integer", "minimum": 0},
                    "selected_items": {"type": "integer", "minimum": 0},
                    "work_seconds": {"type": "integer", "minimum": 0},
                    "downloaded_bytes": {"type": "integer", "minimum": 0},
                    "returned_chars": {"type": "integer", "minimum": 0},
                    "visual_inputs": {"type": "integer", "minimum": 0},
                    "visual_pixels": {"type": "integer", "minimum": 0},
                    "retries": {"type": "integer", "minimum": 0},
                    "snapshot": {"type": "object"},
                }},
                "budget": {"type": ["object", "null"], "additionalProperties": False, "properties": {
                    "max_pages": {"type": "integer", "minimum": 0},
                    "max_scanned_candidates": {"type": "integer", "minimum": 0},
                    "max_items": {"type": "integer", "minimum": 0},
                    "max_download_bytes": {"type": "integer", "minimum": 0},
                    "max_response_chars": {"type": "integer", "minimum": 0},
                    "max_visual_inputs": {"type": "integer", "minimum": 0},
                    "max_visual_pixels": {"type": "integer", "minimum": 0},
                    "max_retries": {"type": "integer", "minimum": 0},
                    "max_work_seconds": {"type": "integer", "minimum": 0},
                }},
            },
        },
    },
    {
        "name": "envoy_read",
        "description": (
            "Read a public URL toward the user's stated goal. The source type is "
            "auto-detected; use returned coverage and resources to decide whether "
            "to expand text or request host visual/video inspection. Public videos "
            "prefer captions, then optional local ASR; browser-captured public media "
            "may be processed into timestamped ASR and bounded frames. Envoy returns "
            "evidence and coverage gaps, not job-location or company-match conclusions. "
            "An envoy_read needs_host response is a handoff, not a terminal state. "
            "For inspect_visual, inspect every "
            "listed resource, call envoy_observe_visual once per inspected resource "
            "using its matching material_id, resource_id, source_block_id and original "
            "session_id. source_url is optional when the material is retained; Envoy "
            "derives and verifies it from the delivered resource. Then answer from "
            "those observations plus text evidence. Report any "
            "uninspected resources; do not repeat envoy_read merely to clear the "
            "original status, and sparse frames never imply full-video coverage. "
            "Request frames only when needed with scope.include_visual=true and "
            "explicit frame-capable host settings."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["protocol_version", "request_id", "source"],
            "properties": {
                "protocol_version": {"type": "string", "const": "envoy.read/1"},
                "request_id": {"type": "string"},
                "idempotency_key": {"type": ["string", "null"]},
                "session_id": {"type": ["string", "null"]},
                "source": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["auto", "article", "video", "audio", "image", "document", "collection"],
                        },
                        "label": {"type": ["string", "null"]},
                    },
                },
                "goal": {"type": "string"},
                "scope": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["current_resource", "range", "collection"],
                        },
                        "include_visual": {"type": "boolean"},
                        "start_seconds": {"type": ["number", "null"], "minimum": 0},
                        "end_seconds": {"type": ["number", "null"], "exclusiveMinimum": 0},
                        "locator": {"type": ["string", "null"]},
                        "follow_links": {"type": "boolean"},
                        "max_items": {"type": "integer"},
                        "max_depth": {"type": "integer"},
                    },
                },
                "ocr_profile": {
                    "type": ["string", "null"],
                    "enum": ["fast", "balanced", "quality", "pro", None],
                },
                "ocr_backend": {
                    "type": ["string", "null"],
                    "enum": ["auto", "vision", "paddleocr", "rapidocr", "tesseract", None],
                    "description": (
                        "OCR engine; auto picks macOS Vision > PaddleOCR > "
                        "RapidOCR > Tesseract."
                    ),
                },
                "ocr_language": {
                    "type": ["string", "null"],
                    "minLength": 2,
                    "maxLength": 32,
                    "pattern": "^[A-Za-z0-9_-]+$",
                },
                "video_subtitle_language": {"type": ["string", "null"]},
                "budget": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "max_response_chars": {"type": "integer"},
                        "max_response_tokens": {"type": ["integer", "null"]},
                        "max_total_response_tokens": {"type": ["integer", "null"]},
                        "max_total_response_chars": {"type": "integer"},
                        "max_visual_inputs": {"type": "integer"},
                        "max_visual_pixels_per_input": {"type": "integer"},
                        "max_total_visual_pixels": {"type": "integer"},
                        "max_visual_clip_seconds": {"type": "integer"},
                        "max_total_visual_clip_seconds": {"type": "integer"},
                        "max_download_bytes": {"type": "integer"},
                        "max_work_seconds": {"type": "integer"},
                        "max_expansion_rounds": {"type": "integer"},
                        "max_optional_model_calls": {"type": "integer"},
                    },
                },
                "host_capabilities": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "image_input": {
                            "type": "string",
                            "enum": ["none", "local_file", "uri", "inline"],
                        },
                        "video_input": {
                            "type": "string",
                            "enum": ["none", "native", "frames"],
                        },
                        "tool_execution": {"type": "boolean"},
                        "usage_reporting": {
                            "type": "string",
                            "enum": ["actual", "estimated", "unknown"],
                        },
                        "async_execution": {"type": "boolean"},
                    },
                },
                "retention": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw_media": {
                            "type": "string",
                            "enum": ["ephemeral", "session", "persistent"],
                        },
                        "derived_ttl_seconds": {
                            "type": "integer",
                        },
                    },
                },
            },
        },
    },
    {
        "name": "envoy_expand",
        "description": (
            "Continue a previously read material only when its coverage reports "
            "continuation_available and next_offset; do not repeat returned ranges."
        ),
        "inputSchema": ExpandRequest.model_json_schema(),
    },
    {
        "name": "envoy_replay",
        "description": (
            "Replay retained source-located evidence into a new host session "
            "under a fresh bounded response budget. Does not re-download or "
            "reuse the previous session's returned-range ledger."
        ),
        "inputSchema": MaterialReplayRequest.model_json_schema(),
    },
    {
        "name": "envoy_search",
        "description": (
            "Find goal-relevant passages in a previously read Envoy material "
            "without expanding through unrelated ranges. Results preserve source "
            "locators and consume the material's shared response budget. Repeated "
            "searches omit passages already returned by search."
        ),
        "inputSchema": MaterialSearchRequest.model_json_schema(),
    },
    {
        "name": "envoy_doctor",
        "description": (
            "Return the versioned standalone capability catalog so the agent can "
            "select available Envoy operations; never reads credentials."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False},
    },
    {
        "name": "envoy_observe_visual",
        "description": (
            "Submit a bounded observation made by the host from a source-linked "
            "image. The observation is untrusted source material and remains "
            "linked to the original resource. Submit exactly one observation "
            "per inspected resource. In an envoy_read needs block, pair "
            "resource_ids[i] with source_block_ids[i] at the same index; "
            "action_id and purpose describe the handoff and are not sent as "
            "observation fields. Preserve its "
            "material_id, resource_id, "
            "source_block_id, and original session_id. source_url is optional "
            "when the retained material identifies the resource."
        ),
        "inputSchema": VisualObservationRequest.model_json_schema(),
    },
    {
        "name": "envoy_visual_history",
        "description": (
            "Read previously persisted host visual observations for a material. "
            "Filter by resource_id and keep max_items/max_chars small for follow-up "
            "questions; returned observations are untrusted source evidence."
        ),
        "inputSchema": VisualObservationHistoryRequest.model_json_schema(),
    },
]

# Tool declarations are part of every request prefix, so hosts that only read
# links should not pay for local-video, collection, replay, or diagnostics
# schemas. The toolset is fixed at process startup: changing tools/list
# mid-session invalidates prompt-cache reuse.
DEFAULT_TOOLSET = "full"
TOOLSET_ENV = "ENVOY_MCP_TOOLSET"
TOOLSETS: dict[str, tuple[str, ...]] = {
    "core": (
        "envoy_read",
        "envoy_expand",
        "envoy_search",
        "envoy_observe_visual",
    ),
    "full": tuple(tool["name"] for tool in TOOLS),
}
TOOLSET_NAMES = tuple(TOOLSETS)


def normalize_toolset(value: str | None = None) -> str:
    """Return a validated toolset name from an argument or environment."""

    selected = (value or os.environ.get(TOOLSET_ENV) or DEFAULT_TOOLSET).strip().lower()
    if selected not in TOOLSETS:
        raise ValueError(
            f"unknown MCP toolset {selected!r}; choose one of {', '.join(TOOLSET_NAMES)}"
        )
    return selected


def tools_for_toolset(value: str | None = None) -> list[dict]:
    """Return the static tool declarations for one toolset, preserving order."""

    allowed = set(TOOLSETS[normalize_toolset(value)])
    return [tool for tool in TOOLS if tool["name"] in allowed]


class MCPProtocolSession:
    def __init__(self):
        self.mode: str | None = None
        self.legacy_protocol_version: str | None = None


def validate_observation_source_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme == "envoy-local-video":
        if (
            parsed.netloc == "sha256"
            and re.fullmatch(r"/[a-fA-F0-9]{64}/[^/]+", parsed.path)
            and not parsed.query
            and not parsed.fragment
        ):
            return value
        raise ValueError("Local video observations require a content-addressed source identity")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Visual observations require a credential-free HTTP(S) source URL")
    return _redact_url(value)


# Kept as a compatibility alias for integrations that imported the original
# internal helper while the CLI and MCP paths converge on one validator.
_observation_source_url = validate_observation_source_url


def _result(request_id: Any, payload: dict, modern: bool, cacheable: bool = False) -> dict:
    result = dict(payload)
    if modern:
        result["resultType"] = "complete"
        result["_meta"] = {SERVER_INFO_META: SERVER_INFO}
        if cacheable:
            result["ttlMs"] = 3_600_000
            result["cacheScope"] = "public"
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_content_text(payload: dict, modern: bool) -> str:
    if payload.get("tool") == "envoy":
        return "Envoy doctor ready."
    if payload.get("tool") == "envoy_discover":
        candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        return (
            f"Envoy discovered {len(candidates)} candidate(s); "
            f"has_more={payload.get('has_more', 'unknown')}. "
            "Titles and metadata are selection hints only."
        )
    if payload.get("tool") == "envoy_collection_checkpoint":
        return (
            f"Envoy collection checkpoint {payload.get('action', 'updated')}; "
            f"manifest_revision={payload.get('manifest_revision', 'unknown')}."
        )
    if not modern:
        return json.dumps(payload, ensure_ascii=False)
    result = str(payload.get("result") or "unknown")
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
    resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
    text_chars = sum(
        len(str(block.get("content", {}).get("text") or ""))
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("content"), dict)
    )
    if result == "blocked":
        errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
        code = errors[0].get("code", "unknown") if errors and isinstance(errors[0], dict) else "unknown"
        return f"Envoy read blocked ({code}). See structuredContent.errors for details."
    if result == "needs_host":
        needs = payload.get("needs") if isinstance(payload.get("needs"), list) else []
        action_types = {
            str(item.get("type"))
            for item in needs
            if isinstance(item, dict) and item.get("type")
        }
        if "inspect_video" in action_types:
            return "Envoy read needs_host: inspect the delivered video with native video input; see structuredContent.needs and resources."
        if "inspect_visual" in action_types:
            return (
                "Envoy read needs_host is a handoff, not a terminal state: inspect "
                "each listed resource before its lease expires, then call "
                "envoy_observe_visual once per inspected resource with its matching "
                "material_id, resource_id, source_block_id, and original session_id. "
                "source URL may be omitted when the material is retained because "
                "Envoy derives it from the resource binding. Continue "
                "answering from the returned observations and "
                "ASR/chapter evidence; do not repeat envoy_read just to clear the "
                "original needs_host status. Report uninspected frames and do not "
                "imply full-video coverage."
            )
        return "Envoy read needs_host: perform the requested host action in structuredContent.needs."
    return (
        f"Envoy read {result}: {text_chars} text characters, "
        f"{len(blocks)} blocks, {len(resources)} resources. "
        "Full materials are in structuredContent."
    )


def _read_payload(response, compact: bool) -> dict:
    if not compact:
        return response.model_dump(mode="json")
    payload = response.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    )
    payload["protocol_version"] = response.protocol_version
    return payload


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


def handle_message(
    message: dict,
    session: MCPProtocolSession | None = None,
    toolset: str | None = None,
) -> dict | None:
    session = session or MCPProtocolSession()
    selected_toolset = normalize_toolset(toolset)
    allowed_tools = set(TOOLSETS[selected_toolset])
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
            {"tools": tools_for_toolset(selected_toolset)},
            modern=modern_request,
            cacheable=modern_request,
        )
    if method != "tools/call":
        return _error(request_id, -32601, f"Method not found: {method}")

    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "Tool arguments must be an object")
    if not isinstance(name, str) or name not in allowed_tools:
        known_tools = {tool["name"] for tool in TOOLS}
        if isinstance(name, str) and name in known_tools:
            return _error(
                request_id,
                -32602,
                f"Tool {name!r} is not exposed by the {selected_toolset!r} MCP toolset",
                {
                    "toolset": selected_toolset,
                    "available": list(TOOLSETS[selected_toolset]),
                    "hint": "Restart the MCP server with --toolset full for advanced tools.",
                },
            )
        return _error(
            request_id,
            -32602,
            f"Unknown tool: {name!r}",
            {"toolset": selected_toolset, "available": list(TOOLSETS[selected_toolset])},
        )
    try:
        if name == "envoy_read":
            response = read_public_page(ReadRequest.model_validate(arguments))
            payload = _read_payload(response, compact=modern_request)
        elif name == "envoy_read_local_video":
            response = read_local_video(LocalVideoReadRequest.model_validate(arguments))
            payload = _read_payload(response, compact=modern_request)
        elif name == "envoy_discover":
            payload = {"tool": "envoy_discover", **discover_profile(
                str(arguments.get("profile_url") or ""),
                limit=int(arguments.get("limit") or 20),
                cursor=arguments.get("cursor"),
                max_work_seconds=int(arguments.get("max_work_seconds") or 60),
            )}
        elif name == "envoy_collection_checkpoint":
            action = str(arguments.get("action") or "")
            collection_id = str(arguments.get("collection_id") or "")
            run_id = str(arguments.get("run_id") or "")
            if action == "create":
                payload = create_collection(
                    collection_id,
                    run_id,
                    str(arguments.get("profile_url") or ""),
                    operation_id=str(arguments.get("operation_id") or ""),
                    budget=(
                        CollectionBudget(**arguments["budget"])
                        if arguments.get("budget")
                        else None
                    ),
                )
            elif action == "get":
                payload = get_collection(collection_id, run_id)
            else:
                if arguments.get("expected_revision") is None or not arguments.get("operation_id"):
                    raise CollectionError("Mutations require expected_revision and operation_id")
                budget_data = arguments.get("budget") or {}
                payload = update_collection(
                    collection_id,
                    run_id=run_id,
                    expected_revision=int(arguments["expected_revision"]),
                    operation_id=str(arguments["operation_id"]),
                    action=action,
                    candidate_ids=arguments.get("candidate_ids"),
                    candidate_id_value=arguments.get("candidate_id"),
                    item_state=arguments.get("item_state"),
                    material_id=arguments.get("material_id"),
                    source_revision=arguments.get("source_revision"),
                    expires_at=arguments.get("expires_at"),
                    evidence_available=arguments.get("evidence_available"),
                    pending_resource_ids=arguments.get("pending_resource_ids"),
                    observed_resource_ids=arguments.get("observed_resource_ids"),
                    usage=arguments.get("usage"),
                    budget=CollectionBudget(**budget_data) if budget_data else None,
                )
            payload = {"tool": "envoy_collection_checkpoint", "action": action, **payload}
        elif name == "envoy_expand":
            response = expand_material(ExpandRequest.model_validate(arguments))
            payload = _read_payload(response, compact=modern_request)
        elif name == "envoy_replay":
            response = replay_material(MaterialReplayRequest.model_validate(arguments))
            payload = _read_payload(response, compact=modern_request)
        elif name == "envoy_search":
            response = search_material(MaterialSearchRequest.model_validate(arguments))
            payload = _read_payload(response, compact=modern_request)
        elif name == "envoy_doctor":
            payload = {
                "tool": "envoy",
                "cli_version": __version__,
                "build": {
                    "channel": BUILD_CHANNEL,
                    "version": BUILD_VERSION,
                    "source_revision": BUILD_REVISION,
                    "source_dirty": BUILD_DIRTY,
                },
                "protocol_version": "envoy.read/1",
                "network_reader": "public_static_and_supported_media_extractors",
                "credential_access": "isolated_envoy_browser_profile_only",
                "website_backend_required": False,
                "capabilities": capability_catalog(),
                "runtime": {
                    **media_runtime_status(),
                    "ocr": available_ocr_backends(),
                    "browser": browser_runtime_status(),
                },
            }
        elif name == "envoy_observe_visual":
            request = VisualObservationRequest.model_validate(arguments)
            source_url = (
                _observation_source_url(request.source_url)
                if request.source_url
                else None
            )
            persistence = record_visual_observation(
                request.material_id,
                source_url,
                request.resource_id,
                request.source_block_id,
                request.observation,
                request.locator_quote,
                request.session_id,
            )
            if not persistence.get("persisted"):
                error_code = (
                    "expired_resource"
                    if persistence.get("reason") == "material_unavailable"
                    else "internal_error"
                    if persistence.get("reason") == "storage_unavailable"
                    else "invalid_request"
                )
                payload = {
                    "protocol_version": "envoy.observe/1",
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "material_id": request.material_id,
                    "execution_state": "completed",
                    "result": "blocked",
                    "blocks": [],
                    "errors": [{
                        "code": error_code,
                        "message": (
                            "Visual observation must reference a retained material resource "
                            "delivered by Envoy; association could not be verified"
                        ),
                    }],
                    "observation_persistence": persistence,
                }
            else:
                source_url = str(persistence["source_url"])
                payload = {
                    "protocol_version": "envoy.observe/1",
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "material_id": request.material_id,
                    "execution_state": "completed",
                    "result": "ready",
                    "blocks": [{
                        "id": f"{request.source_block_id}-observation",
                        "kind": "paragraph",
                        "content": {"type": "text", "text": request.observation},
                        "origin": "host_visual",
                        "locator": {
                            "type": "web",
                            "source_url": source_url,
                            "block_id": request.source_block_id,
                            "quote": request.locator_quote or request.observation[:120],
                        },
                        "relations": [{
                            "type": "derived_from",
                            "target_id": request.source_block_id,
                            "basis": "host_observation",
                        }],
                    }],
                    "coverage": {
                        "modalities": {
                            "visual_observation": {
                                "discovered": 1,
                                "extracted": 1,
                                "returned": 1,
                                "observed": 1,
                                "extraction": "complete",
                                "details": {
                                    "resource_id": request.resource_id,
                                    "untrusted": True,
                                },
                            },
                        },
                    },
                    "observation_persistence": persistence,
                }
        elif name == "envoy_visual_history":
            response = read_visual_observations(
                VisualObservationHistoryRequest.model_validate(arguments)
            )
            payload = _read_payload(response, compact=modern_request)
        else:
            return _error(request_id, -32602, f"Unknown tool: {name}")
    except PublicPageError as exc:
        payload = {
            "protocol_version": "envoy.read/1",
            "request_id": arguments.get("request_id", "mcp"),
            "execution_state": "completed",
            "result": "blocked",
            "errors": [{
                "code": exc.code.value,
                "message": str(exc),
                **({"details": exc.details} if exc.details else {}),
            }],
        }
    except (TypeError, ValueError, CollectionRevisionConflict) as exc:
        return _error(request_id, -32602, str(exc))
    except Exception:
        _LOGGER.exception("Unexpected Envoy MCP tool failure: %s", name)
        return _error(
            request_id,
            -32603,
            "Internal error while handling MCP tool call",
        )
    return _result(request_id, {
        "content": [{
            "type": "text",
            "text": _tool_content_text(payload, modern_request),
        }],
        "structuredContent": payload,
        "isError": payload.get("result") == "blocked",
    }, modern=modern_request)


def run_lines(lines: Iterable[dict], toolset: str | None = None) -> int:
    session = MCPProtocolSession()
    selected_toolset = normalize_toolset(toolset)
    for message in lines:
        try:
            response = handle_message(message, session, selected_toolset)
        except Exception:
            request_id = message.get("id") if isinstance(message, dict) else None
            _LOGGER.exception("Unexpected Envoy MCP request failure")
            response = _error(
                request_id,
                -32603,
                "Internal error while handling MCP request",
            )
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def run_stream(
    input_stream: TextIO,
    output_stream: TextIO,
    toolset: str | None = None,
) -> int:
    session = MCPProtocolSession()
    selected_toolset = normalize_toolset(toolset)
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            output_stream.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            output_stream.flush()
            continue
        try:
            response = handle_message(message, session, selected_toolset)
        except Exception:
            request_id = message.get("id") if isinstance(message, dict) else None
            _LOGGER.exception("Unexpected Envoy MCP request failure")
            response = _error(
                request_id,
                -32603,
                "Internal error while handling MCP request",
            )
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    return 0


def main(toolset: str | None = None) -> int:
    try:
        selected_toolset = normalize_toolset(toolset)
    except ValueError as exc:
        print(f"envoy mcp: {exc}", file=sys.stderr)
        return 2
    return run_stream(sys.stdin, sys.stdout, selected_toolset)
