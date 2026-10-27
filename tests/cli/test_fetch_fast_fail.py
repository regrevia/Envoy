"""Connect-level failures must not be retried three times per exit."""

from __future__ import annotations

import httpx
import pytest

from envoy_cli import reader


class _StubClient:
    def __init__(self, error, calls):
        self._error = error
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stream(self, *_args, **_kwargs):
        self._calls.append(1)
        raise self._error


def _patch(monkeypatch, error, calls, exits):
    monkeypatch.setattr(reader, "proxy_plan", lambda: list(exits))
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda *_args, **_kwargs: _StubClient(error, calls),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)


def test_connect_timeout_is_not_retried_within_one_exit(monkeypatch):
    calls: list[int] = []
    _patch(monkeypatch, httpx.ConnectTimeout("timed out"), calls, [None])

    with pytest.raises(reader.PublicPageError) as raised:
        reader._fetch_html("https://example.com/article", dns_cache={})

    assert raised.value.code is reader.ErrorCode.NETWORK_ERROR
    assert len(calls) == 1, calls


def test_read_timeout_still_gets_the_three_attempts(monkeypatch):
    calls: list[int] = []
    _patch(monkeypatch, httpx.ReadTimeout("slow body"), calls, [None])

    with pytest.raises(reader.PublicPageError):
        reader._fetch_html("https://example.com/article", dns_cache={})

    assert len(calls) == reader.MAX_FETCH_ATTEMPTS, calls


def test_each_planned_exit_is_still_tried_once(monkeypatch):
    calls: list[int] = []
    _patch(monkeypatch, httpx.ConnectError("refused"), calls, [None, "http://127.0.0.1:7897"])

    with pytest.raises(reader.PublicPageError):
        reader._fetch_html("https://example.com/article", dns_cache={})

    assert len(calls) == 2, calls
