"""Cross-host invariant: the tool result itself carries the evidence.

Hosts disagree about which field reaches the model. DSH forwards the `content`
text blocks and drops `structuredContent`; Codex CLI and Claude Code forward
`structuredContent` as JSON and drop the text blocks. A receipt that names the
material but not its text therefore breaks whichever host reads the other
field, so both fields must carry the same evidence: the block text, the search
hits, the resource ids, and the call the model is expected to make next.
"""

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


def test_legacy_response_carries_the_same_block_text() -> None:
    """A legacy host gets the readable rendering, not a JSON dump.

    Claude Code and Codex CLI-class hosts do not negotiate modern `_meta`, and
    the escaped dump they used to receive made a 9,600-character article a
    39,000-character wall the host cuts in the middle of a record, with no
    readable continuation. Both fields still carry the same evidence; only the
    framing changed.
    """
    text = mcp._tool_content_text(_read_payload(), False, _request())

    assert "mat-abc123" in text
    assert "The needle sentence lives here." in text
    assert not text.lstrip().startswith("{")


def test_rendered_text_ignores_a_small_requested_budget_when_the_reader_shipped_more() -> None:
    """The requested budget sizes the *reader*, not the receipt.

    A caller that asks for 4,000 characters already gets a cut material with a
    real `next_offset`. Re-cutting the rendered answer to the same number would
    make it shorter than its own delivery record, and `envoy_expand` refuses
    that missing tail as already delivered.
    """
    body = "long paragraph. " * 90  # 1,440 characters
    payload = _read_payload()
    payload["blocks"] = [
        {
            "id": "blk-0001",
            "kind": "paragraph",
            "content": {"type": "text", "text": body},
        }
    ]
    payload["coverage"] = {"modalities": {"text": {"extraction": "full"}}}

    request = _request()
    request.max_chars = 4_000
    text = mcp._tool_content_text(payload, True, request)

    assert "truncated" not in text
    # Long lines are wrapped at 240 characters, so compare the text itself.
    assert "".join(body.split()) in "".join(text.split())


def test_rendered_answer_is_never_shorter_than_its_delivery_record() -> None:
    """A fully delivered material must be rendered in full.

    Measured on usemagpie.ai/docs/zh/intent: the reader shipped 9,631
    characters and the renderer cut at 8,000, so the answer was 1,631
    characters shorter than the coverage record claimed and the missing tail
    could not be fetched again. The budget has to cover the shipped text plus
    the block lines.
    """
    body = "".join(f"paragraph {index} sentence. " for index in range(400))
    payload = _read_payload()
    payload["blocks"] = [
        {
            "id": f"blk-{index:04d}",
            "kind": "paragraph",
            "content": {"type": "text", "text": body},
            "locator": {"source_url": "https://example.org/article"},
        }
        for index in range(1)
    ]
    payload["coverage"] = {"modalities": {"text": {"extraction": "full"}}}

    text = mcp._tool_content_text(payload, True, _request())

    assert "truncated" not in text
    # Long lines are wrapped at 240 characters, so compare the text itself.
    assert "".join(body.split()) in "".join(text.split())
    assert "delivered the rest" in text


def test_content_text_carries_the_material_handle_and_next_step() -> None:
    text = mcp._tool_content_text(_read_payload(), True, _request())

    assert "material_id=mat-abc123" in text
    assert "source_revision=0123456789ab" in text
    assert "session_id=sess-1" in text
    # This payload reports full text coverage and no next_offset, so the receipt
    # must say the material was delivered whole instead of inventing an offset
    # that would send the caller back over text it already has.
    assert "delivered the rest of the material" in text
    assert "offset=58" not in text
    # A retained handle is a lease, not a permanent citation: say so where the
    # model is told to keep the handle instead of the words.
    assert "keep the quoted words" in text


def test_search_text_carries_hit_text_and_offsets() -> None:
    payload = {
        "tool": "envoy_search",
        "result": "partial",
        "material_id": "mat-abc123",
        "source_revision": "r" * 40,
        "session_id": "sess-1",
        "blocks": [
            {
                "id": "blk-search-4f0b9d2c77aa10e5",
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
    assert "512" in text or "0-512" in text or "blk-search-4f0b9d2c77aa10e5" in text


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
                "delivery": {"type": "local_file", "path": "/home/user/.envoy/materials/frames-mat-abc/frame-001.jpg"},
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
                    "path": "/home/user/.envoy/materials/frames-mat-abc/frame-001.jpg",
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

    assert "/home/user/.envoy/materials/frames-mat-abc/frame-001.jpg" in text
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
