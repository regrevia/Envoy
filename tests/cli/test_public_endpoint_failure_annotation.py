"""When a matched public endpoint gives nothing, the read error says so.

The seam answers ``None`` for both "no endpoint claims this URL" and "the
endpoint was asked and had nothing". The page ladder needs the first to keep
walking, so the reason travels beside the return value and is put on the error
the read already returns — never on a new error, and never as a second code.
"""

from __future__ import annotations

import httpx
import pytest

from envoy_cli import fallbacks, reader
from envoy_core.schemas.read_v1 import ErrorCode, ReadRequest, ReadResult


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    monkeypatch.setattr(fallbacks, "_proxy_url", lambda: None)


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate(
        {"request_id": "public-endpoint-failure", "source": {"url": url}}
    )


def _ask_the_seam(url: str, handler, monkeypatch) -> None:
    monkeypatch.setattr(
        fallbacks,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert fallbacks.public_api_fallback(url) is None


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def test_a_failed_endpoint_annotates_the_error_the_read_already_returns(monkeypatch):
    url = "https://sspai.com/post/117529"
    _ask_the_seam(url, _timeout, monkeypatch)
    request = _request(url)
    blocked = reader._blocked(request, ErrorCode.NETWORK_ERROR, "the page did not answer")

    annotated = reader._annotate_public_endpoint_failure(request, blocked)

    assert annotated.result == blocked.result
    assert len(annotated.errors) == 1
    error = annotated.errors[0]
    # The code and message the read would have returned are untouched.
    assert error.code is ErrorCode.NETWORK_ERROR
    assert error.message == "the page did not answer"
    report = error.details["public_endpoint_failure"]
    assert report["backend"] == "sspai"
    assert report["code"] == ErrorCode.NETWORK_ERROR.value
    assert report["next_step"]


def test_no_endpoint_claimed_the_url_so_the_error_is_unchanged(monkeypatch):
    request = _request("https://example.com/plain")
    blocked = reader._blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "no answer")

    annotated = reader._annotate_public_endpoint_failure(request, blocked)

    assert annotated is blocked
    assert "public_endpoint_failure" not in (annotated.errors[0].details or {})


def test_a_backend_that_answered_nothing_annotates_empty_content(monkeypatch):
    url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    _ask_the_seam(
        url, lambda request: httpx.Response(200, json={"code": -1, "data": None}), monkeypatch
    )
    request = _request(url)
    blocked = reader._blocked(request, ErrorCode.EMPTY_CONTENT, "nothing usable on the page")

    annotated = reader._annotate_public_endpoint_failure(request, blocked)

    report = annotated.errors[0].details["public_endpoint_failure"]
    assert report["code"] == ErrorCode.EMPTY_CONTENT.value
    assert report["final"] is True
    # The read keeps its own code; the endpoint's reason lives in details.
    assert annotated.errors[0].code is ErrorCode.EMPTY_CONTENT


def test_a_ready_read_is_never_annotated(monkeypatch):
    url = "https://sspai.com/post/117529"
    _ask_the_seam(url, _timeout, monkeypatch)
    request = _request(url)
    ready = reader._blocked(request, ErrorCode.NETWORK_ERROR, "stale").model_copy(
        update={"result": ReadResult.READY, "errors": []}
    )

    assert reader._annotate_public_endpoint_failure(request, ready) is ready
