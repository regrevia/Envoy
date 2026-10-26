"""Public-API fallbacks: fixed URLs, plain text, never raising."""

from __future__ import annotations

import httpx

from envoy_cli import fallbacks


def _serve(handler):
    """Point the module at a MockTransport for the duration of one test."""

    def factory() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


def _json(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_stackoverflow_question_and_top_answers_are_plain_text(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/answers"):
            return _json(
                {
                    "items": [
                        {"score": 90, "is_accepted": True, "body": "<p>Use <code>asyncio</code>.</p>"},
                        {"score": 40, "body": "<p>Second best.</p>"},
                        {"score": 10, "body": "<p>Third.</p>"},
                        {"score": 1, "body": "<p>Fourth is outside the cap.</p>"},
                    ]
                }
            )
        return _json(
            {
                "items": [
                    {
                        "title": "How do I &quot;await&quot; a coroutine?",
                        "body": "<p>Question body.</p><script>alert(1)</script>",
                    }
                ]
            }
        )

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback(
        "https://stackoverflow.com/questions/12345/how-to-await?answertab=votes"
    )

    assert result is not None
    assert result["backend"] == "stackexchange"
    assert result["url"] == "https://stackoverflow.com/questions/12345"
    assert "How do I \"await\" a coroutine?" in result["title"]
    text = result["text"]
    assert "Question body." in text
    assert "Use asyncio." in text
    assert "Second best." in text
    assert "Third." in text
    assert "Fourth is outside the cap." not in text
    assert "alert(1)" not in text
    assert "<p>" not in text
    assert "score 90" in text and "accepted" in text
    assert any("/questions/12345/answers" in url and "filter=withbody" in url for url in seen)


def test_stackoverflow_form_that_is_not_a_question_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://stackoverflow.com/questions") is None
    assert fallbacks.public_api_fallback("https://stackoverflow.com/users/12345") is None
    assert fallbacks.public_api_fallback("https://example.com/questions/12345") is None


def test_stackoverflow_network_error_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://stackoverflow.com/questions/12345") is None


def test_npm_package_summary_and_readme(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/left-pad"
        return _json(
            {
                "name": "left-pad",
                "description": "String left pad",
                "readme": "# left-pad\n\n```js\nleftPad('x', 2)\n```",
                "dist-tags": {"latest": "1.3.0"},
                "time": {"modified": "2024-01-01T00:00:00.000Z"},
                "versions": {f"1.{index}.0": {} for index in range(12)},
            }
        )

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback("https://www.npmjs.com/package/left-pad")

    assert result is not None
    assert result["backend"] == "npm"
    assert result["url"] == "https://www.npmjs.com/package/left-pad"
    assert result["title"].startswith("left-pad")
    assert "String left pad" in result["text"]
    assert "latest: 1.3.0" in result["text"]
    assert "leftPad('x', 2)" in result["text"]
    versions_line = next(line for line in result["text"].splitlines() if line.startswith("versions:"))
    assert versions_line.count(",") == 9


def test_npm_form_that_is_not_a_package_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://www.npmjs.com/search?q=left-pad") is None
    assert fallbacks.public_api_fallback("https://registry.npmjs.org/") is None


def test_npm_network_error_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://registry.npmjs.org/left-pad") is None


def test_npm_error_status_is_none(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(404, json={"error": "Not found"})),
    )
    assert fallbacks.public_api_fallback("https://registry.npmjs.org/not-a-real-package-xyz") is None


def test_hacker_news_story_and_comments(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        item_id = request.url.path.rsplit("/", 1)[-1].split(".")[0]
        if item_id == "100":
            return _json(
                {
                    "id": 100,
                    "type": "story",
                    "title": "Show HN: Plain text",
                    "text": "<p>Ask body.</p>",
                    "score": 321,
                    "by": "pg",
                    "kids": [101, 102, 103, 104, 105, 106, 107],
                }
            )
        if item_id in {"101", "102", "103", "104", "105", "106"}:
            return _json({"id": int(item_id), "by": f"user{item_id}", "text": f"<p>comment {item_id}</p>"})
        return _json({"id": int(item_id), "deleted": True})

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback("https://news.ycombinator.com/item?id=100")

    assert result is not None
    assert result["backend"] == "hackernews"
    assert result["url"] == "https://news.ycombinator.com/item?id=100"
    assert result["title"] == "Show HN: Plain text"
    text = result["text"]
    assert "score: 321" in text
    assert "Ask body." in text
    assert text.count("--- comment ") == 5
    assert "comment 106" not in text


def test_hacker_news_form_that_is_not_an_item_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://news.ycombinator.com/news") is None
    assert fallbacks.public_api_fallback("https://news.ycombinator.com/user?id=pg") is None


def test_hacker_news_network_error_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://news.ycombinator.com/item?id=100") is None


def test_text_is_capped(monkeypatch):
    body = "x" * 80_000

    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"items": [{"title": "long", "body": body}]})

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback("https://stackoverflow.com/questions/1")
    assert result is not None
    assert len(result["text"]) == fallbacks._MAX_TEXT


def test_an_unrelated_url_never_touches_the_network(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://example.com/") is None
    assert fallbacks.public_api_fallback("https://www.bilibili.com/video/av170001") is None
    assert fallbacks.public_api_fallback("https://book.douban.com/subject/1292052/") is None
    assert fallbacks.public_api_fallback("") is None


def test_bilibili_video_summary_and_pages(monkeypatch):
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path))
        if request.url.host == "b23.tv":
            return httpx.Response(302, headers={"location": "https://www.bilibili.com/video/BV1GJ411x7h7"})
        return _json(
            {
                "code": 0,
                "message": "OK",
                "data": {
                    "bvid": "BV1GJ411x7h7",
                    "title": "Never Gonna Give You Up",
                    "desc": "Official MV",
                    "pubdate": 1577835803,
                    "owner": {"name": "Sony Music"},
                    "stat": {"view": 107725304, "danmaku": 151306},
                    "pages": [
                        {"page": 1, "part": "Official MV", "duration": 213},
                        {"page": 2, "part": "Behind the scenes", "duration": 75},
                    ],
                },
            }
        )

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback("https://www.bilibili.com/video/BV1GJ411x7h7?spm_id_from=1")

    assert result is not None
    assert result["backend"] == "bilibili"
    assert result["url"] == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert result["title"] == "Never Gonna Give You Up"
    text = result["text"]
    assert "UP: Sony Music" in text
    assert "107725304 views" in text and "151306 danmaku" in text
    assert "Official MV" in text
    assert "P2 Behind the scenes (1:15)" in text

    short = fallbacks.public_api_fallback("https://b23.tv/abc123")
    assert short is not None and short["url"] == "https://www.bilibili.com/video/BV1GJ411x7h7"
    assert ("b23.tv", "/abc123") in seen


def test_bilibili_form_that_is_not_a_video_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://www.bilibili.com/") is None
    assert fallbacks.public_api_fallback("https://space.bilibili.com/12345") is None
    assert fallbacks.public_api_fallback("https://www.bilibili.com/video/av170001") is None


def test_bilibili_network_error_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://www.bilibili.com/video/BV1GJ411x7h7") is None


def test_bilibili_api_error_payload_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"code": -404, "message": "啥都木有", "data": None})

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://www.bilibili.com/video/BV1GJ411x7h7") is None


def test_douban_movie_summary(monkeypatch):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"path": request.url.path, "referer": request.headers.get("referer")})
        return _json(
            {
                "id": "1292052",
                "title": "The Shawshank Redemption",
                "original_title": "The Shawshank Redemption",
                "year": "1994",
                "rating": {"value": 9.7, "count": 3353644},
                "directors": [{"name": "Frank Darabont"}],
                "actors": [{"name": "Tim Robbins"}, {"name": "Morgan Freeman"}],
                "genres": ["Drama", "Crime"],
                "durations": ["142 minutes"],
                "intro": "A banker is sentenced to life in Shawshank prison.",
            }
        )

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    result = fallbacks.public_api_fallback("https://movie.douban.com/subject/1292052/")

    assert result is not None
    assert result["backend"] == "douban"
    assert result["url"] == "https://movie.douban.com/subject/1292052/"
    assert result["title"] == "The Shawshank Redemption (1994)"
    text = result["text"]
    assert "rating: 9.7/10 from 3353644 ratings" in text
    assert "directors: Frank Darabont" in text
    assert "cast: Tim Robbins, Morgan Freeman" in text
    assert "A banker is sentenced to life" in text
    assert seen == [
        {"path": "/rexxar/api/v2/movie/1292052", "referer": "https://m.douban.com/"}
    ]


def test_douban_form_that_is_not_a_movie_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"unexpected request: {request.url}")

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://movie.douban.com/") is None
    assert fallbacks.public_api_fallback("https://movie.douban.com/subject/abc/") is None
    assert fallbacks.public_api_fallback("https://book.douban.com/subject/1292052/") is None


def test_douban_network_error_is_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(fallbacks, "_client", _serve(handler))
    assert fallbacks.public_api_fallback("https://movie.douban.com/subject/1292052/") is None


def test_douban_blocked_response_is_none(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "_client",
        _serve(lambda request: httpx.Response(403, json={"msg": "login required"})),
    )
    assert fallbacks.public_api_fallback("https://movie.douban.com/subject/1292052/") is None
