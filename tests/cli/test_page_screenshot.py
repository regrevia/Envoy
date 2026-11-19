"""A visual read keeps a picture of the page it actually rendered.

A platform that fills its body only inside a signed-in browser hands the host
text that is not the page: an empty shell plus a diagnosis. The picture is the
one piece of evidence the human can check without opening a browser themselves,
so it has to reach the payload and the text block -- and it must never be taken
by a read that did not ask for visual material.
"""

from __future__ import annotations

import hashlib
import types

from envoy_cli import browser, mcp, reader
from envoy_core.schemas.read_v1 import ReadRequest

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"envoy-page-picture"


class _ShotPage:
    """A page that can be photographed, like the real Playwright one."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.paths: list[str] = []

    def screenshot(self, path):
        if self.fail:
            raise RuntimeError("the renderer went away mid-capture")
        with open(path, "wb") as handle:
            handle.write(PNG_BYTES)
        self.paths.append(str(path))


def _request(url: str, *, include_visual: bool) -> ReadRequest:
    return ReadRequest.model_validate({
        "request_id": "screenshot-1",
        "source": {"kind": "auto", "url": url},
        "goal": "read this note",
        "scope": {"include_visual": include_visual},
        "budget": {"max_work_seconds": 5},
    })


def test_a_page_picture_lands_beside_the_material(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    page = _ShotPage()

    path = browser._page_screenshot(page, "https://www.xiaohongshu.com/explore/abc")

    assert path
    assert path.endswith(".png")
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == hashlib.sha256(
        PNG_BYTES
    ).hexdigest()
    # The name carries the host so a human reading a directory listing can tell
    # which page a leftover picture belongs to.
    assert "xiaohongshu.com" in path


def test_a_picture_that_cannot_be_taken_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))

    assert browser._page_screenshot(_ShotPage(fail=True), "https://example.org/a") == ""


def test_the_read_only_asks_for_the_picture_when_visual_material_was_requested(
    monkeypatch,
):
    seen: list[dict] = []

    def fake_read_page(url, **kwargs):
        seen.append(kwargs)
        return browser.BrowserPage(
            url=url, title="t", text="body", html="<html></html>"
        )

    monkeypatch.setattr(browser, "read_page", fake_read_page)
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *a, **k: True)
    monkeypatch.setattr(reader, "_remaining_work_seconds", lambda *_a, **_k: 5)

    for include_visual in (False, True):
        reader._read_with_isolated_browser(
            _request("https://example.org/a", include_visual=include_visual),
            "https://example.org/a",
            {},
        )

    assert [item["capture_screenshot"] for item in seen] == [False, True]


def _empty_shell(monkeypatch, url: str, *, screenshot: str, text: str = ""):
    """A platform page that renders its body only inside a signed-in browser."""

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *a, **k: True)
    monkeypatch.setattr(reader, "_should_read_media_first", lambda *a, **k: False)
    monkeypatch.setattr(reader, "_url_is_video_page", lambda *a, **k: False)
    monkeypatch.setattr(reader, "fetch_feed", lambda *a, **k: None)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *a, **k: None)
    monkeypatch.setattr(reader, "explicit_proxy", lambda: None)
    monkeypatch.setattr(reader, "detected_proxy", lambda: None)
    monkeypatch.setattr(
        reader,
        "_read_with_isolated_browser",
        lambda *a, **k: (
            browser.BrowserPage(
                url=url,
                title="小红书",
                text=text,
                html="<html><body><div id='app'></div></body></html>",
                screenshot_path=screenshot,
            ),
            None,
        ),
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda target, *a, **k: (
            target,
            "<html><head><title>小红书</title></head><body></body></html>",
        ),
    )


def test_the_empty_shell_refusal_carries_a_picture_of_the_page(tmp_path, monkeypatch):
    """The note link that started this: blocked, but the human can see the page."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    shot = tmp_path / "page.png"
    shot.write_bytes(PNG_BYTES)
    url = "https://www.xiaohongshu.com/explore/0123456789abcdef"
    _empty_shell(monkeypatch, url, screenshot=str(shot))

    payload = mcp._read_payload(
        reader._read_public_page_once(_request(url, include_visual=True))
    )

    assert payload["result"] == "blocked"
    screenshot = [
        item for item in payload["resources"]
        if (item.get("transform") or {}).get("derived") == "page_screenshot"
    ]
    assert len(screenshot) == 1
    assert screenshot[0]["mime_type"] == "image/png"
    assert screenshot[0]["delivery"] == {"type": "local_file", "path": str(shot)}
    assert screenshot[0]["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert screenshot[0]["expires_at"]

    text = mcp._material_content_text(
        payload, "envoy_read", types.SimpleNamespace(max_chars=4000)
    )
    # Both halves of the answer have to survive: what to do, and what it looks
    # like. The picture is printed even though the same payload asks for a
    # sign-in.
    assert f"![页面截图](<{shot}>)" in text
    assert payload["needs_user_action"]["action_id"]


def test_a_text_read_refuses_without_photographing_the_page(tmp_path, monkeypatch):
    """A page behind a session must not be photographed by a plain read."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    shot = tmp_path / "page.png"
    shot.write_bytes(PNG_BYTES)
    url = "https://www.xiaohongshu.com/explore/0123456789abcdef"
    _empty_shell(monkeypatch, url, screenshot=str(shot))

    payload = mcp._read_payload(
        reader._read_public_page_once(_request(url, include_visual=False))
    )

    assert payload["result"] == "blocked"
    assert payload.get("resources") is None or payload["resources"] == []
    text = mcp._material_content_text(
        payload, "envoy_read", types.SimpleNamespace(max_chars=4000)
    )
    assert "页面截图" not in text


def test_a_page_picture_is_not_counted_as_one_of_the_pages_images(
    tmp_path, monkeypatch
):
    """An image tally that includes the page picture hides a real gap."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    shot = tmp_path / "page.png"
    shot.write_bytes(PNG_BYTES)
    url = "https://www.xiaohongshu.com/explore/0123456789abcdef"
    _empty_shell(
        monkeypatch, url, screenshot=str(shot), text="<html><body><p>正文</p></body></html>"
    )

    payload = mcp._read_payload(
        reader._read_public_page_once(_request(url, include_visual=True))
    )

    if payload["result"] != "blocked":
        images = payload["coverage"]["modalities"]["images"]
        assert images["delivered"] == 0
        assert payload["coverage"]["modalities"]["screenshot"]["delivered"] == 1
