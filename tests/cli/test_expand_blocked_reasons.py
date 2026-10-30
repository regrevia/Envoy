"""A blocked expand must say why it was blocked and where readable text resumes.

Session d91 showed the failure mode this guards: repeating an offset reported
``budget_exceeded: overlaps an earlier response`` and then recommended
``envoy_replay``, so the caller had no way to learn that the unread text simply
started somewhere else.
"""

TEXT = (
    "Paragraph one explains the setting.\n\n"
    "Paragraph two states the claim that matters here.\n\n"
    "Paragraph three adds the caveat the caller still needs.\n"
)


def _store(tmp_path, monkeypatch):
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "3" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/report",
        "source_revision": "a" * 64,
        "title_text": "Report",
        "text": TEXT,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 8,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })
    return material_id


def _expand(material_id, offset, request_id, max_chars=4000):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    return reader.expand_material(ExpandRequest.model_validate({
        "request_id": request_id,
        "material_id": material_id,
        "offset": offset,
        "max_chars": max_chars,
    }))


def _payload(response):
    payload = response.model_dump(mode="json")
    payload.setdefault("served_from_retained_material", False)
    return payload


def test_repeating_a_delivered_offset_names_the_next_undelivered_one(tmp_path, monkeypatch):
    material_id = _store(tmp_path, monkeypatch)
    first = _expand(material_id, 0, "req-first", max_chars=128)
    assert first.result.value in {"ready", "partial"}
    assert first.blocks
    assert len("".join(block.content.text for block in first.blocks)) < len(TEXT)

    repeated = _expand(material_id, 0, "req-repeat")
    assert repeated.result.value == "blocked"
    error = repeated.errors[0]
    assert error.code.value == "range_already_delivered"
    assert error.details["reason"] == "range_already_delivered"
    next_offset = error.details["next_offset"]
    assert next_offset == error.details["delivered_ranges"][-1][1]
    assert str(next_offset) in error.message

    from envoy_cli import mcp

    text = "\n".join(mcp._blocked_lines(
        _payload(repeated), [error.model_dump(mode="json")], material_id, 800
    ))
    assert f"envoy_expand material_id={material_id} offset={next_offset}" in text
    assert "already returned earlier" in text
    # The old tail told the caller to replay a material it had just tried to
    # expand; that advice has to be gone on this path.
    assert "call envoy_replay" not in text


def test_expanding_a_fully_delivered_material_does_not_ask_for_another_expand(tmp_path, monkeypatch):
    material_id = _store(tmp_path, monkeypatch)
    first = _expand(material_id, 0, "req-whole", max_chars=4000)
    assert first.result.value in {"ready", "partial"}
    assert "".join(block.content.text for block in first.blocks).strip()

    again = _expand(material_id, 0, "req-whole-again", max_chars=4000)
    assert again.result.value == "blocked"
    error = again.errors[0]
    assert error.code.value == "range_already_delivered"
    assert error.details["reason"] == "material_fully_delivered"
    assert "next_offset" not in error.details

    from envoy_cli import mcp

    text = "\n".join(mcp._blocked_lines(
        _payload(again), [error.model_dump(mode="json")], material_id, 800
    ))
    assert "fully delivered" in text
    assert "next: envoy_expand" not in text


def test_a_fully_delivered_material_is_not_reported_as_a_budget_wall(tmp_path, monkeypatch):
    """Nothing is left to read, so the ledger must not describe hidden text.

    Session d91 read a report to the end and then got "cumulative response
    character budget has been exhausted" on the next expand, which reads as
    "more text exists, the budget is in the way" — the opposite of the truth.
    """
    from envoy_cli import reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "5" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/report",
        "source_revision": "b" * 64,
        "title_text": "Report",
        "text": TEXT,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 8,
            "returned_chars": 4000,
            "returned_tokens": 1000,
            "expansion_rounds": 1,
            "returned_ranges": [[0, len(TEXT)]],
        },
    })

    blocked = _expand(material_id, 0, "req-done")
    assert blocked.result.value == "blocked"
    error = blocked.errors[0]
    assert error.details["reason"] == "material_fully_delivered"
    assert "budget" not in error.message.lower()

    from envoy_cli import mcp

    text = "\n".join(mcp._blocked_lines(
        _payload(blocked), [error.model_dump(mode="json")], material_id, 800
    ))
    assert "fully delivered" in text
    assert "budget" not in text.lower()


def test_a_text_need_gets_no_observe_or_frame_boilerplate():
    from envoy_cli import mcp

    needs = [{
        "type": "authenticate",
        "purpose": "the page requires a signed-in session",
        "reason": "login wall",
    }]
    text = "\n".join(mcp._needs_lines(needs, [], "mat-" + "4" * 24, "sess-1"))
    assert "envoy_observe" not in text
    assert "report uninspected frames" not in text
    assert "envoy_read again for the same URL" in text
    assert "login wall" in text


def test_a_visual_need_still_gets_the_observe_instructions():
    from envoy_cli import mcp

    needs = [{
        "type": "inspect_visual",
        "purpose": "one frame per chapter",
        "resource_ids": ["res-1"],
        "source_block_ids": ["blk-image-0001"],
    }]
    resources = [{
        "resource_id": "res-1",
        "mime_type": "image/jpeg",
        "delivery": {"path": "/tmp/frame.jpg"},
    }]
    text = "\n".join(mcp._needs_lines(needs, resources, "mat-" + "5" * 24, "sess-1"))
    assert "envoy_observe" in text
    assert "report uninspected frames" in text
    assert "/tmp/frame.jpg" in text


def test_replay_does_not_require_a_session_the_bridge_would_overwrite():
    from envoy_cli import mcp

    schema = mcp.REPLAY_SCHEMA
    assert set(schema["required"]) == {"material_id", "source_revision"}
    assert "source_revision" in schema["properties"]["source_revision"]["description"]
    assert "omit" in schema["properties"]["session_id"]["description"].lower()


def test_a_search_with_no_new_hits_points_at_the_earlier_response():
    from envoy_cli import mcp

    payload = {"coverage": {"modalities": {"text": {"details": {
        "matches_found": 11,
        "already_returned_matches": 11,
        "query_terms": ["needle"],
    }}}}}
    text = "\n".join(mcp._empty_lines(payload))
    assert "11 already delivered earlier" in text
    assert "from the earlier response" in text
    assert "coverage.next_offset" in text
    # Repeating the search with other words cannot recover delivered passages.
    assert "different query terms" not in text

    with_offset = {"coverage": {"modalities": {"text": {"details": {
        "matches_found": 2,
        "already_returned_matches": 2,
        "next_offset": 900,
    }}}}}
    offset_text = "\n".join(mcp._empty_lines(with_offset))
    assert "envoy_expand offset=900" in offset_text
