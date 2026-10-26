"""Cross-host invariant: the tool result itself carries the evidence.

Hosts disagree about which field reaches the model. DSH forwards the `content`
text blocks and drops `structuredContent`; Codex CLI and Claude Code forward
`structuredContent` as JSON and drop the text blocks. A receipt that names the
material but not its text therefore breaks whichever host reads the other
field, so both fields must carry the same evidence: the block text, the search
hits, the resource ids, and the call the model is expected to make next.
"""

import json
from types import SimpleNamespace

from envoy_cli import mcp


def _request(include_visual: bool = False):
    return SimpleNamespace(scope=SimpleNamespace(include_visual=include_visual))


def _read_payload() -> dict:
    return {
        "tool": "envoy_read",
        "result": "ready",
        "material_id": "mat-abc123",
        "source_revision": "0123456789ab" * 3,
        "session_id": "sess-1",
        "blocks": [
            {
                "id": "blk-0001",
                "kind": "paragraph",
                "order": 0,
                "content": {"type": "text", "text": "The needle sentence lives here."},
            },
            {
                "id": "blk-0002",
                "kind": "paragraph",
                "order": 33,
                "content": {"type": "text", "text": "A second paragraph follows."},
            },
        ],
        "resources": [],
        "coverage": {"modalities": {"text": {"extraction": "full"}}},
    }


def test_content_text_carries_every_block_text_for_modern_hosts() -> None:
    text = mcp._tool_content_text(_read_payload(), True, _request())

    assert "The needle sentence lives here." in text
    assert "A second paragraph follows." in text
    assert "blk-0001" in text


def test_legacy_json_response_carries_the_same_block_text() -> None:
    text = mcp._tool_content_text(_read_payload(), False, _request())

    payload = json.loads(text)
    assert payload["blocks"][0]["content"]["text"] == "The needle sentence lives here."
    assert payload["material_id"] == "mat-abc123"


def test_content_text_carries_the_material_handle_and_next_step() -> None:
    text = mcp._tool_content_text(_read_payload(), True, _request())

    assert "material_id=mat-abc123" in text
    assert "source_revision=0123456789ab" in text
    assert "session_id=sess-1" in text
    assert "envoy_expand material_id=mat-abc123" in text
    assert "do not re-read the URL" in text


def test_search_text_carries_hit_text_and_offsets() -> None:
    payload = {
        "tool": "envoy_search",
        "result": "partial",
        "material_id": "mat-abc123",
        "source_revision": "r" * 40,
        "session_id": "sess-1",
        "blocks": [
            {
                "id": "blk-search-0001",
                "kind": "paragraph",
                "order": 512,
                "content": {"type": "text", "text": "Hit: NEEDLE=42 near the offset."},
            }
        ],
        "resources": [],
        "coverage": {"modalities": {"text": {"extraction": "partial"}}},
    }

    text = mcp._tool_content_text(payload, True, _request())

    assert "Hit: NEEDLE=42 near the offset." in text
    assert "mat-abc123" in text
    assert "512" in text or "0-512" in text or "blk-search-0001" in text


def test_needs_host_text_carries_the_observe_call_shape() -> None:
    payload = {
        "tool": "envoy_read",
        "result": "needs_host",
        "execution_state": "waiting_for_host",
        "material_id": "mat-abc123",
        "source_revision": "r" * 40,
        "session_id": "sess-1",
        "blocks": [],
        "resources": [
            {
                "resource_id": "res-0001",
                "mime_type": "image/jpeg",
                "source_url": "https://example.org/frame-1.jpg",
                "delivery": {"type": "local_file", "path": "/Users/apple/.envoy/materials/frames-mat-abc/frame-001.jpg"},
            }
        ],
        "needs": [
            {
                "type": "inspect_visual",
                "purpose": "describe the delivered frame",
                "resource_ids": ["res-0001"],
                "source_block_ids": ["blk-video-0001"],
            }
        ],
    }

    text = mcp._tool_content_text(payload, True, _request(include_visual=True))

    assert "res-0001" in text
    assert "blk-video-0001" in text
    assert "envoy_observe material_id=mat-abc123" in text
    assert "session_id=sess-1" in text
    assert "do not repeat envoy_read" in text
    assert "handoff" in text


def test_needs_host_text_names_the_original_frame_path() -> None:
    payload = {
        "tool": "envoy_read",
        "result": "needs_host",
        "execution_state": "waiting_for_host",
        "material_id": "mat-abc123",
        "source_revision": "r" * 40,
        "session_id": "sess-1",
        "blocks": [],
        "resources": [
            {
                "resource_id": "res-0001",
                "mime_type": "image/jpeg",
                "delivery": {
                    "type": "local_file",
                    "path": "/Users/apple/.envoy/materials/frames-mat-abc/frame-001.jpg",
                },
            }
        ],
        "needs": [
            {
                "type": "inspect_visual",
                "resource_ids": ["res-0001"],
                "source_block_ids": ["blk-video-frame-001"],
            }
        ],
    }

    text = mcp._tool_content_text(payload, True, _request(include_visual=True))

    assert "/Users/apple/.envoy/materials/frames-mat-abc/frame-001.jpg" in text
    assert "open it with an image or file tool" in text


def test_empty_search_text_explains_zero_matches_instead_of_saying_empty() -> None:
    payload = {
        "tool": "envoy_search",
        "result": "empty",
        "material_id": "mat-abc123",
        "blocks": [],
        "resources": [],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "matches_found": 0,
                        "already_returned_matches": 0,
                        "query_terms": ["zzz"],
                    }
                }
            }
        },
    }

    text = mcp._tool_content_text(payload, True, _request())

    assert "0 hits" in text
    assert "zzz" in text
    assert "envoy_search with different terms" in text
