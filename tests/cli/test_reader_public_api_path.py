"""Ordering tests for the public-endpoint shortcuts in the page reader.

A site that publishes its own JSON endpoint must be answered before Envoy boots
a browser, and a thin endpoint answer must not replace a richer rendered page.
"""

import pytest

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate(
        {"request_id": "public-api-path", "source": {"url": url}}
    )


def _allow_url(monkeypatch) -> None:
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)


def test_feed_is_answered_before_any_page_or_browser_work(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(
        reader,
        "fetch_feed",
        lambda url, **_kwargs: {
            "backend": "feed",
            "title": "Feed title",
            "text": "item " * 60,
            "url": url,
            "items": [],
        },
    )
    monkeypatch.setattr(
        reader,
        "public_api_fallback",
        lambda *_args, **_kwargs: pytest.fail("a feed must not fall through to site endpoints"),
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: pytest.fail("a feed must not fetch the rendered page"),
    )
    monkeypatch.setattr(
        reader,
        "_read_with_isolated_browser",
        lambda *_args, **_kwargs: pytest.fail("a feed must not boot a browser"),
    )

    response = reader._read_public_page_once(_request("https://example.com/feed.xml"))

    assert response.result.value == "ready"
    details = response.coverage.modalities["text"].details
    assert details["reader"] == "public_api:feed"
    assert details["backend"] == "feed"


def test_site_endpoint_answer_skips_the_rendered_page(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        reader,
        "public_api_fallback",
        lambda url, **_kwargs: {
            "backend": "stackexchange",
            "title": "How do JavaScript closures work?",
            "text": "closure text " * 40,
            "url": url,
        },
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: pytest.fail("the endpoint answer must not fetch the page"),
    )
    monkeypatch.setattr(
        reader,
        "_read_with_isolated_browser",
        lambda *_args, **_kwargs: pytest.fail("the endpoint answer must not boot a browser"),
    )

    response = reader._read_public_page_once(
        _request("https://stackoverflow.com/questions/111102")
    )

    assert response.result.value == "ready"
    assert response.coverage.modalities["text"].details["reader"] == (
        "public_api:stackexchange"
    )


def test_thin_generic_answer_keeps_the_page_path(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        reader,
        "public_api_fallback",
        lambda *_args, **_kwargs: {
            "backend": "hackernews",
            "title": "t",
            "text": "too short",
            "url": "https://news.ycombinator.com/item?id=1",
        },
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("page path reached")),
    )

    with pytest.raises(RuntimeError, match="page path reached"):
        reader._read_public_page_once(_request("https://news.ycombinator.com/item?id=1"))


def test_a_backend_may_declare_a_lower_floor(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        reader,
        "public_api_fallback",
        lambda url, **_kwargs: {
            "backend": "sspai",
            "title": "派评 | 近期值得关注的 App",
            "text": "短短一段原文正文 " * 10,
            "url": url,
            "min_chars": 80,
        },
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: pytest.fail("a declared floor must keep the endpoint answer"),
    )

    response = reader._read_public_page_once(_request("https://sspai.com/post/117529"))

    assert response.coverage.modalities["text"].details["reader"] == "public_api:sspai"


def test_floor_defaults_to_the_generic_minimum(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("page path reached")),
    )

    for declared in (None, 0, "not-a-number"):
        fallback = {
            "backend": "npm",
            "title": "left-pad",
            "text": "x" * (reader._PUBLIC_API_FALLBACK_MIN_CHARS - 1),
            "url": "https://www.npmjs.com/package/left-pad",
        }
        if declared is not None:
            fallback["min_chars"] = declared
        monkeypatch.setattr(reader, "public_api_fallback", lambda *_a, _fallback=fallback, **_k: _fallback)
        with pytest.raises(RuntimeError, match="page path reached"):
            reader._read_public_page_once(
                _request("https://www.npmjs.com/package/left-pad")
            )


def test_volcengine_security_check_counts_as_an_access_challenge():
    title = "36氪_让一部分人先看到未来"
    shell = (
        "火山引擎 正在进行安全检测... 为保障您的访问安全，"
        "系统正在检测当前网络环境，该过程通常需要几秒钟，请耐心等待"
    )
    assert reader._looks_like_access_challenge(title, shell)

    article = (
        "这家公司把安全检测做成订阅服务，客户覆盖金融与制造业。"
        "文章讨论了巡检成本，并给出三条落地建议。"
    )
    assert not reader._looks_like_access_challenge("网络安全检测公司融资", article)


def test_a_full_article_that_mentions_a_challenge_is_not_one():
    # Real content may discuss captchas or verification; only a stub page with
    # the marker is a wall, otherwise a GitHub release note about Envoy's own
    # challenge handling is refused as a login wall.
    title = "Release v0.1.26 · regrevia/Envoy · GitHub"
    article = (
        "此前该契约不可见，模型会去侧边栏开页面、撞上验证码，再拿封面帧当证据。"
        "安全验证字样仍先让浏览器解一次人机校验，避免误杀能过的站。"
    ) * 20
    assert len(article) > reader.CHALLENGE_MARKER_MAX_CHARS
    assert not reader._looks_like_access_challenge(title, article)
    assert reader._looks_like_access_challenge("Just a moment...", article)


THIN_BLOG_HTML = (
    "<html><head><title>A blog</title>"
    '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    "</head><body><nav>menu</nav></body></html>"
)


def test_a_thin_page_follows_its_declared_feed(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda url, **_kwargs: None)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (
        "https://blog.example.com/", THIN_BLOG_HTML,
    ))
    calls = []

    def fake_feed(url, **_kwargs):
        calls.append(url)
        return {"backend": "feed", "title": "Feed", "text": "item " * 80, "url": url}

    monkeypatch.setattr(reader, "public_api_fallback", lambda *_a, **_k: None)

    # The URL itself is not feed-shaped, so the first probe is the site endpoint.
    monkeypatch.setattr(reader, "fetch_feed", fake_feed)

    response = reader.read_public_page(_request("https://blog.example.com/"))

    assert calls == ["https://blog.example.com/feed.xml"]
    assert response.coverage.modalities["text"].details["reader"] == (
        "public_api:feed_alternate"
    )


def test_a_full_page_does_not_hijack_its_declared_feed(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *_a, **_k: None)
    body = "real article sentence. " * 40
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (
        "https://blog.example.com/post",
        "<html><head><title>Post</title>"
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        f"</head><body><p>{body}</p></body></html>",
    ))
    monkeypatch.setattr(
        reader,
        "fetch_feed",
        lambda *_args, **_kwargs: pytest.fail("a full page must keep its own text"),
    )

    response = reader.read_public_page(_request("https://blog.example.com/post"))

    assert response.result.value == "ready"
    assert "real article sentence" in "".join(
        block.content.text for block in response.blocks if getattr(block.content, "text", None)
    )


def _short_hackernews_answer(url: str) -> dict:
    return {
        "backend": "hackernews",
        "title": "Y Combinator",
        "text": "Y Combinator\nhttps://news.ycombinator.com/item?id=1\nscore: 57",
        "url": url,
    }


def test_a_short_endpoint_answer_rescues_an_unreachable_page(monkeypatch):
    _allow_url(monkeypatch)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        reader, "public_api_fallback", lambda url, **_kwargs: _short_hackernews_answer(url)
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            reader.PublicPageError("unreachable", reader.ErrorCode.NETWORK_ERROR)
        ),
    )

    response = reader.read_public_page(_request("https://news.ycombinator.com/item?id=1"))

    assert response.result != reader.ReadResult.BLOCKED
    text = "".join(block.content.text or "" for block in response.blocks)
    assert "Y Combinator" in text
    details = response.coverage.modalities["text"].details
    assert details["page_unreachable"] is True
    assert details["page_unreachable_cause"] == "network_error"


def test_a_short_endpoint_answer_does_not_rescue_a_challenge(monkeypatch):
    _allow_url(monkeypatch)

    def blocked_with_a_short_answer(_request):
        reader._SHORT_PUBLIC_FALLBACK.set(
            _short_hackernews_answer("https://news.ycombinator.com/item?id=1")
        )
        return reader._blocked(
            _request,
            reader.ErrorCode.CHALLENGE_REQUIRED,
            "The page wants a verification step",
            action_required=True,
        )

    monkeypatch.setattr(reader, "_read_public_page_once", blocked_with_a_short_answer)

    response = reader.read_public_page(_request("https://news.ycombinator.com/item?id=1"))

    assert response.result == reader.ReadResult.BLOCKED
    assert [error.code for error in response.errors] == [reader.ErrorCode.CHALLENGE_REQUIRED]


def test_the_short_answer_does_not_leak_into_the_next_read(monkeypatch):
    _allow_url(monkeypatch)
    calls = []

    def once(_request):
        calls.append(1)
        if len(calls) == 1:
            reader._SHORT_PUBLIC_FALLBACK.set(
                _short_hackernews_answer("https://news.ycombinator.com/item?id=1")
            )
        return reader._blocked(
            _request,
            reader.ErrorCode.NETWORK_ERROR,
            "unreachable",
        )

    monkeypatch.setattr(reader, "_read_public_page_once", once)

    first = reader.read_public_page(_request("https://news.ycombinator.com/item?id=1"))
    second = reader.read_public_page(_request("https://example.com/article"))

    assert first.result != reader.ReadResult.BLOCKED
    assert second.result == reader.ReadResult.BLOCKED
