"""The HTTP body cache must be invisible except when it answers correctly.

Every test drives the real ``_fetch_html_via`` with a fake transport: the cache
behaviour under test (hit, revalidate, refuse) is what a real server would see,
not a re-implementation of http_cache.
"""

from __future__ import annotations

import httpx
import pytest

from envoy_cli import http_cache, reader

URL = "https://example.com/article"


class _Response:
    def __init__(self, status_code=200, text="<html><body>hello</body></html>", headers=None):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {"content-type": "text/html; charset=utf-8"})
        self.encoding = "utf-8"
        self._text = text
        self.url = URL

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def iter_bytes(self):
        yield self._text.encode("utf-8")


class _Ctx:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *_exc):
        return False


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def stream(self, _method, url, headers=None):
        self.requests.append((url, dict(headers or {})))
        if not self._responses:
            raise AssertionError("unexpected network request")
        return _Ctx(self._responses.pop(0))


@pytest.fixture(autouse=True)
def _public_network(monkeypatch, tmp_path):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    monkeypatch.setattr(reader, "_credential_cookie_header", lambda _url: "")
    # A shared cache directory would let a previous run's entry answer the
    # first read here, hiding the request this test asserts on.
    monkeypatch.setenv(http_cache.ENV_CACHE_DIR, str(tmp_path / "http-cache"))


def _fetch(client, *, stats=None, max_bytes=reader.MAX_HTML_BYTES):
    return reader._fetch_html_via(client, URL, max_bytes, {}, stats=stats)


def test_second_read_is_served_without_a_request():
    first = _Client([_Response(headers={
        "content-type": "text/html",
        "etag": '"v1"',
    })])
    assert _fetch(first) == (URL, "<html><body>hello</body></html>")

    stats: dict[str, str] = {}
    second = _Client([])
    assert _fetch(second, stats=stats) == (URL, "<html><body>hello</body></html>")
    assert second.requests == []
    assert stats["fetch_cache"] == "hit"


def test_stale_entry_is_revalidated_with_its_validators(monkeypatch):
    _fetch(_Client([_Response(headers={"content-type": "text/html", "etag": '"v1"'})]))
    monkeypatch.setattr(http_cache, "_ttl_seconds", lambda: 0.0)

    client = _Client([_Response(status_code=304, headers={"etag": '"v1"'})])
    stats: dict[str, str] = {}
    assert _fetch(client, stats=stats) == (URL, "<html><body>hello</body></html>")
    assert client.requests[0][1]["If-None-Match"] == '"v1"'
    assert stats["fetch_cache"] == "revalidated"


def test_revalidated_entry_serves_the_original_final_url():
    redirect_client = _Client([
        _Response(status_code=301, headers={"location": "https://example.com/final", "content-type": "text/html"}),
        _Response(headers={"content-type": "text/html"}),
    ])
    assert _fetch(redirect_client)[0] == "https://example.com/final"

    stale_client = _Client([])
    assert _fetch(stale_client) == ("https://example.com/final", "<html><body>hello</body></html>")
    assert stale_client.requests == []


def test_no_store_response_is_not_cached():
    _fetch(_Client([_Response(headers={
        "content-type": "text/html",
        "cache-control": "no-store",
    })]))
    client = _Client([_Response(headers={"content-type": "text/html"})])
    stats: dict[str, str] = {}
    assert _fetch(client, stats=stats)[1].startswith("<html>")
    assert len(client.requests) == 1
    assert stats["fetch_cache"] == "miss"


def test_failed_response_never_becomes_cached_content():
    with pytest.raises(reader.PublicPageError):
        _fetch(_Client([_Response(status_code=404)]))

    client = _Client([_Response(headers={"content-type": "text/html"})])
    assert _fetch(client)[1].startswith("<html>")
    assert len(client.requests) == 1


def test_a_different_cookie_set_does_not_share_the_entry(monkeypatch):
    _fetch(_Client([_Response(headers={"content-type": "text/html", "etag": '"v1"'})]))

    monkeypatch.setattr(reader, "_credential_cookie_header", lambda _url: "session=other")
    stats: dict[str, str] = {}
    client = _Client([_Response(headers={"content-type": "text/html"})])
    assert _fetch(client, stats=stats)[1].startswith("<html>")
    assert len(client.requests) == 1
    assert stats["fetch_cache"] == "miss"


def test_a_redirected_entry_keeps_no_validators_to_replay(monkeypatch):
    """An ETag seen after a redirect describes the target, not the source URL."""

    _fetch(_Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html", "etag": '"target-only"'}),
    ]))
    monkeypatch.setattr(http_cache, "_ttl_seconds", lambda: 0.0)

    client = _Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html"}),
    ])
    _fetch(client)
    assert "If-None-Match" not in client.requests[0][1]
    assert "If-None-Match" not in client.requests[1][1]


def test_cache_policy_of_a_redirect_target_is_honoured():
    """A refusal is a property of the response, not of the hop that served it."""

    _fetch(_Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={
            "content-type": "text/html",
            "cache-control": "private, no-store",
            "vary": "*",
        }),
    ]))

    stats: dict[str, str] = {}
    client = _Client([_Response(headers={"content-type": "text/html"})])
    assert _fetch(client, stats=stats)[1].startswith("<html>")
    assert len(client.requests) == 1
    assert stats["fetch_cache"] == "miss"


def test_a_redirect_target_credential_never_shares_the_source_body(monkeypatch):
    monkeypatch.setattr(
        reader,
        "_credential_cookie_header",
        lambda url: "target=one" if "/next" in url else "",
    )

    _fetch(_Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html"}),
    ]))

    monkeypatch.setattr(
        reader,
        "_credential_cookie_header",
        lambda url: "target=two" if "/next" in url else "",
    )
    stats: dict[str, str] = {}
    client = _Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html"}),
    ])
    _fetch(client, stats=stats)
    assert len(client.requests) == 2
    assert stats["fetch_cache"] == "miss"


def test_a_hit_is_refused_when_the_final_url_stops_being_public(monkeypatch):
    _fetch(_Client([_Response(headers={"content-type": "text/html", "etag": '"v1"'})]))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: False)

    client = _Client([])
    with pytest.raises(reader.PublicPageError):
        _fetch(client)
    assert client.requests == []


def test_disabled_cache_reports_bypass_and_never_stores(monkeypatch):
    monkeypatch.setenv("ENVOY_HTTP_CACHE", "0")
    stats: dict[str, str] = {}
    _fetch(_Client([_Response(headers={"content-type": "text/html"})]), stats=stats)
    assert stats["fetch_cache"] == "bypass"

    client = _Client([_Response(headers={"content-type": "text/html"})])
    _fetch(client)
    assert len(client.requests) == 1


def test_concurrent_readers_share_a_single_request():
    import threading

    entered = threading.Event()
    release = threading.Event()

    class _SlowClient(_Client):
        def stream(self, method, url, headers=None):
            entered.set()
            assert release.wait(10), "the first fetch never finished"
            return super().stream(method, url, headers=headers)

    client = _SlowClient([_Response(headers={"content-type": "text/html"})])
    results: list[tuple[str, str] | None] = [None, None]
    errors: list[BaseException] = []
    stats_by_reader: list[dict[str, str]] = [{}, {}]

    def run(index):
        try:
            results[index] = reader._fetch_html_via(
                client, URL, reader.MAX_HTML_BYTES, {}, stats=stats_by_reader[index]
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    first = threading.Thread(target=run, args=(0,))
    first.start()
    assert entered.wait(10), "the first reader never issued its request"
    second = threading.Thread(target=run, args=(1,))
    second.start()
    # The second reader must block on the cache lock rather than issue a GET.
    assert second.is_alive()
    release.set()
    first.join(10)
    second.join(10)

    assert errors == []
    assert results == [(URL, "<html><body>hello</body></html>")] * 2
    assert len(client.requests) == 1
    assert sorted(stats["fetch_cache"] for stats in stats_by_reader) == ["hit", "miss"]


def test_a_hit_is_refused_when_the_redirect_target_rotates_its_cookie(monkeypatch):
    """A stored body came from the target hop; that hop's cookie can change."""

    def cookie_for(url):
        return "" if url == URL else "target=one"

    monkeypatch.setattr(reader, "_credential_cookie_header", cookie_for)
    _fetch(_Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html"}),
    ]))

    monkeypatch.setattr(
        reader, "_credential_cookie_header", lambda url: "" if url == URL else "target=two"
    )
    client = _Client([
        _Response(
            status_code=301,
            headers={"location": "https://example.com/next", "content-type": "text/html"},
        ),
        _Response(headers={"content-type": "text/html"}),
    ])
    stats = {}
    _fetch(client, stats=stats)
    assert len(client.requests) == 2
    assert stats["fetch_cache"] == "miss"
