"""A video page with no transcript still carries its own text."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import reader
from envoy_core.schemas.read_v1 import (
    ExecutionState,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadRequest,
    ReadResponse,
    ReadResult,
    TextContent,
)


def _request(**scope) -> ReadRequest:
    return ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "t-video-fallback",
        "source": {"kind": "auto", "url": "https://www.bilibili.com/video/BV1xx411c7mD"},
        "goal": "read the video page",
        "budget": {"max_response_chars": 8000},
        "retention": {"derived_ttl_seconds": 600},
        "scope": scope or {},
    })


def _response(request: ReadRequest, result: ReadResult, text: str) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=result,
        blocks=[MaterialBlock(
            id="blk-1",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=text),
            origin=MaterialOrigin.SOURCE_TEXT,
        )],
        errors=[],
    )


def test_page_text_is_returned_when_the_media_route_has_nothing(monkeypatch):
    request = _request()
    body = "简介" * 200
    captured: list[ReadRequest] = []

    def fake_read(inner: ReadRequest) -> ReadResponse:
        captured.append(inner)
        return _response(inner, ReadResult.READY, body)

    monkeypatch.setattr(reader, "_read_public_page_once", fake_read)
    result = reader._read_video_page_text(request, request.source.url)

    assert result is not None
    assert result.result == ReadResult.READY
    assert [error.code.value for error in result.errors] == ["missing_capability"]
    assert "page itself" in result.errors[0].message
    assert captured and captured[0].request_id == request.request_id
    # the media route is skipped only while the fallback runs
    assert reader._SKIP_MEDIA_FIRST.get() is False


def test_thin_page_text_keeps_the_block(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        reader,
        "_read_public_page_once",
        lambda inner: _response(inner, ReadResult.READY, "短"),
    )
    assert reader._read_video_page_text(request, request.source.url) is None


def test_blocked_fallback_keeps_the_block(monkeypatch):
    request = _request()
    monkeypatch.setattr(
        reader,
        "_read_public_page_once",
        lambda inner: _response(inner, ReadResult.BLOCKED, "长" * 400),
    )
    assert reader._read_video_page_text(request, request.source.url) is None


def test_visual_requests_keep_the_frame_route():
    request = _request(include_visual=True)
    assert reader._read_video_page_text(request, request.source.url) is None
