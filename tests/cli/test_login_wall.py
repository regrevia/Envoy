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
