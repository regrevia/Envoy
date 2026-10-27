"""A sign-in wall must be reported as one, not sold as partial content."""

from __future__ import annotations

import pytest

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest

INSTAGRAM_WALL = (
    "<html><head><title>Post isn't available • Instagram</title></head><body>"
    "<p>Log in</p><p>Post isn't available</p>"
    "<p>The link may be broken, or the profile may have been removed.</p>"
    "<p>Sign up for Instagram</p><p>By continuing, you agree to Instagram's</p>"
    "</body></html>"
)


@pytest.mark.parametrize(
    ("title", "text", "expected"),
    [
        ("Post isn't available • Instagram", "Log in\n\nSign up for Instagram", True),
        ("Sign in - Example", "You must be logged in to view this page.", True),
        ("长文", "登录后查看完整内容", True),
        ("A real article", "body text " * 60, False),
        ("A real article", "body text " * 60 + "log in to continue", False),
    ],
)
def test_login_wall_markers(title, text, expected):
    assert reader._looks_like_login_wall(title, text) is expected


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate(
        {
            "protocol_version": "envoy.read/1",
            "request_id": "wall-1",
            "source": {"url": url},
            "goal": "what is this post",
            "budget": {"max_work_seconds": 30},
        }
    )


def test_instagram_login_wall_is_blocked_not_partial(monkeypatch):
    request = _request("https://www.instagram.com/reel/C3x0vQ0J6gF/")
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *a, **k: True)
    monkeypatch.setattr(reader, "_should_read_media_first", lambda *a, **k: False)
    monkeypatch.setattr(reader, "_url_is_video_page", lambda *a, **k: False)
    monkeypatch.setattr(reader, "fetch_feed", lambda *a, **k: None)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *a, **k: None)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *a, **k: ("https://www.instagram.com/reel/C3x0vQ0J6gF/", INSTAGRAM_WALL),
    )
    monkeypatch.setattr(reader, "_read_with_isolated_browser", lambda *a, **k: (None, None))

    response = reader._read_public_page_once(request)

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "auth_required"
    assert response.errors[0].action_required is True
    assert "sign-in" in response.errors[0].message


def test_static_login_wall_skips_the_browser(monkeypatch):
    """A wall in the static HTML is final: booting Chromium only re-shows it."""
    request = _request("https://example.com/members-only")
    browser_calls: list[str] = []
    wall = (
        "<html><head><title>\u5fae\u4fe1\u516c\u4f17\u5e73\u53f0</title></head><body>"
        "<p>\u8bf7\u5148\u767b\u5f55</p><p>\u767b\u5f55\u540e\u67e5\u770b</p></body></html>"
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *a, **k: True)
    monkeypatch.setattr(reader, "_should_read_media_first", lambda *a, **k: False)
    monkeypatch.setattr(reader, "_url_is_video_page", lambda *a, **k: False)
    monkeypatch.setattr(reader, "fetch_feed", lambda *a, **k: None)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *a, **k: None)
    monkeypatch.setattr(reader, "_fetch_html", lambda *a, **k: ("https://example.com/members-only", wall))
    monkeypatch.setattr(
        reader,
        "_read_with_isolated_browser",
        lambda *a, **k: browser_calls.append("boot") or (None, None),
    )

    response = reader._read_public_page_once(request)

    assert response.result.value == "blocked"
    assert browser_calls == []


def test_thin_platform_page_is_partial_not_ready(monkeypatch):
    """A platform page that yields only a title must not claim to be complete."""
    request = _request("https://weibo.com/u/1642904381")
    shell = (
        "<html><head><title>微博</title></head><body>"
        "<div id=\"app\">微信公众平台 请在微信客户端打开链接 环境异常</div></body></html>"
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *a, **k: True)
    monkeypatch.setattr(reader, "_should_read_media_first", lambda *a, **k: False)
    monkeypatch.setattr(reader, "_url_is_video_page", lambda *a, **k: False)
    monkeypatch.setattr(reader, "fetch_feed", lambda *a, **k: None)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *a, **k: None)
    monkeypatch.setattr(reader, "_read_with_isolated_browser", lambda *a, **k: (None, None))
    monkeypatch.setattr(reader, "_fetch_html", lambda *a, **k: (request.source.url, shell))
    response = reader._read_public_page_once(request)
    assert response.result.value == "partial"
    details = response.coverage.modalities["text"].details
    assert details.get("thin_platform_page") is True
    assert response.coverage.modalities["text"].unresolved


def test_unresolvable_host_is_a_network_error_not_a_policy_rejection():
    """A DNS failure must not be sold as "not a public URL"."""
    request = _request("https://no-such-host.invalid/article")
    response = reader._read_public_page_once(request)
    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "network_error"
    assert "dns" in response.errors[0].message.lower()


def test_loopback_url_keeps_the_policy_message():
    request = _request("http://127.0.0.1:8080/private")
    response = reader._read_public_page_once(request)
    assert response.errors[0].code.value == "source_unavailable"
    assert "Only public HTTP(S) URLs are allowed" in response.errors[0].message


def test_json_status_stub_is_not_a_complete_read():
    """A share API answering {"result":2,...} is a stub, not the post."""
    import pytest as _pytest

    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "json-stub",
        "source": {"url": "https://www.kuaishou.com/f/X-1WVMwQuRjthjtv"},
        "goal": "what is this post",
        "budget": {"max_work_seconds": 30},
    })
    stub = b'{"result":2,"error_msg":null,"request_id":"793075897050316594"}'
    with _pytest.raises(reader.PublicPageError) as excinfo:
        reader._parse_text_source(
            request,
            {
                "url": "https://www.kuaishou.com/f/X-1WVMwQuRjthjtv",
                "data": stub,
                "mime_type": "application/json",
                "encoding": "utf-8",
            },
        )
    assert excinfo.value.code.value == "empty_content"
    assert "status stub" in str(excinfo.value)


def test_real_api_json_is_still_content():
    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "json-ok",
        "source": {"url": "https://api.github.com/repos/regrevia/Envoy"},
        "goal": "what is this repo",
        "budget": {"max_work_seconds": 30},
    })
    body = b'{"full_name":"regrevia/Envoy","description":"Read public URLs as evidence for agent hosts."}'
    response = reader._parse_text_source(
        request,
        {
            "url": "https://api.github.com/repos/regrevia/Envoy",
            "data": body,
            "mime_type": "application/json",
            "encoding": "utf-8",
        },
    )
    assert response.result.value == "ready"
