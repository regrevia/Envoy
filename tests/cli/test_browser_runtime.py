from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import types

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import browser, netpolicy, reader
from envoy_cli.capabilities import capability_catalog
from envoy_core.schemas.read_v1 import (
    ExecutionState,
    HostDelivery,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadRequest,
    ReadResponse,
    ReadResult,
    ResourceRef,
    TextContent,
)


@dataclass
class FakeResponse:
    status: int = 200


class FakePage:
    def __init__(self, body_text: str):
        self.body_text = body_text
        self.frames = []
        self.url = "https://example.org/article"
        self.navigated_to = None
        self.routes = []
        self.goto_response = FakeResponse()
        self.context = None
        self.chromium = None
        self.closed = False

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def goto(self, url, **_kwargs):
        self.navigated_to = url
        self.goto_options = _kwargs
        self.url = url
        return self.goto_response

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, _milliseconds):
        if self.context and self.context.headless is False:
            if self.chromium.visible_text is not None:
                self.body_text = self.chromium.visible_text

    def evaluate(self, script):
        self.chromium.evaluated_scripts.append(script)
        if (
            "dismiss-generic-overlay" in script
            and self.chromium.blocking_overlays > 0
        ):
            self.chromium.blocking_overlays -= 1
            self.body_text = self.chromium.article_text
            return 1
        if (
            self.context
            and self.context.headless
            and "window.scrollTo" in script
            and self.chromium.reopen_overlay_after_scroll
        ):
            self.chromium.reopen_overlay_after_scroll = False
            self.chromium.relative_panel_login_overlay = True
            self.body_text = (
                "视频章节摘要：深圳岗位招聘，登录后免费畅享高清视频，验证码登录"
            )
            return None
        if (
            self.context
            and self.context.headless
            and "dismiss-login-overlay" in script
            and (
                (
                    self.chromium.dismissible_login_overlay
                    and '["fixed", "absolute"]' in script
                )
                or (
                    self.chromium.relative_panel_login_overlay
                    and "svg" in script
                    and "area < viewportArea * 0.98" in script
                    and "dispatchEvent(new MouseEvent" in script
                )
                or (
                    self.chromium.static_login_overlay
                    and "centeredDialogCard" in script
                    and "dispatchEvent(new MouseEvent" in script
                )
            )
        ):
            self.chromium.dismissible_login_overlay = False
            self.chromium.relative_panel_login_overlay = False
            self.chromium.static_login_overlay = False
            self.body_text = (
                self.chromium.post_dismissal_text
                or "视频正文：深圳岗位招聘，工作内容和职位要求如下。"
            )
            return 1
        return 0

    def title(self):
        return "Example article"

    def locator(self, selector):
        assert selector == "body"
        return self

    def inner_text(self, **_kwargs):
        return self.body_text

    def content(self):
        return f"<html><head><title>{self.title()}</title></head><body>{self.body_text}</body></html>"

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, headless, initial_text):
        self.headless = headless
        self.page = FakePage(initial_text)
        self.page.context = self
        self.closed = False
        self.routes = []
        self.extra_pages = []
        self.listeners = {}

    @property
    def pages(self):
        return [self.page, *self.extra_pages]

    def on(self, event, handler):
        self.listeners[event] = handler

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))


class FakeChromium:
    def __init__(self, initial_text):
        self.initial_text = initial_text
        self.visible_text = "岗位：AI 产品经理。公司：示例科技。工作地点：深圳。"
        self.dismissible_login_overlay = False
        self.relative_panel_login_overlay = False
        self.static_login_overlay = False
        self.reopen_overlay_after_scroll = False
        self.post_dismissal_text = None
        self.blocking_overlays = 0
        self.article_text = "正文：这是一篇关于检索增强生成的深度长文，包含方法与实验结论。"
        self.evaluated_scripts = []
        self.launches = []

    def launch_persistent_context(self, user_data_dir, **kwargs):
        context = FakeContext(kwargs["headless"], self.initial_text)
        context.user_data_dir = user_data_dir
        context.page.chromium = self
        self.launches.append((context, kwargs))
        return context


class FakePlaywrightManager:
    def __init__(self, chromium):
        self.chromium = chromium

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_public_page_uses_only_headless_browser(tmp_path):
    chromium = FakeChromium("A public article with enough readable content.")
    requests = []

    result = browser.read_page(
        "https://example.org/article",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
        on_user_action=requests.append,
    )

    assert result.text == "A public article with enough readable content."
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert requests == []
    assert chromium.launches[0][0].closed is True
    assert chromium.launches[0][0].page.goto_options["wait_until"] == "commit"


def test_youtube_sign_in_to_confirm_bot_is_detected_as_access_challenge():
    page = FakePage("Sign in to confirm you're not a bot")

    _title, _text, challenge = browser._page_state(page)

    assert challenge is True


def test_system_proxy_is_only_a_fallback_after_a_direct_attempt(monkeypatch):
    monkeypatch.delenv("ENVOY_BROWSER_PROXY", raising=False)
    monkeypatch.setattr(netpolicy, "getproxies", lambda: {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    })

    assert netpolicy.proxy_plan() == [None, "http://proxy.example:8080"]
    assert browser._browser_proxy_server() == "http://proxy.example:8080"


def test_browser_does_not_put_proxy_credentials_in_chromium_args(monkeypatch):
    monkeypatch.delenv("ENVOY_BROWSER_PROXY", raising=False)
    monkeypatch.setattr(netpolicy, "getproxies", lambda: {
        "https": "http://user:secret@proxy.example:8080",
    })

    assert netpolicy.detected_proxy() is None
    assert netpolicy.proxy_plan() == [None]
    assert browser._browser_proxy_server() is None


def test_explicit_browser_proxy_overrides_system_proxy(monkeypatch):
    monkeypatch.setenv("ENVOY_BROWSER_PROXY", "socks5://127.0.0.1:7898")
    monkeypatch.setattr(netpolicy, "getproxies", lambda: {
        "https": "http://system-proxy.example:8080",
    })

    assert netpolicy.proxy_plan() == ["socks5://127.0.0.1:7898"]
    assert browser._browser_proxy_server() == "socks5://127.0.0.1:7898"


def test_direct_pin_disables_the_proxy_fallback(monkeypatch):
    monkeypatch.setenv("ENVOY_BROWSER_PROXY", "none")
    monkeypatch.setattr(netpolicy, "getproxies", lambda: {
        "https": "http://proxy.example:8080",
    })

    assert netpolicy.proxy_plan() == [None]
    assert browser._browser_proxy_server() is None


def test_dismissible_login_overlay_is_closed_in_headless_browser(tmp_path):
    chromium = FakeChromium("请登录后继续观看")
    chromium.dismissible_login_overlay = True

    result = browser.read_page(
        "https://www.douyin.com/video/7698386448961683813",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
    )

    assert "深圳岗位招聘" in result.text
    assert chromium.dismissible_login_overlay is False
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert chromium.launches[0][0].closed is True
    assert any("dismiss-login-overlay" in script for script in chromium.evaluated_scripts)


def test_unmarked_login_overlay_uses_relative_panel_close_in_headless_browser(tmp_path):
    chromium = FakeChromium("登录后免费畅享高清视频")
    chromium.relative_panel_login_overlay = True
    chromium.post_dismissal_text = (
        "视频正文：深圳岗位招聘，工作内容和职位要求如下。"
        "请先登录后发表评论"
    )
    requests = []

    result = browser.read_page(
        "https://www.douyin.com/video/7698386448961683813",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
        on_user_action=requests.append,
        max_work_seconds=1,
    )

    assert "深圳岗位招聘" in result.text
    assert chromium.relative_panel_login_overlay is False
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert requests == []
    assert chromium.launches[0][0].closed is True
    assert any(
        "svg" in script
        and "area < viewportArea * 0.98" in script
        and "dispatchEvent(new MouseEvent" in script
        for script in chromium.evaluated_scripts
    )


def test_login_overlay_inside_iframe_is_closed_without_showing_browser(tmp_path, monkeypatch):
    chromium = FakeChromium("视频正文已加载。")
    chromium.relative_panel_login_overlay = True
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)

    class LoginFrame:
        def evaluate(self, script):
            if "dismiss-login-overlay" not in script:
                return 0
            chromium.relative_panel_login_overlay = False
            chromium.launches[0][0].page.body_text = (
                "视频正文：公开视频内容和岗位介绍已经加载完成。"
            )
            return 1

    chromium.frames = [LoginFrame()]
    original_launch = chromium.launch_persistent_context

    def launch_with_frame(user_data_dir, **kwargs):
        context = original_launch(user_data_dir, **kwargs)
        context.page.frames = chromium.frames
        return context

    chromium.launch_persistent_context = launch_with_frame
    result = browser.read_page(
        "https://video.example/watch/123",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
    )

    assert "公开视频内容" in result.text
    assert chromium.relative_panel_login_overlay is False
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]


def test_login_overlay_in_static_hashed_card_is_closed_headlessly(tmp_path, monkeypatch):
    chromium = FakeChromium("视频页面已加载，登录弹窗覆盖播放器。")
    chromium.static_login_overlay = True
    chromium.post_dismissal_text = "视频章节和公开播放器内容正常显示。"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)

    result = browser.read_page(
        "https://video.example/watch/123",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
    )

    assert "公开播放器内容" in result.text
    assert chromium.static_login_overlay is False
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]


def test_consent_wall_is_dismissed_without_showing_a_window(tmp_path):
    chromium = FakeChromium("本网站使用 Cookie，请接受后继续阅读。")
    chromium.blocking_overlays = 1

    result = browser.read_page(
        "https://example.org/consent-article",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
    )

    assert "检索增强生成" in result.text
    assert chromium.blocking_overlays == 0
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert any("dismiss-generic-overlay" in script for script in chromium.evaluated_scripts)


def test_popup_tab_is_closed_and_does_not_become_the_read_result(tmp_path):
    chromium = FakeChromium("请下载 App 后阅读全文")
    chromium.blocking_overlays = 1
    context_ref = []

    original_launch = chromium.launch_persistent_context

    def launch(user_data_dir, **kwargs):
        context = original_launch(user_data_dir, **kwargs)
        popup = FakePage("只存在于弹窗里的替代正文")
        popup.context = context
        popup.chromium = chromium
        popup.closed = False
        context.extra_pages.append(popup)
        context_ref.append(popup)
        return context

    chromium.launch_persistent_context = launch

    result = browser.read_page(
        "https://example.org/app-wall",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
    )

    assert "检索增强生成" in result.text
    assert context_ref and context_ref[0].closed is True
    assert "page" in chromium.launches[0][0].listeners


def test_close_extra_pages_never_closes_the_main_page():
    class Context:
        def __init__(self, pages):
            self._pages = pages

        @property
        def pages(self):
            return self._pages

    main = FakePage("main")
    popup = FakePage("popup")
    popup.closed = False

    closed = browser._close_extra_pages(Context([main, popup]), main)

    assert closed == 1
    assert popup.closed is True
    assert main.closed is False


def test_login_overlay_appearing_during_lazy_load_is_closed_before_capture(tmp_path):
    chromium = FakeChromium("视频页面已加载。")
    chromium.reopen_overlay_after_scroll = True
    chromium.relative_panel_login_overlay = False
    chromium.post_dismissal_text = (
        "视频章节摘要：深圳岗位招聘，相关企业和岗位如下。"
        "请先登录后发表评论"
    )
    requests = []

    result = browser.read_page(
        "https://www.douyin.com/video/7698386448961683813",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
        on_user_action=requests.append,
        max_work_seconds=1,
    )

    assert "深圳岗位招聘" in result.text
    assert "登录后免费畅享高清视频" not in result.text
    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert requests == []



def test_access_challenge_escalates_to_visible_profile_and_resumes(tmp_path, monkeypatch):
    # The terminal gate itself is covered by test_visible_browser_guard.py; this
    # test is about what the escalation does once a human has opted in.
    monkeypatch.setattr(browser, "_visible_browser_allowed", lambda override: True)
    chromium = FakeChromium("安全验证，请完成登录后继续。")
    requests = []

    result = browser.read_page(
        "https://example.org/article",
        profile_dir=tmp_path / "profile",
        playwright_factory=lambda: FakePlaywrightManager(chromium),
        on_user_action=requests.append,
        max_work_seconds=5,
        allow_visible=True,
    )

    assert result.text == "岗位：AI 产品经理。公司：示例科技。工作地点：深圳。"
    assert [launch[1]["headless"] for launch in chromium.launches] == [True, False]
    assert chromium.launches[0][0].closed is True
    assert chromium.launches[1][0].closed is True
    assert requests == ["https://example.org/article"]
    assert all(
        Path(context.user_data_dir) == tmp_path / "profile"
        for context, _kwargs in chromium.launches
    )


def test_unresolved_challenge_fails_closed_after_visible_browser_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "_visible_browser_allowed", lambda override: True)
    chromium = FakeChromium("安全验证，请完成登录后继续。")
    chromium.visible_text = None

    with pytest.raises(browser.BrowserReadError, match="still requires login"):
        browser.read_page(
            "https://example.org/article",
            profile_dir=tmp_path / "profile",
            playwright_factory=lambda: FakePlaywrightManager(chromium),
            max_work_seconds=1,
            allow_visible=True,
        )

    assert [launch[1]["headless"] for launch in chromium.launches] == [True, False]
    assert all(context.closed for context, _kwargs in chromium.launches)


def test_challenge_stays_headless_unless_visible_mode_is_opted_in(tmp_path):
    chromium = FakeChromium("安全验证，请完成登录后继续。")
    requests = []

    with pytest.raises(browser.BrowserChallengeRequired, match="does not open a window"):
        browser.read_page(
            "https://example.org/article",
            profile_dir=tmp_path / "profile",
            playwright_factory=lambda: FakePlaywrightManager(chromium),
            on_user_action=requests.append,
            max_work_seconds=5,
        )

    assert [launch[1]["headless"] for launch in chromium.launches] == [True]
    assert requests == []


@pytest.mark.parametrize(
    "initial_html",
    [
        "<html><title>Security verification</title><body>安全验证，请完成登录后继续。</body></html>",
        "<html><title>Article</title><body></body></html>",
    ],
)
def test_reader_uses_browser_snapshot_for_challenge_or_empty_html(
    initial_html,
    monkeypatch,
    tmp_path,
):
    source_url = "https://example.org/article"
    browser_page = browser.BrowserPage(
        url=source_url,
        title="Article",
        text="岗位：AI 产品经理。公司：示例科技。工作地点：深圳。",
        html=(
            "<html><title>Article</title><body><p>"
            "岗位：AI 产品经理。公司：示例科技。工作地点：深圳。"
            "</p></body></html>"
        ),
    )
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, initial_html))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    calls = []

    def fake_read_page(url, **kwargs):
        calls.append((url, kwargs))
        return browser_page

    monkeypatch.setattr(browser, "read_page", fake_read_page)
    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "browser-fallback",
        "source": {"url": source_url},
        "goal": "Find the role and company",
    }))

    assert response.result.value == "ready"
    assert any(
        block.content.type == "text"
        and "AI 产品经理" in block.content.text
        for block in response.blocks
    )
    assert response.coverage.modalities["text"].details["reader"] == "isolated_chromium"
    assert calls and calls[0][0] == source_url
    assert calls[0][1]["max_work_seconds"] > 0


def test_read_idempotency_key_replays_material_without_fetching_twice(monkeypatch, tmp_path):
    source_url = "https://example.org/idempotent-article"
    calls = []
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (
        calls.append(source_url) or source_url,
        "<html><title>Article</title><body>"
        "<p>This article contains enough public text to be read and reused.</p>"
        "</body></html>",
    ))

    first = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "idempotent-first",
        "idempotency_key": "same-read-op",
        "session_id": "host-session-a",
        "source": {"url": source_url},
    }))
    retry = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "idempotent-retry",
        "idempotency_key": "same-read-op",
        "session_id": "host-session-b",
        "source": {"url": source_url},
    }))

    assert calls == [source_url]
    assert retry.request_id == "idempotent-retry"
    assert retry.session_id == "host-session-b"
    assert retry.material_id != first.material_id
    assert retry.blocks == first.blocks
    replayed_material = reader._load_matching_material(
        retry.material_id,
        retry.source_revision,
        "host-session-b",
    )
    assert replayed_material is not None


def test_temporary_blocked_read_result_is_retryable_but_needs_host_is_cacheable():
    from envoy_core.schemas.read_v1 import ErrorCode, ReadRequest

    temporary = reader._blocked(
        ReadRequest.model_validate({
            "request_id": "temporary-network",
            "source": {"url": "https://example.org"},
        }),
        ErrorCode.NETWORK_ERROR,
        "temporary network issue",
    )
    needs_host = ReadResponse(
        request_id="visual-handoff",
        execution_state=ExecutionState.WAITING_FOR_HOST,
        result=ReadResult.NEEDS_HOST,
    )
    permanent = reader._blocked(
        ReadRequest.model_validate({
            "request_id": "permanent-invalid",
            "source": {"url": "https://example.org"},
        }),
        ErrorCode.INVALID_REQUEST,
        "invalid request",
    )

    assert reader._read_operation_result_is_cacheable(
        temporary.model_dump(mode="json")
    ) is False
    assert reader._read_operation_result_is_cacheable(
        needs_host.model_dump(mode="json")
    ) is True
    assert reader._read_operation_result_is_cacheable(
        permanent.model_dump(mode="json")
    ) is True


def test_cached_frame_replay_rebinds_resource_lease_to_new_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    old_frame = tmp_path / "materials" / "frames-mat-old" / "frame-001.jpg"
    old_frame.parent.mkdir(parents=True)
    old_frame.write_bytes(b"frame")
    reader._store_material("mat-" + "a" * 24, {
        "source_url": "https://example.org/video",
        "source_revision": "b" * 64,
        "session_id": "session-a",
        "text": "",
        "expires_at": 9_999_999_999,
        "visual_resources": [{
            "resource_id": "resource-frame",
            "source_block_id": "blk-frame",
            "source_url": "https://example.org/video",
            "frame_path": str(old_frame),
        }],
    })
    response = ReadResponse(
        request_id="replay-old",
        session_id="session-a",
        material_id="mat-" + "a" * 24,
        source_revision="b" * 64,
        execution_state=ExecutionState.WAITING_FOR_HOST,
        result=ReadResult.NEEDS_HOST,
        resources=[ResourceRef(
            resource_id="resource-frame",
            mime_type="image/jpeg",
            delivery=HostDelivery(type="local_file", path=str(old_frame)),
        )],
    )

    rebound = reader._rebind_cached_response_session(
        response,
        "session-b",
        "replay-new",
    )

    new_path = rebound.resources[0].delivery.path
    assert new_path != str(old_frame)
    assert new_path is not None and Path(new_path).is_file()
    assert Path(new_path).read_bytes() == b"frame"
    import shutil

    shutil.rmtree(old_frame.parent)
    assert Path(new_path).is_file()


def test_reader_uses_browser_after_http_auth_required(monkeypatch):
    source_url = "https://example.org/article"
    browser_page = browser.BrowserPage(
        url=source_url,
        title="Article",
        text="A public article rendered in the isolated browser with enough readable text.",
        html=(
            "<html><title>Article</title><body><p>"
            "A public article rendered in the isolated browser with enough readable text."
            "</p></body></html>"
        ),
    )

    def require_auth(*_args, **_kwargs):
        raise reader.PublicPageError("The server requires access", reader.ErrorCode.AUTH_REQUIRED)

    monkeypatch.setattr(reader, "_fetch_html", require_auth)
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: browser_page)
    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "browser-auth-fallback",
        "source": {"url": source_url},
    }))

    assert response.result.value == "ready"


def test_reader_preserves_browser_challenge_instead_of_returning_empty_content(
    monkeypatch,
):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(
        reader,
        "extract_public_subtitles",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "error": "Fresh cookies are required",
        },
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (source_url, "<html><body></body></html>"),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)

    def challenge_timeout(*_args, **_kwargs):
        raise browser.BrowserChallengeRequired(
            "The visible page still requires login or security verification; "
            "complete it in Envoy's browser and retry the link"
        )

    monkeypatch.setattr(browser, "read_page", challenge_timeout)
    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-browser-challenge",
        "source": {"url": source_url},
        "goal": "Read the job details in the video",
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "challenge_required"
    assert response.errors[0].action_required is True
    assert "visible page still requires login" in response.errors[0].message


def test_machine_error_envelope_is_not_returned_as_page_material():
    payload = '{"error":{"message":"您当前请求存在异常，暂时限制本次访问。"}}'

    assert reader._looks_like_machine_error_payload(payload) is True
    assert reader._looks_like_machine_error_payload('{"code":0,"data":[1,2,3]}') is False
    assert reader._looks_like_machine_error_payload(
        "A normal article paragraph that is comfortably longer than a JSON envelope."
    ) is False


def test_json_error_page_is_reported_as_blocked_not_as_content(monkeypatch):
    source_url = "https://www.zhihu.com/question/19550225"
    payload = '{"error":{"message":"您当前请求存在异常，暂时限制本次访问。"}}'
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (source_url, f"<html><body><pre>{payload}</pre></body></html>"),
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "json-error-envelope",
        "source": {"url": source_url},
    }))

    assert response.result is ReadResult.BLOCKED
    assert response.errors[0].code.value in {"auth_required", "challenge_required"}
    assert response.errors[0].action_required is True
    assert "请求存在异常" not in " ".join(
        block.text.value for block in response.blocks if block.text
    )


def test_browser_navigation_timeout_is_reported_as_network_error(monkeypatch):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            reader.PublicPageError("Static request was denied", reader.ErrorCode.AUTH_REQUIRED)
        ),
    )

    def navigation_timeout(*_args, **_kwargs):
        raise TimeoutError("navigation timed out")

    monkeypatch.setattr(browser, "read_page", navigation_timeout)
    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "browser-navigation-timeout",
        "source": {"url": "https://example.org/article"},
    }))

    assert response.result == ReadResult.BLOCKED
    assert response.errors[0].code.value == "network_error"
    assert "navigation timed out" in response.errors[0].message


def test_video_extractor_auth_error_falls_back_to_visible_browser_page(monkeypatch):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    media_url = "https://cdn.example.test/current-post.mp4"
    page = browser.BrowserPage(
        url=source_url,
        title="Douyin video",
        text="视频标题：工作介绍和招聘岗位详细信息，请查看完整职位和企业介绍。",
        html="<html><title>Douyin video</title><body>视频标题：工作介绍和招聘岗位详细信息，请查看完整职位和企业介绍。</body></html>",
        media_urls=(media_url,),
        page_video_urls=(media_url,),
    )
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(
        reader,
        "extract_public_subtitles",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "error": "Fresh cookies are required",
        },
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: pytest.fail(
        "browser-rendered page should be parsed without a second static request"
    ))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-extractor-browser-fallback",
        "source": {"url": source_url, "kind": "video"},
        "goal": "Find the job title in the video",
    }))

    assert response.result.value == "partial"
    assert response.coverage.modalities["text"].details["reader"] == "isolated_chromium"
    assert "no transcript or video-frame inspection" in " ".join(response.coverage.unresolved)


def test_read_public_page_routes_browser_captured_video_to_evidence_upgrade(monkeypatch):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    media_url = "https://cdn.example.test/public-video.mp4"
    page = browser.BrowserPage(
        url=source_url,
        title="Douyin video",
        text="视频章节摘要：招聘岗位及章节时间码信息已成功加载。",
        html="<html><title>Douyin video</title><body>视频章节摘要：招聘岗位及章节时间码信息已成功加载。</body></html>",
        media_urls=(media_url,),
    )
    observed = {}
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(reader, "extract_public_subtitles", lambda *_args, **_kwargs: {
        "status": "failed",
        "error": "Fresh cookies are required",
    })
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)

    def attach(request, url, urls, response, **kwargs):
        observed.update({"url": url, "media_urls": urls, "response": response})
        return response

    monkeypatch.setattr(reader, "_attach_browser_video_evidence", attach)
    monkeypatch.setattr(reader, "_read_video_asr", lambda *_args, **_kwargs: pytest.fail(
        "browser-captured video should use the unified video pipeline"
    ))
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (
        source_url,
        "<html><title>Video</title><body>视频章节摘要：招聘岗位</body></html>",
    ))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-video-evidence-pipeline",
        "source": {"url": source_url, "kind": "video"},
    }))

    assert "url" in observed, response.model_dump(mode="json")
    assert observed["url"] == source_url
    assert observed["media_urls"] == [media_url]
    assert observed["response"].result == ReadResult.PARTIAL
    assert response.result == ReadResult.PARTIAL


def test_video_extractor_auth_error_preserves_browser_challenge_type(monkeypatch):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(
        reader,
        "extract_public_subtitles",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "error": "Fresh cookies are required",
        },
    )
    monkeypatch.setattr(
        browser,
        "read_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            browser.BrowserChallengeRequired("The visible page still requires login or security verification")
        ),
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-extractor-challenge",
        "source": {"url": source_url, "kind": "video"},
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "challenge_required"


def test_browser_video_media_candidates_allow_signed_local_use_but_reject_private_urls(monkeypatch):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda url, _cache=None: not url.startswith(
        "http://127.0.0.1"
    ))

    selected, rejected = reader._select_browser_video_url([
        "https://cdn.example.test/video.mp4?token=secret",
        "http://127.0.0.1/private.mp4",
    ], {})

    assert selected == "https://cdn.example.test/video.mp4?token=secret"
    assert rejected == 1


def test_browser_page_video_urls_are_separate_from_incidental_media_requests():
    class Page:
        _envoy_media_urls = [
            "https://cdn.example.test/recommendation.mp4",
        ]

        def evaluate(self, _script):
            return ["https://cdn.example.test/current-post.mp4"]

    page = Page()

    assert browser._page_video_urls(page) == [
        "https://cdn.example.test/current-post.mp4",
    ]
    assert browser._captured_media_urls(page) == [
        "https://cdn.example.test/recommendation.mp4",
    ]


def test_browser_video_media_candidates_preserve_page_video_priority(monkeypatch):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    page_video = "https://v95-web.example/video.mp4?token=public"
    incidental_clip = "https://a-effect-cdn.example/animation.mp4"

    selected, rejected = reader._select_browser_video_url(
        [page_video, incidental_clip],
        {},
    )

    assert selected == page_video
    assert rejected == 0


def test_browser_video_evidence_does_not_process_unbound_captured_media(monkeypatch):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", lambda *_args, **_kwargs: pytest.fail(
        "an unrelated captured recommendation must not be downloaded as the current post"
    ))

    response = reader._attach_browser_video_evidence(
        ReadRequest.model_validate({
            "request_id": "browser-video-unbound",
            "source": {"url": source_url, "kind": "video"},
        }),
        source_url,
        ["https://cdn.example.test/recommendation.mp4"],
        ReadResponse(
            request_id="browser-video-unbound",
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        ),
        bound_media_urls=[],
    )

    video = response.coverage.modalities["video"]
    assert response.result == ReadResult.PARTIAL
    assert video.extraction == "partial"
    assert video.details["page_player_bound"] is False
    unresolved = " ".join(video.unresolved)
    assert "player bound no downloadable source" in unresolved
    assert "cookies" in unresolved


def test_browser_video_evidence_groups_mirror_sources_of_one_file(monkeypatch, tmp_path):
    mirror_a = "https://v95-web-sz.douyinvod.com/aaa/video/tos/cn/clip.mp4"
    mirror_b = "https://v11-weba.douyinvod.com/bbb/video/tos/cn/clip.mp4"
    play_endpoint = "https://www.douyin.com/aweme/v1/play/?aid=6383&biz_sign=zz"
    another_clip = "https://v11-weba.douyinvod.com/bbb/video/tos/cn/other.mp4"
    identities = {
        mirror_a: ("clip-digest", "video/mp4", 19_604_211),
        mirror_b: ("clip-digest", "video/mp4", 19_604_211),
        play_endpoint: ("clip-digest", "video/mp4", 19_604_211),
        another_clip: ("other-digest", "video/mp4", 5_000_000),
    }
    source_url = "https://www.douyin.com/video/7682646844794539298"
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")
    downloaded: list[str] = []

    def fake_download(url, *_args, **_kwargs):
        downloaded.append(url)
        return {"path": str(video_path), "duration": 72, "downloaded_bytes": 5, "elapsed_ms": 1}

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_probe_page_media", lambda url, _cache: identities.get(url))
    monkeypatch.setattr(reader, "download_video_for_frames", fake_download)
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_args, **_kwargs: {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        "language": "en",
        "model": "base",
        "status": "ok",
    })
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader._attach_browser_video_evidence(
        ReadRequest.model_validate({
            "request_id": "browser-video-mirrors",
            "source": {"url": source_url, "kind": "video"},
        }),
        source_url,
        [another_clip],
        ReadResponse(
            request_id="browser-video-mirrors",
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        ),
        bound_media_urls=[mirror_a, mirror_b, play_endpoint],
    )

    assert downloaded == [mirror_a]
    assert response.coverage.modalities["video"].extraction == "partial"
    assert "no unique page video URL" not in " ".join(
        response.coverage.modalities["video"].unresolved
    )


def test_browser_video_evidence_reads_the_page_source_past_mse_and_ui_assets(monkeypatch, tmp_path):
    """Douyin exposes a blob: MSE source beside the signed <source> URL.

    Counting the blob: entry as a competing candidate, or letting a captured
    UI asset outvote the page player, hid the only URL that can be fetched.
    """

    source_url = "https://www.douyin.com/video/7682646844794539298"
    signed_url = "https://v11-weba.douyinvod.com/aaa/bbb/video/tos/cn/obj/?token=secret"
    request = ReadRequest.model_validate({
        "request_id": "browser-video-mse-source",
        "source": {"url": source_url, "kind": "video"},
    })
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")
    downloaded: list[str] = []

    def fake_download(url, *_args, **_kwargs):
        downloaded.append(url)
        return {"path": str(video_path), "duration": 72, "downloaded_bytes": 5, "elapsed_ms": 1}

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", fake_download)
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_args, **_kwargs: {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        "language": "en",
        "model": "base",
        "status": "ok",
    })
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader._attach_browser_video_evidence(
        request,
        source_url,
        ["https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/uuu_265.mp4"],
        ReadResponse(
            request_id=request.request_id,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        ),
        bound_media_urls=[
            "blob:https://www.douyin.com/12654a98-5ab8-4441-a59d-9e0a2d329c69",
            signed_url,
        ],
    )

    assert downloaded == [signed_url]
    video = response.coverage.modalities["video"]
    assert video.details["transcript_source"] == "asr"
    assert "rejected_media_candidates" not in video.details
    serialized = response.model_dump_json()
    assert signed_url not in serialized


def test_browser_video_evidence_respects_include_visual_false(monkeypatch, tmp_path):
    request = ReadRequest.model_validate({
        "request_id": "browser-video-text-only",
        "source": {
            "url": "https://www.douyin.com/video/7698386448961683813",
            "kind": "video",
        },
        "host_capabilities": {"image_input": "local_file", "video_input": "frames"},
    })
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", lambda *_args, **_kwargs: {
        "path": str(video_path),
        "duration": 10,
        "downloaded_bytes": 5,
        "elapsed_ms": 1,
    })
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": False,
        "asr_model_available": False,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "extract_video_frames", lambda *_args, **_kwargs: pytest.fail(
        "declaring frame capability must not override scope.include_visual=false"
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader._attach_browser_video_evidence(
        request,
        request.source.url,
        ["https://cdn.example.test/video.mp4"],
        ReadResponse(
            request_id=request.request_id,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        ),
        duration_seconds=10,
        bound_media_urls=["https://cdn.example.test/video.mp4"],
    )

    assert response.result == ReadResult.PARTIAL
    assert not any(block.kind == MaterialKind.VIDEO_FRAME for block in response.blocks)


def test_browser_short_link_uses_final_video_url_for_video_evidence(monkeypatch):
    source_url = "https://v.douyin.com/short-share/"
    final_url = "https://www.douyin.com/video/7698386448961683813"
    media_url = "https://cdn.example.test/current-post.mp4"
    page = browser.BrowserPage(
        url=final_url,
        title="Douyin video",
        text="视频章节摘要：招聘岗位及章节时间码信息已成功加载。",
        html=(
            "<html><title>Douyin video</title><body>"
            "视频章节摘要：招聘岗位及章节时间码信息已成功加载。"
            "</body></html>"
        ),
        media_urls=(media_url,),
        page_video_urls=(media_url,),
    )
    observed = {}

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (
        final_url,
        "<html><title>Douyin video</title><body>"
        "视频章节摘要：招聘岗位及章节时间码信息已成功加载。"
        "</body></html>",
    ))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)

    def attach(request, url, urls, response, **kwargs):
        observed.update({
            "url": url,
            "urls": urls,
            "bound": kwargs.get("bound_media_urls"),
        })
        return response

    monkeypatch.setattr(reader, "_attach_browser_video_evidence", attach)
    monkeypatch.setattr(reader, "_read_with_isolated_browser", lambda *_args, **_kwargs: (page, None))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-short-link",
        "source": {"url": source_url},
    }))

    assert response.result == ReadResult.PARTIAL
    assert observed["url"] == final_url
    assert observed["urls"] == [media_url]
    assert observed["bound"] == [media_url]


def test_browser_video_evidence_merges_timestamped_asr_and_sparse_frames(
    monkeypatch,
    tmp_path,
):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    media_url = "https://cdn.example.test/video.mp4"
    request = ReadRequest.model_validate({
        "request_id": "browser-video-evidence",
        "session_id": "video-session",
        "source": {"url": source_url, "kind": "video"},
        "goal": "Extract all video evidence",
        "ocr_profile": "balanced",
        "scope": {"include_visual": True},
        "host_capabilities": {"image_input": "local_file", "video_input": "frames"},
        "budget": {
            "max_visual_inputs": 2,
            "max_visual_pixels_per_input": 400,
            "max_total_visual_pixels": 800,
            "max_download_bytes": 4096,
        },
    })
    material_id = "mat-" + "a" * 24
    page_block = MaterialBlock(
        id="blk-body-0001",
        kind=MaterialKind.PARAGRAPH,
        content=TextContent(type="text", text="00:10 第二章：深圳岗位介绍"),
        origin=MaterialOrigin.SOURCE_TEXT,
    )
    page_response = ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
        blocks=[page_block],
    )
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")
    calls = []

    def download(url, output, **kwargs):
        calls.append(("download", url, kwargs["max_download_bytes"]))
        return {
            "path": str(video_path),
            "duration": 30,
            "downloaded_bytes": 5,
            "elapsed_ms": 10,
        }

    def transcribe(path, **kwargs):
        calls.append(("asr", path))
        return {
            "status": "success",
            "language": "zh",
            "model": "base",
            "duration_seconds": 30,
            "elapsed_ms": 20,
            "segments": [
                {"start": 8.0, "end": 11.0, "text": "岗位地点在深圳。"},
            ],
        }

    def extract(path, output, timestamps, **kwargs):
        calls.append(("frames", list(timestamps)))
        Path(output).mkdir(parents=True, exist_ok=True)
        frame_path = Path(output) / "frame.jpg"
        frame_path.write_bytes(b"frame")
        return [{
            "path": str(frame_path),
            "timestamp": timestamps[0],
            "width_px": 20,
            "height_px": 20,
            "pixels": 400,
            "size_bytes": 5,
            "sha256": "b" * 64,
        }]

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", download)
    monkeypatch.setattr(reader, "probe_media_duration", lambda *_args, **_kwargs: 30)
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "asr_model_cache_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(reader, "transcribe_audio_file", transcribe)
    monkeypatch.setattr(reader, "extract_video_frames", extract)
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "tesseract",
        "engine_version": "tesseract 5",
        "regions": [{
            "text": "深圳岗位",
            "bbox": [0.1, 0.2, 0.8, 0.4],
            "confidence": 0.9,
        }],
    })
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader._attach_browser_video_evidence(
        request,
        source_url,
        [media_url],
        page_response,
        duration_seconds=30,
        bound_media_urls=[media_url],
    )

    assert calls[0][0] == "download"
    assert [call[0] for call in calls] == ["download", "asr", "frames"]
    transcript = next(block for block in response.blocks if block.origin == MaterialOrigin.ASR)
    assert transcript.locator.type == "video_time"
    assert transcript.locator.start_seconds == 8.0
    frame = next(block for block in response.blocks if block.kind == MaterialKind.VIDEO_FRAME)
    assert frame.locator.type == "video_time"
    assert frame.locator.start_seconds == 7.5
    assert response.coverage.modalities["video"].details["transcript_source"] == "asr"
    ocr = next(block for block in response.blocks if block.kind == MaterialKind.OCR)
    assert ocr.content.text == "深圳岗位"
    assert ocr.locator.resource_id == frame.content.resource_id
    assert ocr.locator.type == "image_region"
    assert response.coverage.modalities["ocr"].details["timestamp_source"] == "parent_video_frame_locator"
    assert response.coverage.modalities["video"].details["frame_timestamps"] == [7.5]
    stored = reader._load_matching_material(
        response.material_id,
        response.source_revision,
        request.session_id,
    )
    assert stored["source_kind"] == "video"
    assert stored["transcript_source"] == "asr"
    assert "岗位地点在深圳" in stored["text"]


def test_browser_video_evidence_marks_unavailable_asr_and_frame_capability(
    monkeypatch,
    tmp_path,
):
    source_url = "https://www.douyin.com/video/7698386448961683813"
    media_url = "https://cdn.example.test/video.mp4"
    request = ReadRequest.model_validate({
        "request_id": "browser-video-no-capabilities",
        "source": {"url": source_url, "kind": "video"},
    })
    page_response = ReadResponse(
        request_id=request.request_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", lambda *_args, **_kwargs: pytest.fail(
        "media nothing can consume must not be downloaded"
    ))
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": False,
        "asr_model_available": False,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "extract_video_frames", lambda *_args, **_kwargs: pytest.fail(
        "frames must not be delivered when the host has not declared image capability"
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader._attach_browser_video_evidence(
        request,
        source_url,
        [media_url],
        page_response,
        duration_seconds=30,
        bound_media_urls=[media_url],
    )

    details = response.coverage.modalities["video"].details
    assert response.result == ReadResult.PARTIAL
    assert response.coverage.modalities["video"].extraction == "partial"
    assert "ASR is unavailable" in " ".join(response.coverage.modalities["video"].unresolved)
    assert details["media_fetched"] is False
    assert details["media_fetch_skipped"] == "local_asr_unavailable"
    assert details["transcript_source"] == "unavailable"



def test_browser_video_evidence_uses_signed_url_locally_without_exposing_it(
    monkeypatch,
    tmp_path,
):
    signed_media_url = (
        "https://cdn.example.test/video.mp4?"
        "sign=short-lived&biz_sign=playback&uifid=session-proof"
    )
    request = ReadRequest.model_validate({
        "request_id": "browser-video-signed-source",
        "source": {
            "url": "https://www.douyin.com/video/7698386448961683813",
            "kind": "video",
        },
    })
    response = ReadResponse(
        request_id=request.request_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
        blocks=[MaterialBlock(
            id="page-summary",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text="视频章节摘要：招聘岗位"),
            origin=MaterialOrigin.SOURCE_TEXT,
        )],
        coverage={
            "unresolved": [
                "Embedded video resources were discovered but not processed",
                "The isolated browser read the video page only; no transcript or video-frame inspection was completed",
            ],
            "modalities": {
                "video": {
                    "unresolved": [
                        "Browser page was read, but video transcript and frames have not been extracted",
                    ],
                },
            },
        },
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    downloaded = {}

    def download(url, output, **kwargs):
        downloaded["url"] = url
        video = Path(output) / "video.mp4"
        video.write_bytes(b"video")
        return {
            "path": str(video),
            "duration": 10,
            "downloaded_bytes": 5,
            "elapsed_ms": 1,
        }

    monkeypatch.setattr(reader, "download_video_for_frames", download)
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_args, **_kwargs: {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        "language": "en",
        "model": "base",
        "status": "ok",
    })
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    result = reader._attach_browser_video_evidence(
        request,
        request.source.url,
        [signed_media_url],
        response,
        duration_seconds=None,
        bound_media_urls=[signed_media_url],
    )

    assert result.result == ReadResult.PARTIAL
    assert downloaded["url"] == signed_media_url
    serialized = result.model_dump_json()
    for secret in ("short-lived", "playback", "session-proof"):
        assert secret not in serialized
    assert not any(
        "video page only" in item or "video resources were discovered but not processed" in item
        for item in result.coverage.unresolved
    )
    assert not any(
        "have not been extracted" in item
        for item in result.coverage.modalities["video"].unresolved
    )
    assert result.coverage.modalities["video"].details["browser_media_url"] == (
        "https://cdn.example.test"
    )
    assert any(block.id == "page-summary" for block in result.blocks)


def test_browser_network_guard_blocks_private_redirect_targets(monkeypatch):
    class Request:
        url = "http://127.0.0.1/private"

    class Route:
        request = Request()
        continued = False
        aborted = None

        def continue_(self):
            self.continued = True

        def abort(self, reason):
            self.aborted = reason

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: False)
    route = Route()

    browser._guard_route(route, {})

    assert route.continued is False
    assert route.aborted == "blockedbyclient"


def test_browser_profile_is_created_with_user_only_permissions(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows profile permissions inherit from the user's home directory")
    profile = tmp_path / "private-profile"
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))

    assert browser.browser_profile_dir() == profile
    assert profile.is_dir()
    assert profile.stat().st_mode & 0o777 == 0o700


def test_capability_catalog_explains_headless_and_interactive_browser_modes():
    catalog = capability_catalog()
    browser_capability = next(
        item
        for item in catalog["inputs"]["conditional"]
        if item["id"] == "dynamic_public_pages"
    )

    assert "read headlessly" in browser_capability["behavior"]
    assert "show the Envoy-owned browser only" in browser_capability["behavior"]
    assert catalog["limits"]["external_browser_profile_access"] is False


def test_browser_runtime_status_reports_missing_playwright(monkeypatch):
    real_import = __import__

    def import_without_playwright(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_playwright)

    status = browser.browser_runtime_status()

    assert status["installed"] is False
    assert status["ready"] is False
    assert status["browser"] == "chromium"


def test_browser_runtime_status_reports_installed_chromium(monkeypatch, tmp_path):
    executable = tmp_path / "chromium"
    executable.write_text("browser")

    class FakeSyncPlaywright:
        def __enter__(self):
            return types.SimpleNamespace(
                chromium=types.SimpleNamespace(executable_path=str(executable))
            )

        def __exit__(self, *_args):
            return None

    playwright_package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakeSyncPlaywright
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    status = browser.browser_runtime_status()

    assert status["installed"] is True
    assert status["ready"] is True


def test_cli_doctor_reports_browser_runtime(monkeypatch, capsys):
    from envoy_cli import cli

    monkeypatch.setattr(
        cli.browser_runtime,
        "browser_runtime_status",
        lambda: {"installed": True, "browser": "chromium", "ready": False},
    )

    assert cli.main(["doctor", "--json"]) == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["runtime"]["browser"]["ready"] is False


def test_egress_block_pages_count_as_network_evidence():
    assert reader._challenge_suggests_egress_issue(
        "安全限制",
        "IP存在风险，请切换可靠网络环境后重试\n300012",
    )
    assert reader._challenge_suggests_egress_issue("访问过于频繁", "")
    assert not reader._challenge_suggests_egress_issue(
        "Verify you are human",
        "Please complete the captcha to continue",
    )


TEASER_CLIP = "https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/uuu_265.mp4"
SIGNED_CLIP = "https://v95-web-sz.douyinvod.com/aaa/video/tos/cn/clip.mp4"
MIRROR_CLIP = "https://v11-weba.douyinvod.com/bbb/video/tos/cn/clip.mp4"


def test_await_page_video_url_keeps_waiting_past_a_cover_teaser():
    """A bound teaser is proof the player is still mounting, not a source."""

    class Page:
        def __init__(self, screens):
            self.screens = list(screens)

        @property
        def frames(self):
            return []

        def evaluate(self, script, *_args):
            if "overlay" in script:
                return 0
            if "currentSrc" in script:
                return self.screens.pop(0) if self.screens else []
            return True

        def wait_for_timeout(self, _milliseconds):
            return None

    assert browser._await_page_video_url(Page([[TEASER_CLIP], [SIGNED_CLIP]]), timeout_seconds=2) == [
        SIGNED_CLIP
    ]


def test_await_page_video_url_returns_nothing_when_only_a_teaser_ever_binds():
    class Page:
        @property
        def frames(self):
            return []

        def evaluate(self, script, *_args):
            if "overlay" in script:
                return 0
            if "currentSrc" in script:
                return [TEASER_CLIP]
            return True

        def wait_for_timeout(self, _milliseconds):
            return None

    assert browser._await_page_video_url(Page(), timeout_seconds=0) == []


def test_page_media_preview_hosts_are_recognized():
    assert browser._is_preview_media_url(TEASER_CLIP) is True
    assert browser._is_preview_media_url("https://lf-douyin-pc-web.douyinstatic.com/obj/x.mp4") is True
    assert browser._is_preview_media_url(SIGNED_CLIP) is False
    assert browser._is_preview_media_url("") is False


def test_page_media_ranking_prefers_the_full_title_over_a_cover_teaser(monkeypatch):
    identities = {
        TEASER_CLIP: ("teaser-digest", "video/mp4", 199_802),
        SIGNED_CLIP: ("clip-digest", "video/mp4", 19_604_211),
        MIRROR_CLIP: ("clip-digest", "video/mp4", 19_604_211),
    }
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_probe_page_media", lambda url, _cache: identities.get(url))

    ranked = reader._rank_page_media_urls([TEASER_CLIP, SIGNED_CLIP, MIRROR_CLIP], {})

    assert ranked == [SIGNED_CLIP, MIRROR_CLIP, TEASER_CLIP]


def test_page_media_total_bytes_prefers_the_range_total():
    assert reader._page_media_total_bytes("bytes 0-65535/19604211", "65536") == 19_604_211
    assert reader._page_media_total_bytes(None, "19604211") == 19_604_211
    assert reader._page_media_total_bytes("bytes */0", "0") == 0
    assert reader._page_media_total_bytes(None, None) == 0


def test_browser_video_evidence_refuses_a_cover_preview_as_the_post(monkeypatch):
    """The teaser must never be published as the requested title.

    Publishing it is worse than reporting a login wall: the download succeeds,
    the transcript comes back empty, and the answer looks like a real read.
    """

    source_url = "https://www.douyin.com/video/7682646844794539298"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "download_video_for_frames", lambda *_args, **_kwargs: pytest.fail(
        "a cover preview must never be published as the requested title"
    ))

    request = ReadRequest.model_validate({
        "request_id": "browser-video-teaser-only",
        "source": {"url": source_url, "kind": "video"},
    })
    response = reader._attach_browser_video_evidence(
        request,
        source_url,
        [TEASER_CLIP],
        ReadResponse(
            request_id=request.request_id,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        ),
        bound_media_urls=[TEASER_CLIP],
    )

    video = response.coverage.modalities["video"]
    assert video.details["browser_media_preview_only"] is True
    assert video.details["page_player_bound"] is False
    action = video.details["needs_user_action"]
    assert action["kind"] == "login_or_verification"
    assert action["host"] == "www.douyin.com"
    assert [option["action"] for option in action["options"]] == [
        "import_cookies",
        "human_sign_in",
    ]
    assert action["requires_human"] is True
    assert all(option["requires_human"] is True for option in action["options"])
    assert all("command" not in option for option in action["options"])
    assert "player bound no downloadable source" in " ".join(video.unresolved)


def test_blocked_challenge_carries_the_session_choices():
    from envoy_core.schemas.read_v1 import ErrorCode

    request = ReadRequest.model_validate({
        "request_id": "blocked-challenge",
        "source": {"url": "https://www.douyin.com/video/1", "kind": "video"},
    })
    blocked = reader._blocked(
        request,
        ErrorCode.CHALLENGE_REQUIRED,
        "The page blocks isolated headless reading",
        action_required=True,
    )
    details = blocked.errors[0].details
    action = details["needs_user_action"]
    assert action["host"] == "www.douyin.com"
    assert action["requires_human"] is True
    assert all(option["requires_human"] is True for option in action["options"])
    assert all("command" not in option for option in action["options"])
    serialized = json.dumps(details, ensure_ascii=False)
    assert "ENVOY_VISIBLE_BROWSER=" not in serialized
    assert "--request-json" not in serialized

    plain = reader._blocked(request, ErrorCode.BUDGET_EXCEEDED, "too large")
    assert "needs_user_action" not in plain.errors[0].details


def test_a_fresh_cookie_demand_asks_for_a_scoped_import():
    """A site may want cookies without wanting an account; say so, and scope it."""

    action = reader._login_user_action(
        "https://www.douyin.com/video/7688250071749610794",
        kind="cookies_required",
    )

    assert action["kind"] == "cookies_required"
    assert action["host"] == "www.douyin.com"
    import_option = action["options"][0]
    assert import_option["requires_human"] is True
    assert "command" not in import_option
    assert "not necessarily what it wants" in action["question"]
    assert [option["action"] for option in action["options"]] == [
        "import_cookies",
        "human_sign_in",
    ]


def test_a_silent_video_keeps_its_source_and_names_the_gap():
    """Reaching a music clip must not become an empty answer; it is a partial read."""

    request = ReadRequest.model_validate({
        "request_id": "no-speech",
        "source": {"url": "https://www.bilibili.com/video/BV1xx411c7mD", "kind": "video"},
    })
    response = reader._no_speech_partial(
        request,
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "Local ASR found no speech segments",
        title="沉默的风景",
        duration="183.4",
    )

    assert response.result == ReadResult.PARTIAL
    assert response.execution_state == ExecutionState.COMPLETED
    assert response.errors == []
    assert response.blocks[0].content.text == "沉默的风景"
    video = response.coverage.modalities["video"]
    assert video.extraction == "partial"
    assert video.details["no_speech"] is True
    assert video.details["duration_seconds"] == 183.4
    assert video.details["page_player_bound"] is True
    unresolved = " ".join(video.unresolved)
    assert "no detectable speech" in unresolved
    assert "183s" in unresolved


def test_a_silent_video_without_a_known_duration_still_says_what_is_missing():
    request = ReadRequest.model_validate({
        "request_id": "no-speech-noduration",
        "source": {"url": "https://www.douyin.com/video/1", "kind": "video"},
    })
    response = reader._no_speech_partial(
        request,
        "https://www.douyin.com/video/1",
        "Local ASR found no speech segments",
        duration="unknown",
    )

    assert response.result == ReadResult.PARTIAL
    assert response.blocks[0].content.text == "Video source"
    assert "duration_seconds" not in response.coverage.modalities["video"].details


def test_a_music_track_is_flagged_rather_than_quoted_as_speech():
    """A stray whisper line must not pass as the transcript of a music video."""

    confidence, seconds, ratio = reader._transcript_confidence(
        [{"start": 115.0, "end": 117.5}], 212.393
    )
    assert confidence == "low"
    assert seconds == 2.5
    assert ratio < 0.02

    confidence, _, _ = reader._transcript_confidence(
        [{"start": 1.0, "end": 40.0}, {"start": 40.0, "end": 80.0}], 212.393
    )
    assert confidence == "normal"

    # A short clip that is genuinely mostly speech must not be penalised.
    confidence, _, _ = reader._transcript_confidence(
        [{"start": 0.0, "end": 6.0}], 8.0
    )
    assert confidence == "normal"
