"""The outbound proxy plan: direct first, a discovered proxy only as fallback."""

from __future__ import annotations

import httpx
import pytest

from envoy_cli import netpolicy
from envoy_cli import reader


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(netpolicy.ENV_VAR, raising=False)


def _advertise(monkeypatch, mapping):
    monkeypatch.setattr(netpolicy, "getproxies", lambda: dict(mapping))


def test_direct_first_then_the_discovered_proxy(monkeypatch):
    _advertise(monkeypatch, {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})

    assert netpolicy.detected_proxy() == "http://127.0.0.1:7897"
    assert netpolicy.proxy_plan() == [None, "http://127.0.0.1:7897"]
    assert netpolicy.proxy_configured() is True


def test_socks_only_system_wide_proxy_still_falls_back(monkeypatch):
    _advertise(monkeypatch, {"socks": "127.0.0.1:1080"})

    assert netpolicy.detected_proxy() == "socks5://127.0.0.1:1080"
    assert netpolicy.proxy_plan() == [None, "socks5://127.0.0.1:1080"]


def test_no_proxy_means_a_single_direct_attempt(monkeypatch):
    _advertise(monkeypatch, {})

    assert netpolicy.proxy_plan() == [None]
    assert netpolicy.proxy_configured() is False


def test_pinned_proxy_has_no_direct_attempt(monkeypatch):
    _advertise(monkeypatch, {"https": "http://127.0.0.1:7897"})
    monkeypatch.setenv(netpolicy.ENV_VAR, "socks5://10.0.0.2:1080")

    assert netpolicy.proxy_plan() == ["socks5://10.0.0.2:1080"]


@pytest.mark.parametrize("value", sorted(netpolicy.DIRECT_VALUES))
def test_operator_can_forbid_the_fallback(monkeypatch, value):
    _advertise(monkeypatch, {"https": "http://127.0.0.1:7897"})
    monkeypatch.setenv(netpolicy.ENV_VAR, value)

    assert netpolicy.proxy_plan() == [None]
    assert netpolicy.proxy_configured() is False


def test_a_proxy_url_with_a_path_is_ignored(monkeypatch):
    _advertise(monkeypatch, {"https": "http://127.0.0.1:7897/pac"})

    assert netpolicy.detected_proxy() is None
    assert netpolicy.proxy_plan() == [None]


class _SecondHopClient:
    """Fails on the direct attempt, serves the proxy attempt unless told not to."""

    created: list[str | None] = []

    def __init__(self, proxy, *, body=b"PNG", content_type="image/png", fail_all=False):
        self._proxy = proxy
        self._body = body
        self._content_type = content_type
        self._fail_all = fail_all
        type(self).created.append(proxy)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def stream(self, *_args, **_kwargs):
        if self._fail_all or self._proxy is None:
            raise httpx.ConnectTimeout("connection timed out")
        return self

    @property
    def headers(self):
        return {"content-type": self._content_type, "content-length": str(len(self._body))}

    @property
    def status_code(self):
        return 200

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield self._body


def test_image_source_falls_back_to_the_proxy_after_a_direct_timeout(monkeypatch):
    _SecondHopClient.created = []
    monkeypatch.setattr(reader, "proxy_plan", lambda: [None, "http://127.0.0.1:7897"])
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda *_a, proxy=None, **_k: _SecondHopClient(proxy),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)

    result = reader._fetch_source("https://cdn.example.com/a.png", 1024, {})

    assert result["kind"] == "image"
    assert _SecondHopClient.created == [None, "http://127.0.0.1:7897"]


def test_image_source_reports_network_error_when_every_exit_fails(monkeypatch):
    _SecondHopClient.created = []
    monkeypatch.setattr(reader, "proxy_plan", lambda: [None, "http://127.0.0.1:7897"])
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda *_a, proxy=None, **_k: _SecondHopClient(proxy, fail_all=True),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)

    with pytest.raises(reader.PublicPageError) as raised:
        reader._fetch_source("https://cdn.example.com/a.png", 1024, {})

    assert raised.value.code is reader.ErrorCode.NETWORK_ERROR
    assert _SecondHopClient.created == [None, "http://127.0.0.1:7897"]
