"""A read whose material expired must recover, not tell the caller to do the impossible.

Two defects are pinned here. First, a reused bridge entry could point at material
that had already been evicted, so the host was told to call `envoy_expand` on a
dead id and got `expired_resource` back. Second, a one-line JSON body (70 KB on
one line, as `curl` returns it) was cut off at the content budget without saying
what was missing or how to continue, so no entry in it could be located.
"""

from __future__ import annotations

import json
import types


from envoy_cli import mcp, reader
from envoy_core.schemas.read_v1 import ReadRequest


def _request(**overrides) -> ReadRequest:
    payload = {
        "protocol_version": "envoy.read/1",
        "request_id": overrides.pop("request_id", "recovery"),
        "source": {"url": overrides.pop("url", "https://api.example.com/data.json")},
        "goal": "locate an entry",
        "budget": {"max_work_seconds": 30},
        **overrides,
    }
    return ReadRequest.model_validate(payload)


def _json_source(rows: int = 1200) -> bytes:
    # Each row carries prose: a body of short machine fields is (correctly) read as
    # an API status stub, not as readable content, and would never reach the blocks.
    body = {
        "rows": [
            {"id": index, "name": f"row-{index}", "text": f"row {index} " + "lorem ipsum " * 4}
            for index in range(rows)
        ]
    }
    return json.dumps(body).encode()


def test_pretty_json_turns_one_line_into_addressable_lines():
    compact = _json_source(20).decode()

    pretty = reader._pretty_json_text(compact)

    assert pretty != compact
    assert pretty.count("\n") > 20
    assert json.loads(pretty) == json.loads(compact)
    assert '"name": "row-19"' in pretty


def test_pretty_json_leaves_non_json_and_broken_input_alone():
    assert reader._pretty_json_text("<html>not json</html>") == "<html>not json</html>"
    assert reader._pretty_json_text('{"broken": ') == '{"broken": '
    # A bare scalar has no structure to spread over lines.
    assert reader._pretty_json_text('"just a string"') == '"just a string"'
    # An array of scalars is still a document with structure, so it is spread out.
    pretty_list = reader._pretty_json_text("[1, 2, 3]")
    assert pretty_list.count("\n") == 4
    assert json.loads(pretty_list) == [1, 2, 3]


def test_pretty_json_keeps_the_original_when_it_would_exceed_the_source_limit():
    # A JSON body whose pretty form crosses MAX_TEXT_CHARS must not change what
    # "truncated" means for the rest of the reader.
    # 110k short keys: ~1.6 MB compact, ~2.1 MB pretty-printed (over the cap).
    body = {f"k{index}": index for index in range(110_000)}
    compact = json.dumps(body, separators=(",", ":"))
    assert len(compact) < reader.MAX_TEXT_CHARS
    assert len(json.dumps(body, indent=2)) > reader.MAX_TEXT_CHARS

    assert reader._pretty_json_text(compact) == compact


def test_json_text_source_delivers_pretty_lines():
    body = _json_source(30)

    response = reader._parse_text_source(
        _request(),
        {
            "url": "https://api.example.com/data.json",
            "data": body,
            "mime_type": "application/json",
            "encoding": "utf-8",
        },
    )

    # blocks[].content is a pydantic model, not a dict.
    delivered = "\n".join(str(getattr(block.content, "text", "") or "") for block in response.blocks)
    assert '"name": "row-29"' in delivered
    assert delivered.count("\n") >= 20


def test_material_is_retained_reports_a_missing_material():
    assert reader.material_is_retained("no-such-material", "revision", "session") is False
    assert reader.material_is_retained("", "revision", "session") is False


def test_material_retained_flag_follows_the_stored_text_source():
    response = reader._parse_text_source(
        _request(request_id="retained-check"),
        {
            "url": "https://api.example.com/retained.json",
            "data": _json_source(10),
            "mime_type": "application/json",
            "encoding": "utf-8",
        },
    )

    assert response.material_id
    assert reader.material_is_retained(
        response.material_id, response.source_revision, response.session_id
    ) is True


def test_expired_expand_tells_the_caller_to_read_again_instead_of_expanding():
    lines = mcp._blocked_lines(
        {"result": "blocked", "errors": []},
        [
            {
                "code": "expired_resource",
                "message": "Material is missing or expired",
                "details": {"delivered_ranges": [[0, 4000]], "text_length": 70555},
            }
        ],
        "mat-dead",
        8000,
    )
    rendered = "\n".join(lines)

    assert "delivered ranges: 0-4000" in rendered
    assert "envoy_read with refresh=true" in rendered
    # The whole point: no command that needs the dead material is offered.
    assert "envoy_expand material_id=mat-dead" not in rendered


def test_other_blocked_expands_still_name_the_next_range():
    rendered = "\n".join(
        mcp._blocked_lines(
            {"result": "blocked"},
            [
                {
                    "code": "budget_exhausted",
                    "message": "the response budget is spent",
                    "details": {"delivered_ranges": [[0, 4000]], "text_length": 9000},
                }
            ],
            "mat-live",
            8000,
        )
    )

    assert "envoy_expand material_id=mat-live offset=4000 max_chars=8000" in rendered
    assert "envoy_read with refresh=true" not in rendered


def test_fit_content_wraps_a_one_line_json_body_and_says_what_was_cut():
    body = _json_source(1200).decode()
    assert "\n" not in body
    continuation_calls: list[int] = []

    def continuation(delivered: int) -> str:
        continuation_calls.append(delivered)
        return f"next: envoy_expand material_id=mat-1 offset={delivered} max_chars=8000"

    lines = mcp._fit_content(
        [("Envoy read ready material_id=mat-1", 0), (body, len(body))],
        mcp._content_char_budget(types.SimpleNamespace(max_chars=None)),
        continuation=continuation,
    )
    rendered = "\n".join(lines)

    assert lines[0] == "Envoy read ready material_id=mat-1"
    assert "output truncated" in rendered
    assert f"of {len(body)} characters of this material" in rendered
    assert "next: envoy_expand material_id=mat-1 offset=" in rendered
    assert continuation_calls and 0 < continuation_calls[0] < len(body)
    assert all(len(line) <= mcp._FIT_LINE_WIDTH + len("… output truncated") + 200 for line in lines)
    # Wrapping is what makes the body addressable: the JSON key appears in a line
    # of its own rather than 5000 characters into a single delivered line.
    assert any('"name": "row-' in line for line in lines)


def test_fit_content_keeps_everything_when_it_fits():
    lines = mcp._fit_content(["header", ("short body", 10)], 8000, continuation=lambda n: "unused")

    assert lines == ["header", "short body"]


def test_material_content_text_truncation_keeps_a_continuation_hint():
    body = _json_source(1200).decode()
    payload = {
        "result": "ready",
        "material_id": "mat-1",
        "source_revision": "a" * 64,
        "session_id": "session-1",
        "blocks": [
            {
                "id": "blk-1",
                "kind": "paragraph",
                "content": {"type": "text", "text": body},
                "origin": "source_text",
                "locator": {"type": "web", "source_url": "https://api.example.com/data.json"},
                "order": 0,
            }
        ],
        "coverage": {"modalities": {"text": {"delivered": 1, "returned": 1}}},
        # A cache serve outlives the material lease: this payload was still in
        # _RETAINED_READS after its material had been evicted from disk.
        "served_from_retained_material": True,
    }
    request = types.SimpleNamespace(max_chars=2000)

    text = mcp._material_content_text(payload, "envoy_read", request)

    assert "Envoy read ready" in text
    assert "output truncated" in text
    # The material is gone, so the only honest continuation is a re-read;
    # envoy_expand would fail with expired_resource.
    assert "envoy_read with refresh=true" in text
    assert "envoy_expand" not in text
    assert max(len(line) for line in text.splitlines()) < 600


def test_material_content_text_offers_expand_when_the_material_is_retained(monkeypatch):
    body = _json_source(1200).decode()
    payload = {
        "result": "ready",
        "material_id": "mat-1",
        "source_revision": "a" * 64,
        "session_id": "session-1",
        "blocks": [
            {
                "id": "blk-1",
                "kind": "paragraph",
                "content": {"type": "text", "text": body},
                "origin": "source_text",
                "locator": {"type": "web", "source_url": "https://api.example.com/data.json"},
                "order": 0,
            }
        ],
        "coverage": {"modalities": {"text": {"delivered": 1, "returned": 1}}},
    }
    monkeypatch.setattr(mcp, "_material_retained", lambda payload: True)

    text = mcp._material_content_text(
        payload, "envoy_read", types.SimpleNamespace(max_chars=2000)
    )

    assert "envoy_expand material_id=mat-1" in text
    assert "envoy_read with refresh=true" not in text
