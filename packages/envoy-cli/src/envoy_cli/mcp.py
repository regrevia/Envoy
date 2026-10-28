from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, TextIO
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

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
from . import auth_session
from .browser import browser_runtime_status
from . import storage as state_storage
from .build_info import BUILD_CHANNEL, BUILD_DIRTY, BUILD_REVISION, BUILD_VERSION
from .capabilities import capability_catalog
from .collections import (
    CollectionBudget,
    CollectionError,
    CollectionRevisionConflict,
    create_collection,
    discover_profile,
    get_collection,
    list_collections,
    update_collection,
)
from .local_source import (
    classify_local_source,
    local_read_request,
    read_local_pdf,
)
from .local_video import read_local_video
from .media import media_runtime_status
from .ocr import available_ocr_backends
from .reader import (
    PublicPageError,
    _estimated_tokens,
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

# ---------------------------------------------------------------------------
# Declared input schemas.
#
# A tool's `inputSchema` is re-sent in every request prefix, so it describes
# only what a caller must know: `request_id`, `protocol_version`,
# `idempotency_key`, `session_id`, and the advanced `budget` / `retention` /
# `host_capabilities` / `ocr_*` blocks are filled in by `_server_arguments`
# before pydantic validation. The pydantic models stay authoritative for what
# the server accepts; an advanced block a caller does send is still validated
# exactly as before, so this reduces declaration size, not accepted input.
# ---------------------------------------------------------------------------

# A bounded read range is flattened here; `_server_arguments` rebuilds
# `scope.kind="range"` from the pair.
_READ_SCOPE_PROPERTIES = {
    "start_seconds": {
        "type": ["number", "null"],
        "minimum": 0,
        "description": "Range read start; send together with end_seconds.",
    },
    "end_seconds": {
        "type": ["number", "null"],
        "exclusiveMinimum": 0,
        "description": "Range read end; send together with start_seconds.",
    },
    "page_start": {
        "type": ["integer", "null"],
        "minimum": 1,
        "description": "PDF page window start (1-based); send with page_end for another window.",
    },
    "page_end": {
        "type": ["integer", "null"],
        "minimum": 1,
        "description": "PDF page window end, inclusive; clamped to the document length.",
    },
    "locator": {"type": ["string", "null"]},
    "include_visual": {"type": "boolean"},
}

# Frames are gated on what the caller can consume, so the declaration belongs to
# the visual request rather than to an advanced block: a caller that sets
# `include_visual` and omits these gets text only, and cannot tell why.
_READ_VISUAL_PROPERTIES = {
    "host_capabilities": {
        "type": "object",
        "additionalProperties": True,
        "description": (
            "What this host can consume. Video frames are extracted only when "
            "video_input='frames' and image_input='local_file'; without them the "
            "read still returns subtitles or ASR but delivers no images."
        ),
        "properties": {
            "video_input": {
                "type": "string",
                "enum": ["none", "native", "frames"],
                "default": "none",
            },
            "image_input": {
                "type": "string",
                "enum": ["none", "local_file", "uri"],
                "default": "none",
            },
            "tool_execution": {"type": "boolean"},
            "async_execution": {"type": "boolean"},
        },
    },
    "retention": {
        "type": "object",
        "additionalProperties": True,
        "description": (
            "raw_media='persistent' keeps the downloaded source file for later "
            "frame extraction; derived_ttl_seconds bounds the material lease."
        ),
        "properties": {
            "raw_media": {
                "type": "string",
                "enum": ["ephemeral", "session", "persistent"],
                "default": "ephemeral",
            },
            "derived_ttl_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 31536000,
            },
        },
    },
}

READ_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["source", "goal"],
    "properties": {
        "source": {
            "type": "object",
            "additionalProperties": True,
            "required": ["url"],
            "properties": {
                "url": {
                    "type": "string",
                    "description": "HTTP(S) URL or local file path (file:///abs/path.mp4).",
                },
                "kind": {
                    "type": "string",
                    "enum": ["auto", "article", "video", "audio", "image", "document", "collection"],
                },
                "label": {"type": ["string", "null"]},
            },
        },
        "goal": {
            "type": "string",
            "description": "What the user needs from this source; scopes what is returned.",
        },
        **_READ_SCOPE_PROPERTIES,
        **_READ_VISUAL_PROPERTIES,
    },
}

LOCAL_VIDEO_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["path", "idempotency_key"],
    "properties": {
        "path": {"type": "string", "description": "Local video or audio file path."},
        "idempotency_key": {
            "type": "string",
            "description": "Required: a retry with the same key replays one ASR run.",
        },
        "goal": {"type": "string"},
        **_READ_SCOPE_PROPERTIES,
        **_READ_VISUAL_PROPERTIES,
    },
}

EXPAND_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["material_id"],
    "properties": {
        "material_id": {"type": "string"},
        "offset": {"type": "integer", "minimum": 0, "description": "coverage.next_offset"},
        "max_chars": {"type": "integer", "minimum": 128, "maximum": 100000},
    },
}

SEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["material_id", "query"],
    "properties": {
        "material_id": {"type": "string"},
        "query": {"type": "string"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        "max_chars": {"type": "integer", "minimum": 128, "maximum": 20000},
    },
}

REPLAY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["material_id", "source_revision", "session_id"],
    "properties": {
        "material_id": {"type": "string"},
        "source_revision": {"type": "string"},
        "session_id": {
            "type": "string",
            "description": "Target session; must differ from the source session.",
        },
        "source_session_id": {"type": ["string", "null"]},
        "max_chars": {"type": "integer", "minimum": 256, "maximum": 100000},
    },
}

OBSERVE_VISUAL_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["resource_id", "source_block_id", "observation"],
    "properties": {
        "resource_id": {"type": "string"},
        "source_block_id": {"type": "string"},
        "observation": {"type": "string"},
        "material_id": {"type": ["string", "null"]},
        "locator_quote": {"type": ["string", "null"]},
        "source_url": {"type": ["string", "null"]},
        "session_id": {
            "type": ["string", "null"],
            "description": (
                "Session that holds the material; pass the original session_id when "
                "answering from a different process or surface."
            ),
        },
    },
}

VISUAL_HISTORY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["material_id"],
    "properties": {
        "material_id": {"type": "string"},
        "resource_id": {"type": ["string", "null"]},
        "max_items": {"type": "integer", "minimum": 1, "maximum": 16},
        "max_chars": {"type": "integer", "minimum": 128, "maximum": 20000},
    },
}

AUTH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {"type": "string", "enum": ["open", "status", "cancel", "import"]},
        "action_id": {"type": ["string", "null"]},
        "url": {"type": ["string", "null"]},
        "browser": {
            "type": "string",
            "enum": ["chrome", "edge", "brave", "vivaldi", "chromium", "safari"],
            "description": (
                "For action 'import': which of the user's own browsers holds the "
                "session. Copying is local; only the cookie count and names are "
                "reported back, never the values."
            ),
        },
        "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 900},
        "visible": {"type": "boolean", "default": False},
    },
}

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
            "candidates, or of a Bilibili space. Returns titles and metadata as "
            "selection hints only; does not download media, run ASR, extract "
            "frames, or decide relevance."
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
            "evidence availability are reported separately. action=list returns "
            "every resumable run on this machine with its progress, so a later "
            "session can pick a paused batch back up."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "collection_id", "run_id"],
            "properties": {
                "action": {"type": "string", "enum": [
                    "create", "get", "select", "item_state", "record_evidence",
                    "discovery_snapshot", "record_handoff", "record_observation",
                    "record_usage", "set_cursor", "touch_lease", "list",
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
            "may be processed into timestamped ASR and bounded frames. A file the user "
            "already has is accepted too: pass it as `source.url` either as a `file://` "
            "URL or as an absolute path, and Envoy reads it locally without uploading it. "
            "Envoy returns "
            "evidence and coverage gaps, not job-location or company-match conclusions. "
            "An envoy_read needs_host response is a handoff, not a terminal state. "
            "For inspect_visual, inspect every "
            "listed resource, call envoy_observe once per inspected resource "
            "using its matching material_id, resource_id, source_block_id and original "
            "session_id. source_url is optional when the material is retained; Envoy "
            "derives and verifies it from the delivered resource. Then answer from "
            "those observations plus text evidence. Report any "
            "uninspected resources; do not repeat envoy_read merely to clear the "
            "original status, and sparse frames never imply full-video coverage. "
            "Request frames with scope.include_visual=true plus "
            "host_capabilities {video_input:'frames', image_input:'local_file'}; a "
            "video read without that declaration returns text and names the exact "
            "fields to send, so re-read with them instead of opening the page in a "
            "separate browser view. retention {raw_media:'persistent'} keeps the "
            "source file for later frame extraction. The returned text carries "
            "`material_id`, `source_revision`, the block text, and the resource ids: "
            "keep them. Both the content text and structuredContent carry the same "
            "payload, because hosts disagree about which one reaches the model (DSH "
            "reads only content text; Codex CLI and Claude Code read only "
            "structuredContent), so neither field is a receipt-only view. Once a "
            "source has been read, continue with "
            "envoy_search or envoy_expand on that material_id instead of reading "
            "the URL again; a repeated read can hit an auth wall or challenge, and "
            "Envoy then serves the material it already retained rather than "
            "re-fetching. Resource descriptors are Envoy artifacts delivered in that "
            "payload; this server exposes no MCP resources capability, so listing MCP "
            "resources is always empty by design. budget is accepted "
            "but not declared: {max_work_seconds, max_download_bytes, "
            "max_response_chars}. Raise max_work_seconds for long audio or video "
            "(a one-hour recording needs several hundred seconds; the ceiling is "
            "3600) and raise max_response_chars up to 1000000 only when you really "
            "need the whole text in one reply; refresh true re-reads instead of "
            "replaying retained material."
        ),
        "inputSchema": READ_SCHEMA,
    },
    {
        "name": "envoy_expand",
        "description": (
            "Continue a previously read material only when its coverage reports "
            "continuation_available and next_offset; do not repeat returned ranges."
        ),
        "inputSchema": EXPAND_SCHEMA,
    },
    {
        "name": "envoy_replay",
        "description": (
            "Replay retained source-located evidence into a new host session "
            "under a fresh bounded response budget. Does not re-download or "
            "reuse the previous session's returned-range ledger."
        ),
        "inputSchema": REPLAY_SCHEMA,
    },
    {
        "name": "envoy_search",
        "description": (
            "Find goal-relevant passages in a previously read Envoy material "
            "without expanding through unrelated ranges. Results preserve source "
            "locators and consume the material's shared response budget. Repeated "
            "searches omit passages already returned by search."
        ),
        "inputSchema": SEARCH_SCHEMA,
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
        "name": "envoy_observe",
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
        "inputSchema": OBSERVE_VISUAL_SCHEMA,
    },
    {
        "name": "envoy_visual_history",
        "description": (
            "Read previously persisted host visual observations for a material. "
            "Filter by resource_id and keep max_items/max_chars small for follow-up "
            "questions; returned observations are untrusted source evidence."
        ),
        "inputSchema": VISUAL_HISTORY_SCHEMA,
    },
    {
        "name": "envoy_auth",
        "description": (
            "Hand a sign-in that needs a human to the user, in the chat. Use it "
            "when envoy_read returns challenge_required or login required on a "
            "site whose content the user wants: action 'open' starts the site's "
            "own sign-in page in a background browser of its own and returns a "
            "screenshot of it (usually the QR code to scan); show that picture to "
            "the user with its absolute path, then call action 'status' after they "
            "finish and re-read the original link. The browser stays open in the "
            "background between calls, so a scan is enough and the user never has "
            "to find a terminal. action 'cancel' closes it. When the user's own "
            "browser already holds the session, action 'import' with browser "
            "'chrome'/'edge'/'safari' copies that site's cookies into Envoy's "
            "local store without a terminal; only counts and cookie names come "
            "back, never values. This whole tool is off by default: it works only "
            "after the user runs `envoy auth handoff on`. Pass action_id when a "
            "previous open gave one, or url to start from a link."
        ),
        "inputSchema": AUTH_SCHEMA,
    },
]

# Tool declarations are part of every request prefix, so hosts that only read
# links should not pay for local-video, collection, replay, or diagnostics
# schemas. The toolset is fixed at process startup: changing tools/list
# mid-session invalidates prompt-cache reuse.
DEFAULT_TOOLSET = "core"
TOOLSET_ENV = "ENVOY_MCP_TOOLSET"
TOOLSETS: dict[str, tuple[str, ...]] = {
    "core": (
        "envoy_read",
        "envoy_expand",
        "envoy_search",
        "envoy_observe",
        "envoy_auth",
    ),
    "full": tuple(tool["name"] for tool in TOOLS),
}
# Renamed tools keep accepting the previous spelling for one release, so a
# session started before the restart does not fail on a stale name.
TOOL_ALIASES: dict[str, str] = {"envoy_observe_visual": "envoy_observe"}

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
    """Return the static tool declarations for one toolset, preserving order.

    Credential handoff ships off, so its tool is not advertised until the user
    turns the switch on: a model should not be offered a capability that is
    deliberately unavailable.
    """

    allowed = set(TOOLSETS[normalize_toolset(value)])
    if not _credential_handoff_enabled():
        allowed.discard("envoy_auth")
    return [tool for tool in TOOLS if tool["name"] in allowed]


def _credential_handoff_enabled() -> bool:
    from . import features

    return features.credential_handoff_enabled()


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


def _state_not_writable_payload(request_id: str, message: str) -> dict:
    return {
        "protocol_version": "envoy.read/1",
        "request_id": request_id,
        "execution_state": "completed",
        "result": "blocked",
        "errors": [{
            "code": "state_not_writable",
            "message": message,
            "retryable": False,
            "action_required": True,
            "details": {
                "needs_user_action": {
                    "kind": "state_not_writable",
                    "requires_human": True,
                    "question": "Envoy cannot write its state directory in this host.",
                    "options": [{
                        "kind": "set_writable_state_dir",
                        "effect": "Keep retained material in a writable directory",
                        # A state directory is fixed when the host starts the MCP
                        # process, so this is a host-operator action rather than
                        # a tool the model can call mid-turn.
                        "instruction": (
                            "Restart this host's Envoy MCP process with "
                            "ENVOY_LOCAL_DATA_DIR set to "
                            f"{state_storage.fallback_root()} so retained material "
                            "lands in a writable directory."
                        ),
                    }],
                },
            },
        }],
    }


def _payload_unresolved(payload: dict, limit: int = 2) -> list[str]:
    notes: list[str] = []
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    modalities = coverage.get("modalities") if isinstance(coverage.get("modalities"), dict) else {}
    for item in modalities.values():
        if not isinstance(item, dict):
            continue
        for note in item.get("unresolved") or []:
            text = str(note).strip()
            if text and text not in notes:
                notes.append(text)
    # Cosmetic exclusions ("3 decorative images were skipped") are not blockers;
    # a real gap must not be pushed out of the visible line by one of them.
    substantive = [note for note in notes if "excluded" not in note.lower()]
    cosmetic = [note for note in notes if "excluded" in note.lower()]
    return (substantive + cosmetic)[:limit]


def _payload_needs_user_action(payload: dict) -> dict | None:
    def from_details(details) -> dict | None:
        action = details.get("needs_user_action") if isinstance(details, dict) else None
        return action if isinstance(action, dict) else None

    for error in payload.get("errors") or []:
        if isinstance(error, dict):
            action = from_details(error.get("details"))
            if action:
                return action
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    modalities = coverage.get("modalities") if isinstance(coverage.get("modalities"), dict) else {}
    for item in modalities.values():
        if isinstance(item, dict):
            action = from_details(item.get("details"))
            if action:
                return action
    return None


def _action_text(action: dict) -> str:
    parts: list[str] = []
    if action.get("requires_human"):
        parts.append("Ask the user to do this")
    question = str(action.get("question") or "").strip()
    if question:
        parts.append(question)
    options = [item for item in (action.get("options") or []) if isinstance(item, dict)]
    for option in options[:2]:
        kind = str(option.get("kind") or option.get("action") or "").strip()
        # A human action carries prose; an executable shortcut would be a command.
        effect = str(
            option.get("instruction") or option.get("effect") or option.get("message") or ""
        ).strip()
        command = str(option.get("command") or "").strip()
        label = ": ".join(part for part in (kind, effect) if part)
        if command:
            label = f"{label} -> {command}" if label else command
        if label:
            parts.append(label)
    action_id = str(action.get("action_id") or "").strip()
    if action_id:
        parts.append(
            f"Pause id {action_id}, resumable: the user runs "
            f"`envoy auth open --action-id {action_id}` once in their terminal; "
            f"`envoy auth verify --action-id {action_id}` re-checks without a window."
        )
    return " ".join(parts)


def _payload_visual_requirement(payload: dict) -> dict | None:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    modalities = coverage.get("modalities") if isinstance(coverage.get("modalities"), dict) else {}
    for item in modalities.values():
        if not isinstance(item, dict):
            continue
        details = item.get("details")
        requirement = details.get("visual_requirement") if isinstance(details, dict) else None
        if isinstance(requirement, dict):
            return requirement
    return None


def _visual_requirement_text(requirement: dict) -> str:
    block = json.dumps(requirement, ensure_ascii=False, separators=(", ", ": "))
    return (
        f" Frames require {block}; this read omitted that declaration, so no images "
        "were delivered. Re-read with those fields rather than opening the page in "
        "a separate browser view."
    )


def _evidence_text(payload: dict, request) -> str:
    include_visual = bool(getattr(getattr(request, "scope", None), "include_visual", False))
    resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    modalities = coverage.get("modalities") if isinstance(coverage.get("modalities"), dict) else {}
    requirement = _payload_visual_requirement(payload)
    if requirement:
        return _visual_requirement_text(requirement)
    # A text article read with include_visual needs no frame warning; only a source
    # that claimed a visual modality and delivered nothing does.
    visual_source = any(
        name in modalities for name in ("video", "image", "visual")
    )
    if include_visual and visual_source and not resources:
        return (
            " No frames or images were delivered; do not describe on-screen visuals "
            "or shot rhythm as if they were observed."
        )
    return ""


def _tool_content_text(payload: dict, modern: bool, request=None) -> str:
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
    tool = str(payload.get("tool") or "envoy_read")
    if tool == "envoy_discover":
        return _discover_content_text(payload)
    if tool == "envoy_collection_checkpoint":
        return _collection_content_text(payload)
    return _material_content_text(payload, tool, request)


def _content_char_budget(request) -> int:
    """Return the character budget this tool call's own content may use.

    Modern hosts may drop ``structuredContent`` entirely, so the ``content``
    text has to carry the evidence. The budget keeps that text from growing
    past what the caller asked for.
    """
    for attr in ("max_chars", "max_response_chars"):
        value = getattr(request, attr, None)
        if isinstance(value, int) and value > 0:
            return max(1_200, min(value, 12_000))
    return 8_000


def _block_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


def _locator_note(block: dict) -> str:
    locator = block.get("locator")
    if not isinstance(locator, dict):
        return ""
    start = locator.get("start_seconds")
    end = locator.get("end_seconds")
    if start is not None and end is not None:
        return f" t={float(start):.1f}-{float(end):.1f}s"
    page = locator.get("page_number")
    if page is not None:
        return f" p.{page}"
    url = str(locator.get("source_url") or "")
    block_id = str(locator.get("block_id") or "")
    if url:
        return f" {url[:70]}#{block_id}" if block_id else f" {url[:70]}"
    return ""


def _block_line(index: int, total: int, block: dict) -> str:
    block_id = str(block.get("id") or "")
    kind = str(block.get("kind") or "block")
    return f"[{index}/{total}] {block_id} · {kind}{_locator_note(block)}"


# A body can arrive as one enormous line (minified JSON, a single-line CSV).
# Wrapping it keeps the beginning inside the budget instead of spending the
# whole allowance on a header it cannot show.
_FIT_LINE_WIDTH = 240
# Room kept for the closing notice, so the caller learns what was cut instead
# of receiving a response that just stops.
_FIT_NOTICE_ROOM = 400


def _wrap_line(line: str) -> list[str]:
    if len(line) <= _FIT_LINE_WIDTH:
        return [line]
    return [
        line[start:start + _FIT_LINE_WIDTH]
        for start in range(0, len(line), _FIT_LINE_WIDTH)
    ]


def _fit_content(lines: list, budget: int, *, continuation=None) -> list[str]:
    """Return the lines that fit ``budget``, saying honestly what was cut.

    ``lines`` entries are either text, or ``(text, weight)`` where ``weight`` is
    how many characters of the delivered body that line carries. ``continuation``
    receives the number of body characters already shown and returns the next
    step, which is emitted inside the closing notice: a response long enough to
    be truncated must still tell the caller how to read the rest.
    """
    budget = max(int(budget), 0)
    notice_room = min(_FIT_NOTICE_ROOM, max(budget // 4, 120))
    limit = max(budget - notice_room, 1)
    total = 0
    body_total = 0
    for entry in lines:
        text, weight = entry if isinstance(entry, tuple) else (str(entry), 0)
        total += len(text) + 1
        body_total += weight
    out: list[str] = []
    used = 0
    shown = 0
    body_shown = 0
    for entry in lines:
        text, weight = entry if isinstance(entry, tuple) else (str(entry), 0)
        segments = _wrap_line(str(text))
        for segment in segments:
            size = len(segment) + 1
            if used + size > limit:
                matter = (
                    f"showing {body_shown} of {body_total} characters of this material"
                    if body_total
                    else f"showing {shown} of {total} characters"
                )
                notice = f"… output truncated: {matter}"
                if callable(continuation):
                    hint = str(continuation(body_shown)).strip()
                    if hint:
                        notice += f". {hint}"
                out.append(notice)
                return out
            out.append(segment)
            used += size
            shown += size
            body_shown += weight if len(segment) == len(text) else len(segment)
    return out


def _material_header(payload: dict, tool: str) -> str:
    result = str(payload.get("result") or "unknown")
    parts = [f"Envoy {tool.removeprefix('envoy_')} {result}"]
    if payload.get("material_id"):
        parts.append(f"material_id={payload['material_id']}")
    revision = str(payload.get("source_revision") or "")
    if revision:
        parts.append(f"source_revision={revision[:12]}")
    if payload.get("session_id"):
        parts.append(f"session_id={payload['session_id']}")
    return " · ".join(parts)


def _needs_lines(
    needs: list[dict],
    resources: list[dict],
    material_id: str,
    session_id: str,
) -> list[str]:
    action_types = {str(item.get("type")) for item in needs if item.get("type")}
    lines = [""]
    if "inspect_video" in action_types:
        lines.append(
            "inspect the delivered video with native video input; this is a handoff, "
            "not a terminal state"
        )
    if "inspect_visual" in action_types or "inspect_video" not in action_types:
        lines.append(
            "inspect each listed resource before its lease expires; this is a handoff, "
            "not a terminal state"
        )
    lines.append(
        "call envoy_observe once per inspected resource with its matching material_id, "
        "resource_id, source_block_id and original session_id"
    )
    resource_ids: list[str] = []
    source_block_ids: list[str] = []
    for need in needs[:6]:
        kind = str(need.get("type") or "host_action")
        purpose = str(need.get("purpose") or "")
        reason = str(need.get("reason") or "")
        lines.append(f"- need {kind}" + (f" · {purpose}" if purpose else ""))
        if reason:
            lines.append(f"  why: {reason}")
        for resource_id in need.get("resource_ids") or []:
            if str(resource_id) not in resource_ids:
                resource_ids.append(str(resource_id))
        for block_id in need.get("source_block_ids") or []:
            if str(block_id) not in source_block_ids:
                source_block_ids.append(str(block_id))
    for resource in resources[:8]:
        note = f"- resource {resource.get('resource_id')} · {resource.get('mime_type')}"
        if resource.get("expires_at"):
            note += f" · expires {resource.get('expires_at')}"
        delivery = resource.get("delivery")
        path = str(delivery.get("path") or "") if isinstance(delivery, dict) else ""
        if path:
            note += f" · path {path}"
        lines.append(note)
    if not resources:
        for resource_id in resource_ids[:8]:
            lines.append(f"- resource {resource_id}")
    if resource_ids:
        lines.append("")
        for index, resource_id in enumerate(resource_ids[:8]):
            block_id = source_block_ids[index] if index < len(source_block_ids) else ""
            lines.append(
                f'envoy_observe material_id={material_id} resource_id={resource_id} '
                f'source_block_id={block_id} session_id={session_id} '
                'observation="<what you saw>"'
            )
    if any(
        isinstance(resource.get("delivery"), dict) and resource["delivery"].get("path")
        for resource in resources
    ):
        lines.append(
            "each path above is an absolute file on this machine: open it with an image or "
            "file tool to view the original frame, and quote the locator when you cite it"
        )
    lines.append(
        "source URL may be omitted when the material is retained because Envoy derives "
        "and verifies it from the delivered resource"
    )
    lines.append(
        "then answer from those observations; do not repeat envoy_read merely to clear "
        "the original needs_host status"
    )
    lines.append("report uninspected frames and do not imply full-video coverage")
    return lines


def _blocked_lines(payload: dict, errors: list[dict], material_id: str, budget: int) -> list[str]:
    lines: list[str] = []
    for error in errors[:2]:
        lines.append(f"error {error.get('code')}: {error.get('message')}")
        details = error.get("details")
        if isinstance(details, dict):
            ranges = details.get("delivered_ranges")
            if isinstance(ranges, list) and ranges:
                rendered = ", ".join(f"{item[0]}-{item[1]}" for item in ranges[:8] if len(item) == 2)
                lines.append(f"delivered ranges: {rendered}")
            if details.get("text_length"):
                lines.append(f"material length: {details['text_length']} chars")
    expired = any(
        str(error.get("code")) == "expired_resource" for error in errors[:2]
    )
    if expired:
        # The material this call names is gone. Naming idempotency keys or
        # operation ids here would be advice the caller cannot follow: only a
        # fresh read of the source brings the evidence back.
        lines.append("")
        lines.append(
            "next: envoy_read with refresh=true for the same source URL; the "
            "material named above expired or was evicted, so envoy_expand and "
            "envoy_replay cannot read it"
        )
        action = _payload_needs_user_action(payload)
        if action:
            lines.append(f"Requires user action: {_action_text(action)}")
        for note in _payload_unresolved(payload)[:4]:
            lines.append(f"unresolved: {note}")
        return lines
    action = _payload_needs_user_action(payload)
    if action:
        lines.append(f"Requires user action: {_action_text(action)}")
    for note in _payload_unresolved(payload)[:4]:
        lines.append(f"unresolved: {note}")
    if material_id:
        offset = "<chars already delivered>"
        for error in errors[:1]:
            details = error.get("details")
            if isinstance(details, dict):
                delivered = details.get("delivered_ranges")
                if isinstance(delivered, list) and delivered:
                    last = delivered[-1]
                    if isinstance(last, list) and len(last) == 2:
                        offset = str(last[1])
        lines.append("")
        lines.append(
            f"next: envoy_expand material_id={material_id} offset={offset} "
            f"max_chars={budget}"
        )
        lines.append(
            "re-reading the URL is blocked; call envoy_replay with this material_id "
            "in a new session, or envoy_expand for more of the retained material"
        )
    return lines


def _empty_lines(payload: dict) -> list[str]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    modalities = coverage.get("modalities") if isinstance(coverage.get("modalities"), dict) else {}
    text = modalities.get("text") if isinstance(modalities.get("text"), dict) else {}
    details = text.get("details") if isinstance(text.get("details"), dict) else {}
    if "matches_found" in details:
        found = int(details.get("matches_found") or 0)
        if not found:
            lines = ["0 hits: none of the query terms occur in this material"]
            terms = details.get("query_terms")
            if isinstance(terms, list) and terms:
                lines.append("query terms: " + ", ".join(str(term) for term in terms))
            lines.append(
                "next: envoy_search with different terms, or envoy_expand for a raw range"
            )
            return lines
        lines = [
            f"0 new hits; {found} lexical match(es) in this material, "
            f"{details.get('already_returned_matches', 0)} already delivered earlier",
        ]
        terms = details.get("query_terms")
        if isinstance(terms, list) and terms:
            lines.append("query terms: " + ", ".join(str(term) for term in terms))
        lines.append("next: envoy_search with different query terms, or envoy_expand for a raw range")
        return lines
    return ["no content returned for this request"]


def _material_retained(payload: dict) -> bool:
    """Whether the material this payload names is still on disk and unexpired.

    A ``next: envoy_expand`` line must be true when it is printed: telling a
    caller to expand a material that is already gone costs it a round trip that
    can only fail.
    """
    material_id = str(payload.get("material_id") or "").strip()
    revision = str(payload.get("source_revision") or "").strip()
    if not material_id or not revision:
        return False
    if not payload.get("served_from_retained_material"):
        # This payload was produced by the call that is describing it, so the reader
        # wrote the material moments ago. Only a cache serve can name a dead one.
        return True
    session_id = payload.get("session_id")
    try:
        from .reader import material_is_retained
    except Exception:  # pragma: no cover - import cycle or partial install
        return True
    try:
        return bool(
            material_is_retained(
                material_id,
                revision,
                str(session_id) if session_id else None,
            )
        )
    except Exception:  # pragma: no cover - unknown state is not a lie worth making
        return True


def _material_content_text(payload: dict, tool: str, request=None) -> str:
    result = str(payload.get("result") or "unknown")
    blocks = [block for block in payload.get("blocks") or [] if isinstance(block, dict)]
    resources = [item for item in payload.get("resources") or [] if isinstance(item, dict)]
    needs = [item for item in payload.get("needs") or [] if isinstance(item, dict)]
    errors = [item for item in payload.get("errors") or [] if isinstance(item, dict)]
    material_id = str(payload.get("material_id") or "")
    session_id = str(payload.get("session_id") or "")
    budget = _content_char_budget(request)

    head = _material_header(payload, tool)
    if not needs and result != "blocked":
        text_chars = sum(len(_block_text(block)) for block in blocks)
        head += f" · {len(blocks)} blocks / {text_chars} chars"
        if resources:
            head += f" / {len(resources)} resources"
    lines = [head]

    if needs:
        lines.extend(_needs_lines(needs, resources, material_id, session_id))
        return "\n".join(_fit_content(lines, budget))

    if result == "blocked":
        lines.extend(_blocked_lines(payload, errors, material_id, budget))
        return "\n".join(_fit_content(lines, budget))

    if result == "empty":
        lines.extend(_empty_lines(payload))

    for index, block in enumerate(blocks, 1):
        lines.append("")
        lines.append(_block_line(index, len(blocks), block))
        text = _block_text(block)
        lines.append((text, len(text)) if text else "(no text)")

    tail: list[str] = []
    for note in _payload_unresolved(payload)[:4]:
        tail.append(f"Unresolved: {note}")
    visual_note = _evidence_text(payload, request).strip()
    if visual_note:
        tail.append(visual_note)
    if payload.get("served_from_retained_material"):
        tail.append("served from retained material; source not re-fetched")
    action = _payload_needs_user_action(payload)
    if action:
        tail.append(f"Requires user action: {_action_text(action)}")
    if resources:
        ids = ", ".join(str(item.get("resource_id")) for item in resources[:8])
        tail.append(f"resources: {ids}")
    retained = _material_retained(payload) if material_id else False
    if material_id:
        delivered = sum(len(_block_text(block)) for block in blocks)
        if retained:
            tail.append(
                f"next: envoy_expand material_id={material_id} offset={delivered} "
                f"max_chars={budget} (or envoy_search material_id=… query=…); "
                "do not re-read the URL"
            )
        else:
            tail.append(
                "next: envoy_read with refresh=true for the same URL; this result "
                "was not retained, so envoy_expand has no material to read"
            )
    if tail:
        lines.append("")
        lines.extend(tail)

    def continuation(shown: int) -> str:
        if not material_id:
            return ""
        if retained:
            return (
                f"next: envoy_expand material_id={material_id} offset={shown} "
                f"max_chars={budget} to read the rest"
            )
        return "next: envoy_read with refresh=true for the same URL to read it again"

    return "\n".join(_fit_content(lines, budget, continuation=continuation))


def _discover_content_text(payload: dict) -> str:
    candidates = [item for item in payload.get("candidates") or [] if isinstance(item, dict)]
    lines = [
        f"Envoy discover: {len(candidates)} candidate(s); has_more={payload.get('has_more', 'unknown')}",
        "Titles and metadata are selection hints only; read a URL for evidence.",
    ]
    for item in candidates[:20]:
        title = item.get("title") or "(untitled)"
        url = item.get("url") or item.get("source_url") or ""
        lines.append(f"- {title} — {url}")
    return "\n".join(lines)


def _collection_content_text(payload: dict) -> str:
    runs = [item for item in payload.get("runs") or [] if isinstance(item, dict)]
    action = str(payload.get("action") or "updated")
    lines = [f"Envoy collection checkpoint {action} · {len(runs)} run(s)"]
    for run in runs[:20]:
        parts = [
            str(run.get("collection_id") or "?"),
            str(run.get("platform") or "?"),
            f"{run.get('completed', 0)}/{run.get('selected', 0)} done",
        ]
        if run.get("needs_login"):
            parts.append("needs login")
        if run.get("failed"):
            parts.append(f"{run.get('failed')} failed")
        lines.append("- " + " · ".join(parts))
    return "\n".join(lines)


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


# Advanced request fields a host does not send in normal use. They stay
# accepted (the pydantic models are unchanged); they are simply not declared.
# Only genuinely optional fields are listed, and an omitted value must be left
# out rather than filled with `None`. The read schemas declare `retention` and
# `host_capabilities` separately because they gate frame delivery.
_SERVER_FILLED_FIELDS = (
    "idempotency_key",
    "session_id",
    "source_session_id",
    "ocr_profile",
    "ocr_backend",
    "ocr_language",
    "video_subtitle_language",
)
_RANGE_FIELDS = (
    "start_seconds",
    "end_seconds",
    "page_start",
    "page_end",
    "locator",
    "include_visual",
)
# One bridge process serves one host session, so a stable identity keeps
# material scope coherent across calls: re-reading a URL addresses the same
# persisted material instead of minting a request-scoped duplicate. Replay is
# excluded because it is defined by the difference between two sessions.
_PROCESS_SESSION_ID = f"mcp-{uuid.uuid4().hex}"

_LOCAL_FILE_SCHEME = "file://"


def _local_source_path(arguments: dict) -> str | None:
    """Return the on-disk path a caller handed to `envoy_read`, if any.

    A file the user already has is not a public URL, so it cannot be fetched.
    It is read by the same local pipeline `envoy_read_local_video` uses, which
    keeps a bounded core toolset sufficient for local footage: the file is never
    uploaded, only its transcript and any requested frames are returned.
    """

    source = arguments.get("source")
    if not isinstance(source, dict):
        return None
    raw = str(source.get("url") or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(_LOCAL_FILE_SCHEME):
        decoded = unquote(urlparse(raw).path or "") or unquote(raw[len(_LOCAL_FILE_SCHEME):])
        if not decoded:
            return None
        if re.match(r"^/[A-Za-z]:[\\/]", decoded):
            # file:///C:/clip.mp4 -> C:/clip.mp4; the leading slash before a drive
            # letter is URL syntax, not the start of the Windows path.
            decoded = decoded[1:]
        # A URL path is forward-slash by syntax, so a file:// URL keeps that
        # shape on Windows too; callers echo it back as written.
        return Path(decoded).expanduser().as_posix()
    elif raw.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", raw):
        candidate = raw
    else:
        return None
    return str(Path(candidate).expanduser())


def _local_file_identity(path: str) -> str:
    """Stable idempotency identity for a local file, without hashing its bytes."""

    digest = hashlib.sha256()
    digest.update(str(Path(path).expanduser().resolve()).encode("utf-8", "surrogatepass"))
    try:
        stat = Path(path).expanduser().stat()
    except OSError:
        return digest.hexdigest()[:32]
    digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:32]


def _local_video_arguments(read_arguments: dict, path: str) -> dict:
    allowed = set(LocalVideoReadRequest.model_fields)
    converted = {key: value for key, value in read_arguments.items() if key in allowed}
    converted["path"] = path
    if not converted.get("idempotency_key"):
        converted["idempotency_key"] = f"local-{_local_file_identity(path)}"
    return converted


# A read is the expensive part. Once a source has been read in this bridge
# process, a later failure of the same source (auth wall, challenge, transient
# block) must not cost the host the evidence it already received: returning the
# retained material keeps the session movable instead of dead.
#
# Retained material is addressed by the URL's *identity*, not by its text. The
# query string says which item of a site is meant (`?v=<video id>`,
# `?__biz=...&mid=...&idx=...`), so it is kept except for parameters that only
# describe the visit. Keying on scheme+host+path alone served one item's
# material for a different item of the same site.
_BLOCKED_RESULTS = {"blocked", "failed"}
_RETAINABLE_RESULTS = {"ready", "partial", "needs_host"}
_RETAINED_READS: dict[str, dict] = {}

# Parameters that never change which page or media the URL addresses.
_TRACKING_QUERY_PARAMETERS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "utm_social",
    "gclid", "dclid", "fbclid", "msclkid", "yclid", "twclid", "igshid",
    "spm", "spm_id_from", "scm", "from", "from_source", "from_spmid",
    "vd_source", "share_source", "share_medium", "share_plat",
    "share_session_id", "share_tag", "timestamp", "_t", "tt_from",
    "is_from_webapp", "ref", "referrer", "mc_cid", "mc_eid",
})

# Identity often travels in the query string, so a parameter that looks like a
# tracker stays when it is one of these. A wrong reuse serves the wrong item; a
# missed reuse only costs one read, so the tie always goes to reading again.
_IDENTITY_QUERY_PARAMETERS = frozenset({
    "v", "p", "t", "id", "fid", "mid", "sn", "idx", "index", "list", "bvid",
    "aid", "cid", "item_ids", "item_id", "note_id", "video_id", "aweme_id",
    "chksm", "scene", "subscene", "ascene", "devicetype", "version",
    "nettype", "pass_ticket", "wx_header", "sharer_sharetime",
    "sharer_shareid", "sessionid", "feed_id", "article_id", "biz", "appmsgid",
})

_VISUAL_BLOCK_KINDS = frozenset({
    "visual", "image", "images", "frame", "frames", "video_frame", "screenshot",
    "visual_observation",
})
_VIDEO_INPUT_RANK = {"none": 0, "native": 1, "frames": 2}
_IMAGE_INPUT_RANK = {"none": 0, "uri": 1, "local_file": 2}


def _retained_key(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    kept = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name.lower() not in _TRACKING_QUERY_PARAMETERS
        or name.lower() in _IDENTITY_QUERY_PARAMETERS
    ]
    query = urlencode(sorted(kept))
    suffix = f"?{query}" if query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}{suffix}{fragment}"


def _refresh_requested(arguments: dict) -> bool:
    """Only a real boolean opt-in counts; "false" must not read as "refresh"."""

    return arguments.get("refresh") is True


def _request_material_needs(arguments: dict) -> dict:
    scope = arguments.get("scope") if isinstance(arguments.get("scope"), dict) else {}
    budget = arguments.get("budget") if isinstance(arguments.get("budget"), dict) else {}
    host = arguments.get("host_capabilities") if isinstance(arguments.get("host_capabilities"), dict) else {}
    return {
        "start_seconds": scope.get("start_seconds"),
        "end_seconds": scope.get("end_seconds"),
        "page_start": scope.get("page_start"),
        "page_end": scope.get("page_end"),
        "include_visual": bool(scope.get("include_visual")),
        "video_input": str(host.get("video_input") or "none"),
        "image_input": str(host.get("image_input") or "none"),
        "max_response_chars": int(budget.get("max_response_chars") or 12_000),
        "max_response_tokens": budget.get("max_response_tokens"),
        "ocr_profile": arguments.get("ocr_profile"),
        "ocr_backend": arguments.get("ocr_backend"),
        "ocr_language": arguments.get("ocr_language"),
        "video_subtitle_language": arguments.get("video_subtitle_language"),
    }


def _payload_has_visual(payload: dict) -> bool:
    for block in payload.get("blocks") or []:
        if isinstance(block, dict) and str(block.get("kind") or "").lower() in _VISUAL_BLOCK_KINDS:
            return True
    modalities = (payload.get("coverage") or {}).get("modalities") or {}
    if isinstance(modalities, dict):
        for name, item in modalities.items():
            if str(name).lower() not in {
                "visual", "frames", "video_frames", "images", "visual_observation",
            }:
                continue
            if isinstance(item, dict) and any(
                isinstance(item.get(key), int) and item[key] > 0
                for key in ("delivered", "returned", "observed", "extracted")
            ):
                return True
    return False


def _retained_needs_cover(have: dict, need: dict) -> bool:
    if (
        have.get("start_seconds"),
        have.get("end_seconds"),
        have.get("page_start"),
        have.get("page_end"),
    ) != (
        need.get("start_seconds"),
        need.get("end_seconds"),
        need.get("page_start"),
        need.get("page_end"),
    ):
        # Another window is different work; it does not invalidate this one.
        return False
    if need.get("include_visual") and not have.get("include_visual"):
        return False
    if _VIDEO_INPUT_RANK.get(need.get("video_input"), 0) > _VIDEO_INPUT_RANK.get(
        have.get("video_input"), 0
    ):
        return False
    if _IMAGE_INPUT_RANK.get(need.get("image_input"), 0) > _IMAGE_INPUT_RANK.get(
        have.get("image_input"), 0
    ):
        return False
    for key in ("ocr_profile", "ocr_backend", "ocr_language", "video_subtitle_language"):
        if need.get(key) != have.get(key) and (need.get(key) or have.get(key)):
            return False
    return True


def _retained_serves(stored: dict, arguments: dict, compact: bool | None = None) -> bool:
    if compact is not None and bool(stored.get("compact")) != bool(compact):
        # A compact payload omits fields a legacy client expects, and vice versa.
        return False
    need = _request_material_needs(arguments)
    if not _retained_needs_cover(stored.get("needs") or {}, need):
        return False
    # The retained response was shaped under the budget of its own request.
    # A tighter budget now would be violated by returning it unchanged.
    if (stored.get("served_chars") or 0) > need["max_response_chars"]:
        return False
    max_tokens = need["max_response_tokens"]
    if isinstance(max_tokens, int) and max_tokens > 0:
        if (stored.get("served_tokens") or 0) > max_tokens:
            return False
    if need["include_visual"] and not stored.get("visual_delivered"):
        return False
    return True


def _remember_read(arguments: dict, payload: dict, compact: bool | None = None) -> None:
    source = arguments.get("source")
    if not isinstance(source, dict):
        return
    key = _retained_key(str(source.get("url") or ""))
    if not key or payload.get("result") not in _RETAINABLE_RESULTS:
        return
    if not payload.get("material_id"):
        return
    needs = _request_material_needs(arguments)
    existing = _RETAINED_READS.get(key)
    if (
        existing is not None
        and existing.get("visual_delivered")
        and not needs.get("include_visual")
    ):
        # A later narrow read must not evict the frames an earlier read delivered.
        return
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    _RETAINED_READS[key] = {
        "payload": payload,
        "material_id": payload.get("material_id"),
        "source_revision": payload.get("source_revision"),
        "needs": needs,
        "served_chars": len(serialized),
        "served_tokens": _estimated_tokens(serialized),
        "visual_delivered": _payload_has_visual(payload),
        "compact": None if compact is None else bool(compact),
    }


def _retained_read(arguments: dict) -> dict | None:
    source = arguments.get("source")
    if not isinstance(source, dict):
        return None
    return _RETAINED_READS.get(_retained_key(str(source.get("url") or "")))


def _retained_candidate(arguments: dict, compact: bool | None = None) -> dict | None:
    """Retained material that still satisfies this exact request, if any."""
    stored = _retained_read(arguments)
    if stored is None or not _retained_serves(stored, arguments, compact):
        return None
    # Serving it is still right even if its material has since expired: the payload
    # is the content, and a blocked source may leave no other way to get it. The
    # text is what must not point at a dead material (see _material_retained).
    return stored


def _blocked_reason(payload: dict) -> str:
    for key in ("errors", "notices"):
        items = payload.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("message"):
                    return str(item["message"])
    return "the source could not be read again"


def _retained_payload(
    retained: dict,
    exc: Any = None,
    request_id: Any = None,
    blocked: dict | None = None,
) -> dict:
    payload = dict(retained["payload"])
    if request_id is not None:
        payload["request_id"] = str(request_id)
    payload["served_from_retained_material"] = True
    notices = payload.get("notices")
    notice: dict = {
        "code": "served_from_retained_material",
        "message": (
            "The source could not be re-read, so the material already retained "
            "in this session was returned instead; nothing was re-fetched"
        )
        if exc is not None or blocked is not None
        else (
            "This source was read earlier in this session, so the retained material "
            "was returned; nothing was re-fetched. Send refresh=true to read it again"
        ),
    }
    if blocked is not None:
        notice["refetch_error"] = {
            "code": str(blocked.get("result") or "blocked"),
            "message": _blocked_reason(blocked),
        }
    elif exc is not None:
        notice["refetch_error"] = {
            "code": getattr(getattr(exc, "code", None), "value", "unknown"),
            "message": str(exc),
        }
    payload["notices"] = [*(notices if isinstance(notices, list) else []), notice]
    return payload


def _material_handle(payload: dict) -> str:
    material_id = str(payload.get("material_id") or "").strip()
    if not material_id:
        return ""
    revision = str(payload.get("source_revision") or "").strip()
    handle = f" material_id={material_id}"
    if revision:
        handle += f" source_revision={revision}"
    return handle


def _local_file_blocked_payload(request_id: Any, path: str, reason: str, message: str) -> dict:
    return {
        "protocol_version": "envoy.read/1",
        "request_id": str(request_id or "mcp"),
        "execution_state": "completed",
        "result": "blocked",
        "blocks": [],
        "errors": [{
            "code": "source_unavailable",
            "message": message,
            "details": {"kind": "local_file", "reason": reason, "filename": Path(path).name},
        }],
    }


def _local_unsupported_payload(
    request_id: Any,
    path: str,
    kind: str,
    message: str,
) -> dict:
    """A local file Envoy has no reader for: name the boundary, upload nothing."""

    return {
        "protocol_version": "envoy.read/1",
        "request_id": str(request_id or "mcp"),
        "execution_state": "completed",
        "result": "blocked",
        "blocks": [],
        "errors": [{
            "code": "unsupported_source",
            "message": message,
            "details": {
                "kind": "local_file",
                "reason": "unsupported_local_type",
                "detected_kind": kind,
                "filename": Path(path).name,
                "uploaded": False,
            },
        }],
    }


def _auth_payload(arguments: dict) -> dict:
    """Shape one envoy_auth call into a payload every host can render.

    The screenshot path is repeated as plain text because DSH reads only the
    text block and the picture itself is what the user has to see.
    """

    action = str(arguments.get("action") or "").strip()
    action_id = str(arguments.get("action_id") or "").strip()
    url = str(arguments.get("url") or "").strip()
    timeout_seconds = int(arguments.get("timeout_seconds") or 300)
    payload: dict = {"tool": "envoy_auth", "action": action}
    from . import features

    if not features.credential_handoff_enabled():
        payload.update({
            "result": "blocked",
            "credential_handoff": {"enabled": False, "scope": "this machine only"},
            "instruction": (
                "Credential handoff is off by default. Relay this to the user: "
                "they can enable it with `envoy auth handoff on`. Nothing was "
                "opened and no stored session was used."
            ),
        })
        return payload
    if action == "open":
        if not action_id and not url:
            raise ValueError("envoy_auth open needs action_id or url")
        result = auth_session.start(
            url,
            action_id=action_id or None,
            timeout_seconds=timeout_seconds,
            visible=bool(arguments.get("visible")),
        )
        payload.update(result)
        payload["result"] = "ready" if result.get("screen_path") else "starting"
        payload["instruction"] = (
            "show the user the picture at screen_path (it is usually their sign-in "
            "QR code) and ask them to scan or finish the verification; the browser "
            "keeps running in the background. When they say they are done, call "
            "envoy_auth {action:'status', action_id} and then re-read source_url "
            "with envoy_read; never repeat a read before the status says signed_in."
        )
    elif action == "status":
        if not action_id:
            raise ValueError("envoy_auth status needs action_id")
        result = auth_session.status(action_id)
        payload.update(result)
        signed_in = result.get("status") == "signed_in"
        payload["result"] = "ready" if signed_in else "waiting"
        payload["instruction"] = (
            "the sign-in worked and the source can be read now: call envoy_read on "
            "source_url"
            if signed_in
            else "still waiting for the human; show screen_path again if they lost "
            "it, do not re-read the source yet"
        )
    elif action == "cancel":
        if not action_id:
            raise ValueError("envoy_auth cancel needs action_id")
        payload.update(auth_session.stop(action_id))
        payload["result"] = "canceled"
    elif action == "import":
        if not url:
            raise ValueError("envoy_auth import needs url")
        source = str(arguments.get("browser") or "").strip().lower()
        if not source:
            raise ValueError(
                "envoy_auth import needs browser: chrome, edge, brave, vivaldi, "
                "chromium or safari"
            )
        from .cookies import import_cookies

        payload.update(import_cookies(source, source_url=url))
        payload["use_local_store"] = True
        payload["result"] = "ready"
        payload["instruction"] = (
            "the session the user's own browser already holds was copied for this "
            "site only into Envoy's local store (mode 600, never uploaded); call "
            "envoy_read on source_url now. Do not ask for a password and do not "
            "repeat cookie names or values back to the user."
        )
    else:
        raise ValueError("envoy_auth action must be open, status, cancel, or import")
    return payload


# A long video or podcast needs more than the generic read allowance for
# subtitle/ASR work, but only media hosts are granted the extra time: a one-hour
# recording can take several minutes of transcription and would otherwise be
# reported as a budget timeout.
_MEDIA_SOURCE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "bilibili.com",
    "b23.tv",
    "vimeo.com",
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
    "soundcloud.com",
    "ximalaya.com",
    "xiaoyuzhoufm.com",
    "podcasts.apple.com",
    "open.spotify.com",
    "music.163.com",
    "instagram.com",
    "youku.com",
    "v.youku.com",
    "facebook.com",
)
_MEDIA_DEFAULT_WORK_SECONDS = 300


def _media_source_host(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _MEDIA_SOURCE_HOSTS)


def _default_budget_for(name: str, prepared: dict) -> None:
    """Give a media read enough time when the caller did not choose a budget."""

    if name != "envoy_read":
        return
    source = prepared.get("source")
    url = source.get("url") if isinstance(source, dict) else None
    if not _media_source_host(url):
        return
    budget = prepared.get("budget")
    if budget is None:
        prepared["budget"] = {"max_work_seconds": _MEDIA_DEFAULT_WORK_SECONDS}
    elif isinstance(budget, dict) and budget.get("max_work_seconds") is None:
        prepared["budget"] = {**budget, "max_work_seconds": _MEDIA_DEFAULT_WORK_SECONDS}


def _server_arguments(name: str, arguments: dict) -> dict:
    """Complete a compact tool call into the full request model accepts.

    Adds the request envelope the server owns (`request_id`, `protocol_version`)
    and any omitted advanced block. Every caller-provided value is passed
    through untouched, so an explicit `budget`, `scope`, or `session_id` is
    validated and honoured exactly as before.
    """

    prepared = dict(arguments)
    model = {
        "envoy_read": ReadRequest,
        "envoy_read_local_video": LocalVideoReadRequest,
        "envoy_expand": ExpandRequest,
        "envoy_replay": MaterialReplayRequest,
        "envoy_search": MaterialSearchRequest,
        "envoy_observe": VisualObservationRequest,
        "envoy_visual_history": VisualObservationHistoryRequest,
    }.get(name)
    if model is not None:
        fields = set(model.model_fields)
        version_field = model.model_fields.get("protocol_version")
        default_version = getattr(version_field, "default", PROTOCOL_VERSION)
        prepared.setdefault("protocol_version", default_version)
        prepared.setdefault("request_id", f"mcp-{uuid.uuid4().hex}")
        for field in _SERVER_FILLED_FIELDS:
            if field in fields:
                prepared.setdefault(field, None)
        if "session_id" in fields and name != "envoy_replay":
            if prepared.get("session_id") is None:
                prepared["session_id"] = _PROCESS_SESSION_ID
        if name == "envoy_replay":
            # Replay targets a fresh session. The host session id the model holds
            # is not the id Envoy minted for the old material, so a model-supplied
            # value there is unusable: default to this bridge process and let the
            # reader derive the source session from the retained material.
            if not prepared.get("session_id"):
                prepared["session_id"] = _PROCESS_SESSION_ID
            # An omitted source session means "the retained material's own
            # session"; the reader derives it. An explicit value is still
            # validated so a wrong guess fails loudly with a next action.
            if not prepared.get("source_session_id"):
                prepared.pop("source_session_id", None)
    if name in {"envoy_read", "envoy_read_local_video"}:
        flattened = {key: prepared.pop(key) for key in _RANGE_FIELDS if key in prepared}
        scope = prepared.get("scope")
        if isinstance(scope, dict):
            merged = dict(scope)
            for key, value in flattened.items():
                merged.setdefault(key, value)
        else:
            merged = {"kind": "range", **flattened} if flattened else None
        if merged is not None:
            prepared["scope"] = merged
    if name == "envoy_read":
        _default_budget_for(name, prepared)
    return prepared


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
    if isinstance(name, str):
        name = TOOL_ALIASES.get(name, name)
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
                    "hint": (
                        "Restart the MCP server with --toolset full for advanced tools, "
                        "or use the equivalent `envoy` CLI subcommand."
                    ),
                },
            )
        return _error(
            request_id,
            -32602,
            f"Unknown tool: {name!r}",
            {"toolset": selected_toolset, "available": list(TOOLSETS[selected_toolset])},
        )
    try:
        arguments = _server_arguments(name, arguments)
        if name == "envoy_read":
            fresh_read = False
            local_path = _local_source_path(arguments)
            if local_path is None:
                retained = (
                    None
                    if _refresh_requested(arguments)
                    else _retained_candidate(arguments, modern_request)
                )
                if retained is not None:
                    payload = _retained_payload(retained, request_id=arguments.get("request_id"))
                else:
                    read_arguments = {
                        key: value for key, value in arguments.items() if key != "refresh"
                    }
                    response = read_public_page(ReadRequest.model_validate(read_arguments))
                    payload = _read_payload(response, compact=modern_request)
                    fresh_read = True
                    if payload.get("result") in _BLOCKED_RESULTS:
                        # A re-read that is blocked must not lose evidence that is
                        # still valid in this session.
                        retained = _retained_candidate(arguments, modern_request)
                        if retained is not None:
                            payload = _retained_payload(
                                retained,
                                request_id=arguments.get("request_id"),
                                blocked=payload,
                            )
                            # The payload is the earlier request's material: its
                            # needs must not be recorded as this request's needs.
                            fresh_read = False
            else:
                path = Path(local_path)
                if not path.is_file():
                    payload = _local_file_blocked_payload(
                        request_id,
                        local_path,
                        "not_found",
                        (
                            "The local file was not found at the path Envoy was given; "
                            "no reading was attempted and nothing was uploaded"
                        ),
                    )
                elif not os.access(path, os.R_OK):
                    payload = _local_file_blocked_payload(
                        request_id,
                        local_path,
                        "not_readable",
                        "The local file exists but is not readable by Envoy's process",
                    )
                else:
                    explicit_kind = None
                    requested_source = arguments.get("source")
                    if isinstance(requested_source, dict):
                        explicit_kind = str(requested_source.get("kind") or "").strip() or None
                    decision = classify_local_source(local_path, explicit_kind=explicit_kind)
                    if decision.kind == "pdf":
                        response = read_local_pdf(
                            local_read_request(arguments, str(decision.path), kind="document"),
                            decision.path,
                        )
                    elif decision.kind == "video":
                        response = read_local_video(
                            LocalVideoReadRequest.model_validate(
                                _local_video_arguments(arguments, local_path)
                            )
                        )
                    else:
                        response = None
                        payload = _local_unsupported_payload(
                            request_id,
                            local_path,
                            decision.kind,
                            decision.message,
                        )
                    if response is not None:
                        payload = _read_payload(response, compact=modern_request)
                        fresh_read = True
            if fresh_read:
                _remember_read(arguments, payload, modern_request)
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
            if action == "list":
                payload = {
                    "tool": "envoy_collection_checkpoint",
                    "action": "list",
                    "runs": list_collections(),
                    "resultType": "complete",
                }
            elif action == "create":
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
        elif name == "envoy_observe":
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
        elif name == "envoy_auth":
            payload = _auth_payload(arguments)
        else:
            return _error(request_id, -32602, f"Unknown tool: {name}")
    except PublicPageError as exc:
        # A failed refetch falls back to this session's material even when the
        # caller asked to refresh: the notice marks it and the error is returned.
        retained = _retained_candidate(arguments, modern_request)
        if retained is not None:
            payload = _retained_payload(retained, exc, request_id=arguments.get("request_id"))
        else:
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
    except (state_storage.StateUnavailable, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno not in state_storage.UNUSABLE_ERRNOS:
            _LOGGER.exception("Unexpected Envoy MCP tool failure: %s", name)
            return _error(
                request_id,
                -32603,
                "Internal error while handling MCP tool call",
            )
        payload = _state_not_writable_payload(
            arguments.get("request_id", "mcp"),
            str(exc) if isinstance(exc, state_storage.StateUnavailable) else (
                f"Envoy cannot write {exc.filename or state_storage.preferred_root()}: "
                f"{exc.strerror or exc}. Point ENVOY_LOCAL_DATA_DIR at a writable directory."
            ),
        )
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
            "text": _tool_content_text(payload, modern_request, locals().get("request")),
        }],
        "structuredContent": payload,
        "isError": payload.get("result") == "blocked",
    }, modern=modern_request)


def _configure_stdio() -> None:
    """Keep JSON-RPC on UTF-8 even when the host process asks for a legacy codec.

    A Windows host may launch us with ``PYTHONIOENCODING=cp1252``; writing Chinese
    source text then raised UnicodeEncodeError in the middle of a response. Switch
    the standard streams to UTF-8 and replace what still cannot be encoded instead
    of losing the response.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            _LOGGER.debug("Could not reconfigure stdio stream", exc_info=True)


def _emit_line(output_stream: TextIO, text: str) -> None:
    """Write one JSON-RPC line, falling back to UTF-8 bytes if the codec rejects it.

    Encodability is checked before touching the stream: writing first and catching
    UnicodeEncodeError leaves a TextIOWrapper in a half-written state.
    """
    line = text + "\n"
    encoding = getattr(output_stream, "encoding", None) or "utf-8"
    try:
        line.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        buffer = getattr(output_stream, "buffer", None)
        if buffer is None:
            output_stream.write(line.encode("utf-8", "replace").decode(encoding, "replace"))
        else:
            buffer.write(line.encode("utf-8"))
    else:
        output_stream.write(line)
    output_stream.flush()


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
            _emit_line(sys.stdout, json.dumps(response, ensure_ascii=False))
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
            _emit_line(output_stream, json.dumps(_error(None, -32700, "Parse error")))
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
            _emit_line(output_stream, json.dumps(response, ensure_ascii=False))
    return 0


def main(toolset: str | None = None) -> int:
    _configure_stdio()
    try:
        selected_toolset = normalize_toolset(toolset)
    except ValueError as exc:
        print(f"envoy mcp: {exc}", file=sys.stderr)
        return 2
    return run_stream(sys.stdin, sys.stdout, selected_toolset)
