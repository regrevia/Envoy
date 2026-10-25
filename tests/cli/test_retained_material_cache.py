import json
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def _isolate_retained_reads():
    from envoy_cli import mcp

    mcp._RETAINED_READS.clear()
    yield
    mcp._RETAINED_READS.clear()


def _payload(
    material_id="mat-1",
    result="ready",
    frames=0,
    body="正文正文正文",
    request_id="stale-request-id",
):
    blocks = [{"kind": "text", "text": body}]
    modalities = {"text": {"extracted": 1, "returned": 1, "extraction": "complete"}}
    if frames:
        modalities["frames"] = {
            "discovered": frames,
            "processed": frames,
            "delivered": frames,
            "extraction": "partial",
        }
        blocks.append({"kind": "visual", "visual_inputs": frames})
    return {
        "protocol_version": "envoy.read/1",
        "request_id": request_id,
        "material_id": material_id,
        "source_revision": "rev-1",
        "execution_state": "completed",
        "result": result,
        "blocks": blocks,
        "coverage": {"modalities": modalities},
        "notices": [],
        "usage": {},
    }


class _FakeReads:
    """Stand-in for `read_public_page`, recording each real fetch."""

    def __init__(self):
        self.requests = []
        self.payloads = []

    def read(self, request):
        self.requests.append(request)
        return object()

    def payload(self, response, compact):
        if not self.payloads:
            raise AssertionError("no fake payload queued for this read")
        value = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return json.loads(json.dumps(value))


@pytest.fixture
def reads(monkeypatch):
    from envoy_cli import mcp

    fake = _FakeReads()
    monkeypatch.setattr(mcp, "read_public_page", fake.read)
    monkeypatch.setattr(mcp, "_read_payload", fake.payload)
    return fake


def _read(reads, capsys, url, request_id="req-1", **overrides):
    from envoy_cli import mcp

    arguments = {"request_id": request_id, "source": {"url": url}, "goal": "读正文"}
    arguments.update(overrides)
    mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "envoy_read", "arguments": arguments},
        },
    ])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result = lines[-1]["result"]
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def test_query_parameters_keep_distinct_media_apart(reads, capsys):
    reads.payloads = [_payload("mat-a"), _payload("mat-b")]

    first = _read(reads, capsys, "https://video.example.com/watch?v=aaaa", request_id="req-a")
    second = _read(reads, capsys, "https://video.example.com/watch?v=bbbb", request_id="req-b")

    assert len(reads.requests) == 2, "different video ids must not share retained material"
    assert "served_from_retained_material" not in first
    assert "served_from_retained_material" not in second
    assert first["material_id"] == "mat-a"
    assert second["material_id"] == "mat-b"


def test_landing_page_does_not_borrow_the_watch_page_material(reads, capsys):
    reads.payloads = [_payload("mat-watch"), _payload("mat-landing")]

    _read(reads, capsys, "https://video.example.com/watch?v=aaaa", request_id="req-watch")
    landing = _read(reads, capsys, "https://video.example.com/", request_id="req-landing")

    assert len(reads.requests) == 2
    assert landing["material_id"] == "mat-landing"


def test_tracking_parameters_reuse_the_same_material(reads, capsys):
    reads.payloads = [_payload("mat-1")]

    _read(reads, capsys, "https://weixin.example.com/s/abc?utm_source=feed&utm_medium=share", request_id="req-1")
    second = _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2")

    assert len(reads.requests) == 1, "tracking parameters must not mint new material"
    assert second["served_from_retained_material"] is True
    assert second["material_id"] == "mat-1"


def test_retained_response_carries_the_current_request_id(reads, capsys):
    reads.payloads = [_payload("mat-1")]

    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-1")
    second = _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2")

    assert second["request_id"] == "req-2"


def test_different_time_range_rereads(reads, capsys):
    reads.payloads = [_payload("mat-1")]

    _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-1",
          scope={"start_seconds": 0, "end_seconds": 30})
    _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-2",
          scope={"start_seconds": 30, "end_seconds": 60})

    assert len(reads.requests) == 2, "a different time window is different material work"


def test_visual_request_after_text_read_rereads(reads, capsys):
    reads.payloads = [_payload("mat-1")]

    _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-1")
    _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-2",
          scope={"include_visual": True},
          host_capabilities={"video_input": "frames", "image_input": "local_file"},
          retention={"raw_media": "persistent"})

    assert len(reads.requests) == 2, "a request for frames cannot be served by a text-only read"


def test_tighter_budget_rereads(reads, capsys):
    reads.payloads = [_payload("mat-1", body="正文" * 2000)]

    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-1")
    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2",
          budget={"max_response_chars": 256, "max_total_response_chars": 256})

    assert len(reads.requests) == 2, "retained output must fit the current response budget"


def test_visual_read_keeps_frames_available_for_a_second_visual_read(reads, capsys):
    reads.payloads = [_payload("mat-1", frames=3)]

    _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-1",
          scope={"include_visual": True},
          host_capabilities={"video_input": "frames", "image_input": "local_file"})
    second = _read(reads, capsys, "https://video.example.com/watch?v=a", request_id="req-2",
                   scope={"include_visual": True},
                   host_capabilities={"video_input": "frames", "image_input": "local_file"})

    assert len(reads.requests) == 1, "a narrower later read must not evict the retained frames"
    assert second["served_from_retained_material"] is True


def test_refresh_falls_back_to_retained_material_when_the_source_is_blocked(reads, capsys):
    reads.payloads = [_payload("mat-1")]
    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-1")

    reads.payloads = [_payload("mat-blocked", result="blocked")]
    fallback = _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2", refresh=True)

    assert len(reads.requests) == 2, "refresh must really re-read the source first"
    assert fallback["result"] == "ready"
    assert fallback["served_from_retained_material"] is True
    assert fallback["material_id"] == "mat-1"
    notice = [item for item in fallback["notices"] if item["code"] == "served_from_retained_material"]
    assert notice and "refetch_error" in notice[-1]


def test_blocked_refetch_falls_back_to_retained_material(reads, capsys):
    reads.payloads = [_payload("mat-1")]
    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-1")

    # Only an explicit refresh reaches the blocked-refetch fallback; an equal
    # request is already served from the retained material above.
    reads.payloads = [_payload("mat-blocked", result="blocked")]
    fallback = _read(
        reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2", refresh=True
    )

    assert fallback["served_from_retained_material"] is True
    assert fallback["material_id"] == "mat-1"
    from envoy_cli import mcp

    # The fallback must not be recorded under the new request: a later upgrade
    # to visual still has to read the source instead of inheriting this payload.
    assert mcp._RETAINED_READS["https://weixin.example.com/s/abc"]["needs"]["include_visual"] is False
    upgraded = _read(
        reads,
        capsys,
        "https://weixin.example.com/s/abc",
        request_id="req-3",
        scope={"include_visual": True},
    )
    assert "served_from_retained_material" not in upgraded
    assert len(reads.requests) == 3


def test_retained_material_is_not_reused_after_a_partial_read_evicts_it(reads, capsys):
    reads.payloads = [_payload("mat-partial", result="partial")]
    _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-1",
          scope={"start_seconds": 0, "end_seconds": 30})
    second = _read(reads, capsys, "https://weixin.example.com/s/abc", request_id="req-2",
                   scope={"start_seconds": 30, "end_seconds": 60})

    assert len(reads.requests) == 2
    assert "served_from_retained_material" not in second


def test_fragments_are_part_of_the_retained_identity(reads, capsys):
    reads.payloads = [_payload("mat-page-5"), _payload("mat-page-9")]

    _read(reads, capsys, "https://docs.example.com/report.pdf#page=5", request_id="req-1")
    second = _read(reads, capsys, "https://docs.example.com/report.pdf#page=9", request_id="req-2")

    assert len(reads.requests) == 2, "a fragment addresses a different part of the document"
    assert second["material_id"] == "mat-page-9"


def test_an_ocr_language_mismatch_in_either_direction_reads_again(reads, capsys):
    reads.payloads = [_payload("mat-en"), _payload("mat-zh"), _payload("mat-plain")]

    _read(reads, capsys, "https://example.com/a", request_id="req-1", ocr_language="en")
    # A request with no language is still a different OCR setting, and so is zh.
    _read(reads, capsys, "https://example.com/a", request_id="req-2")
    _read(reads, capsys, "https://example.com/a", request_id="req-3", ocr_language="zh")

    assert len(reads.requests) == 3


def test_visual_observations_count_as_delivered_visual_evidence():
    from envoy_cli import mcp

    assert mcp._payload_has_visual(
        {"coverage": {"modalities": {"visual_observation": {"extracted": 1}}}}
    )
    assert mcp._payload_has_visual({"blocks": [{"kind": "visual_observation"}]})
    assert mcp._payload_has_visual({"blocks": [{"kind": "frame"}]})
    assert not mcp._payload_has_visual({"blocks": [{"kind": "paragraph"}]})


def test_a_string_refresh_value_is_not_a_refresh(reads, capsys):
    reads.payloads = [_payload("mat-1")]

    _read(reads, capsys, "https://example.com/a", request_id="req-1")
    second = _read(reads, capsys, "https://example.com/a", request_id="req-2", refresh="false")

    assert len(reads.requests) == 1
    assert second["served_from_retained_material"] is True


def test_a_failed_refetch_reports_the_current_request_id(reads, capsys):
    from envoy_cli.reader import PublicPageError
    from envoy_core.schemas.read_v1 import ErrorCode

    reads.payloads = [_payload("mat-1")]
    _read(reads, capsys, "https://example.com/a", request_id="req-1")

    def fail(request):
        raise PublicPageError("the source went away", ErrorCode.SOURCE_UNAVAILABLE)

    reads.read = fail  # type: ignore[method-assign]
    from envoy_cli import mcp

    mcp.read_public_page = fail
    fallback = _read(reads, capsys, "https://example.com/a", request_id="req-9", refresh=True)

    assert fallback["served_from_retained_material"] is True
    assert fallback["request_id"] == "req-9"
    assert fallback["notices"][-1]["refetch_error"]["code"] == "source_unavailable"


def test_a_human_action_is_never_rendered_as_a_runnable_command():
    from envoy_cli import mcp

    text = mcp._action_text({
        "kind": "login_required",
        "requires_human": True,
        "question": "This page needs a signed-in session.",
        "options": [
            {
                "kind": "human_sign_in",
                "instruction": "Sign in once in a terminal; later reads reuse the saved profile.",
                "effect": "The session is kept for headless reads",
            }
        ],
    })

    assert text.startswith("Ask the user to do this")
    assert "Sign in once in a terminal" in text
    assert text.index("Ask the user to do this") < text.index("Sign in once")
