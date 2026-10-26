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
