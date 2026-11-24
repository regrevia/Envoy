"""A matched-but-empty public endpoint is not the same as no endpoint at all.

The seam still answers ``dict | None`` so a read can keep walking; these pin the
reason it keeps for the task, and that the reason speaks the reader's own codes.
"""

from __future__ import annotations

import httpx
import pytest

from envoy_cli import diagnosis, fallbacks
from envoy_core.schemas.read_v1 import ErrorCode


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    monkeypatch.setattr(fallbacks, "_proxy_url", lambda: None)


def _serve(handler):
    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


def test_an_unclaimed_url_has_no_outcome_and_no_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no backend claims this URL")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))

    assert fallbacks.public_api_fallback_outcome("https://example.com/a") is None
    assert fallbacks.public_api_fallback("https://example.com/a") is None
    assert fallbacks.failure_for("https://example.com/a") is None


def test_a_transport_failure_is_recorded_as_retryable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))

    outcome = fallbacks.public_api_fallback_outcome(
        "https://stackoverflow.com/questions/111102"
    )

    assert isinstance(outcome, fallbacks.FallbackFailure)
    assert outcome.code is ErrorCode.NETWORK_ERROR
    assert outcome.retryable is True
    assert outcome.final is False
    assert outcome.backend == "stackexchange"


def test_an_endpoint_answer_without_the_body_is_final(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(404, json={"error": "gone"})),
    )

    outcome = fallbacks.public_api_fallback_outcome("https://registry.npmjs.org/left-pad")

    assert isinstance(outcome, fallbacks.FallbackFailure)
    assert outcome.code is ErrorCode.SOURCE_UNAVAILABLE
    assert outcome.final is True
    assert outcome.retryable is False


def test_an_endpoint_that_answers_nothing_usable_is_empty_content(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(200, json={"code": -404, "data": None})),
    )

    outcome = fallbacks.public_api_fallback_outcome(
        "https://www.bilibili.com/video/BV1GJ411x7h7"
    )

    assert isinstance(outcome, fallbacks.FallbackFailure)
    assert outcome.code is ErrorCode.EMPTY_CONTENT
    assert outcome.final is True


def test_the_seam_stays_none_while_the_reason_stays_readable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))

    url = "https://sspai.com/post/117529"
    assert fallbacks.public_api_fallback(url) is None

    failure = fallbacks.failure_for(url)
    assert failure is not None
    assert failure.code is ErrorCode.NETWORK_ERROR
    assert failure.url == url


def test_a_reason_is_not_reused_for_a_different_site(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(500, json={})),
    )

    assert fallbacks.public_api_fallback("https://sspai.com/post/117529") is None
    assert fallbacks.failure_for("https://vimeo.com/76979871") is None


def test_a_resolved_short_link_still_owns_its_reason(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                302, headers={"location": "https://www.bilibili.com/video/BV1GJ411x7h7"}
            )
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    monkeypatch.setattr(fallbacks, "_proxy_url", lambda: "http://proxy.invalid:8080")
    monkeypatch.setattr(
        fallbacks,
        "_proxy_client",
        lambda proxy: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert fallbacks.public_api_fallback("https://b23.tv/abc123") is None
    failure = fallbacks.failure_for("https://b23.tv/abc123")
    assert failure is not None and failure.backend == "bilibili"


def test_the_envelope_is_one_shape_whatever_the_failure(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(429, json={})),
    )
    outcome = fallbacks.public_api_fallback_outcome("https://vimeo.com/76979871")
    assert isinstance(outcome, fallbacks.FallbackFailure)

    report = outcome.as_dict()

    assert report["schema_version"] == fallbacks.SCHEMA
    assert report["code"] == ErrorCode.SOURCE_UNAVAILABLE.value
    assert report["backend"] == "vimeo"
    assert report["next_step"]
    assert report["final"] is True
    # The next step is the sentence the reader already gives for that code.
    assert report["next_step"] == diagnosis.diagnose(ErrorCode.SOURCE_UNAVAILABLE).next_step


def test_credentials_never_appear_in_the_envelope():
    # No route pattern tolerates credentials, so such a URL never reaches an
    # endpoint; the scrub is the second layer, for whatever a pattern is added
    # later. Both are pinned because an envelope is shown to the host.
    scrubbed = fallbacks._display_url(
        "https://user:secret@vimeo.com:443/76979871?token=abc"
    )
    assert "secret" not in scrubbed
    assert scrubbed.startswith("https://vimeo.com:443/")
    assert fallbacks.route_backend("https://user:secret@vimeo.com/76979871") is None
    assert fallbacks._display_url("https://vimeo.com/76979871") == (
        "https://vimeo.com/76979871"
    )


def test_route_backend_answers_without_the_network():
    assert fallbacks.route_backend("https://vimeo.com/76979871") == "vimeo"
    assert fallbacks.route_backend("https://b23.tv/abc123") == "bilibili"
    assert fallbacks.route_backend("https://example.com/x") is None
    assert fallbacks.route_backend("") is None
