"""The in-chat sign-in handoff: screenshot, state, and the tool that carries it.

These tests never open a browser. They cover the parts a model depends on:
which page is worth photographing, what a stale action must not report, and
that the MCP tool exists in the toolset every host loads.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

from envoy_cli import auth_session, authflow, cli, mcp


@pytest.fixture(autouse=True)
def _credential_handoff_on(monkeypatch):
    # These tests pin the enabled mechanics; the shipped default (off) has its
    # own coverage in tests/cli/test_credential_handoff.py.
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")


@pytest.fixture(autouse=True)
def _isolated_auth_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_AUTH_DIR", str(tmp_path / "auth"))
    yield tmp_path

def test_login_url_maps_article_hosts_to_their_sign_in_entry():
    assert auth_session._login_url("https://zhuanlan.zhihu.com/p/123") == "https://www.zhihu.com/signin"
    assert auth_session._login_url("https://www.xiaohongshu.com/explore/x") == (
        "https://www.xiaohongshu.com/login"
    )
    assert auth_session._login_url("https://example.com/a") == "https://example.com/a"

def test_looks_blocked_only_reads_a_reached_page():
    assert auth_session._looks_blocked('{"error":{"code":40362}} 异常')
    assert auth_session._looks_blocked("请完成安全验证")
    assert not auth_session._looks_blocked("")
    assert not auth_session._looks_blocked("Late in the afternoon the harbour emptied.")

def test_a_page_that_never_showed_a_gate_cannot_report_a_sign_in():
    # The link may simply have been readable: claiming a sign-in would send the
    # caller into a retry loop for a page that never needed one.
    assert not auth_session._looks_like_gate("A normal article", "Body text here.", "https://example.com/a")
    assert auth_session._looks_like_gate("微博", "登录/注册", "https://weibo.com/u/1")


def test_a_sign_in_page_url_counts_as_a_gate_even_before_it_renders():
    assert auth_session._looks_like_gate("", "", "https://passport.example.com/sso/signin")
    assert auth_session._looks_like_gate("", "", "https://example.com/login?next=/a")
    assert not auth_session._looks_like_gate("", "", "https://example.com/article/1")


def test_a_rate_limited_page_is_a_gate_not_a_signed_in_page():
    assert auth_session._looks_like_gate("", "Too many requests. Try again later.", "https://example.com/a")


def test_status_of_a_dead_worker_is_not_running():
    action_dir = auth_session._session_dir("auth-dead")
    action_dir.mkdir(parents=True)
    (action_dir / "session.json").write_text(
        json.dumps({"status": "waiting", "pid": 999999, "screenshot": "/nope/gone.png"}),
        encoding="utf-8",
    )
    payload = auth_session.status("auth-dead")
    assert payload["status"] == "not_running"
    assert payload["browser_alive"] is False
    assert payload["screen_path"] == ""

def test_start_reports_the_first_screenshot_as_a_picture(tmp_path, monkeypatch):
    shot_holder: dict[str, Path] = {}
    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        action_id = argv[argv.index("--action-id") + 1]
        work = auth_session._session_dir(action_id)
        shot = work / "sign-in.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        shot_holder["shot"] = shot
        (work / "session.json").write_text(
            json.dumps({"status": "waiting", "pid": os.getpid(), "screenshot": str(shot)}),
            encoding="utf-8",
        )

        class _Proc:
            pid = 4242

        return _Proc()

    monkeypatch.setattr(auth_session.subprocess, "Popen", fake_popen)
    payload = auth_session.start("https://zhuanlan.zhihu.com/p/123", timeout_seconds=120)
    assert calls, "a background worker must be spawned"
    assert calls[0]["kwargs"]["start_new_session"] is True
    assert payload["screen_path"] == str(shot_holder["shot"])
    assert payload["screen_mime"] == "image/png"
    assert payload["host"] == "zhuanlan.zhihu.com"
    assert Path(payload["log_path"]).parent == auth_session._session_dir(payload["action_id"])

def test_auth_payload_open_names_the_picture_and_the_next_step(monkeypatch):
    def fake_start(url, **kwargs):
        return {
            "action_id": "auth-abc",
            "host": "zhuanlan.zhihu.com",
            "source_url": url,
            "status": "waiting",
            "screen_path": "/tmp/sign-in.png",
            "screen_mime": "image/png",
            "browser": "background",
        }

    monkeypatch.setattr(mcp.auth_session, "start", fake_start)
    payload = mcp._auth_payload(
        {"action": "open", "url": "https://zhuanlan.zhihu.com/p/123"}
    )
    assert payload["result"] == "ready"
    assert payload["screen_path"] == "/tmp/sign-in.png"
    assert "envoy_auth" in payload["instruction"]
    assert "envoy_read" in payload["instruction"]

def test_auth_payload_status_says_which_call_comes_next(monkeypatch):
    monkeypatch.setattr(
        mcp.auth_session,
        "status",
        lambda action_id: {"action_id": action_id, "status": "signed_in"},
    )
    payload = mcp._auth_payload({"action": "status", "action_id": "auth-abc"})
    assert payload["result"] == "ready"
    assert "envoy_read" in payload["instruction"]

    monkeypatch.setattr(
        mcp.auth_session,
        "status",
        lambda action_id: {"action_id": action_id, "status": "waiting"},
    )
    waiting = mcp._auth_payload({"action": "status", "action_id": "auth-abc"})
    assert waiting["result"] == "waiting"

def test_auth_payload_rejects_incomplete_arguments():
    with pytest.raises(ValueError):
        mcp._auth_payload({"action": "open"})
    with pytest.raises(ValueError):
        mcp._auth_payload({"action": "status"})
    with pytest.raises(ValueError):
        mcp._auth_payload({"action": "dance"})

def test_envoy_auth_is_in_the_core_toolset_and_asks_nothing_new_of_a_host():
    names = [tool["name"] for tool in mcp.TOOLS]
    assert names.count("envoy_auth") == 1
    assert "envoy_auth" in mcp.TOOLSETS["core"]
    schema = next(tool["inputSchema"] for tool in mcp.TOOLS if tool["name"] == "envoy_auth")
    assert schema["properties"]["action"]["enum"] == [
        "open",
        "status",
        "cancel",
        "import",
    ]


def test_auth_payload_hands_the_model_a_line_that_renders_the_picture(monkeypatch, tmp_path):
    screen = tmp_path / "qr.png"
    screen.write_bytes(b"png")
    line = f"![扫码登录或验证](<{screen}>)"
    monkeypatch.setattr(
        mcp.auth_session,
        "start",
        lambda url, **kwargs: {
            "action_id": "auth-abc",
            "host": "xiaohongshu.com",
            "source_url": url,
            "status": "waiting",
            "screen_path": str(screen),
            "screen_mime": "image/png",
        },
    )
    payload = mcp._auth_payload({"action": "open", "url": "https://www.xiaohongshu.com/explore/1"})
    assert payload["picture_markdown"] == line
    assert "picture_markdown" in payload["instruction"]

    monkeypatch.setattr(
        mcp.auth_session,
        "status",
        lambda action_id: {"action_id": action_id, "status": "waiting", "screen_path": str(screen)},
    )
    waiting = mcp._auth_payload({"action": "status", "action_id": "auth-abc"})
    assert waiting["picture_markdown"] == line

    monkeypatch.setattr(
        mcp.auth_session,
        "status",
        lambda action_id: {"action_id": action_id, "status": "signed_in", "screen_path": str(screen)},
    )
    signed_in = mcp._auth_payload({"action": "status", "action_id": "auth-abc"})
    assert "picture_markdown" not in signed_in


def test_a_relative_screenshot_path_never_becomes_a_chat_link():
    assert mcp._picture_markdown("qr.png") == ""
    assert mcp._picture_markdown("") == ""
    assert mcp._picture_markdown(None) == ""


def test_a_network_risk_control_page_is_not_a_signed_in_session():
    blocked = "安全限制 当前网络存在风险，请切换可靠网络后重试 300012"
    assert auth_session._risk_controlled("", blocked, "https://www.xiaohongshu.com/website-login/error") is True
    assert auth_session._risk_controlled("", "扫码登录 请使用小红书App扫描二维码", "/login") is False
    assert auth_session._risk_controlled("", "", "") is False


def test_auth_payload_open_stops_at_the_network_verdict(monkeypatch):
    monkeypatch.setattr(
        mcp.auth_session,
        "start",
        lambda url, **kwargs: {
            "action_id": "auth-abc",
            "host": "www.xiaohongshu.com",
            "source_url": url,
            "status": "failed",
            "screen_path": "/tmp/envoy-auth/blocked.png",
            "egress_blocked": True,
            "reason": "the site's network risk control refused the sign-in page itself",
        },
    )
    payload = mcp._auth_payload({"action": "open", "url": "https://www.xiaohongshu.com/explore/1"})
    assert payload["result"] == "blocked"
    assert "picture_markdown" not in payload
    assert "different network egress" in payload["instruction"]
    assert "scan" in payload["instruction"]

def _fake_qr_browser(monkeypatch, tmp_path, *, cookies):
    """A Playwright stand-in: one page that stays on the site's gate."""

    added: list[list[dict]] = []
    launched: list[dict] = []

    class _Page:
        url = "https://www.zhihu.com/signin"

        def goto(self, *a, **k):
            return None

        def title(self):
            return "知乎 - 登录"

        def inner_text(self, *a, **k):
            return "扫码登录 请使用知乎App扫描二维码"

        def screenshot(self, path=None, **k):
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")

    class _Context:
        pages = [_Page()]

        def cookies(self):
            return [dict(cookie) for cookie in cookies]

        def add_cookies(self, keep):
            added.append([dict(item) for item in keep])

        def close(self):
            return None

    class _Chromium:
        def launch_persistent_context(self, profile, **kwargs):
            launched.append({"profile": profile, **kwargs})
            return _Context()

    class _Playwright:
        chromium = _Chromium()

    class _Manager:
        def __enter__(self):
            return _Playwright()

        def __exit__(self, *exc):
            return False

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _Manager()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(tmp_path / "browser-profile"))
    return launched, added


def test_the_sign_in_browser_keeps_its_session_cookie_for_later_reads(tmp_path, monkeypatch):
    # The QR path used to close its context and leave a cookie with no expiry
    # behind, so the profile let the next headless read look anonymous.
    launched, added = _fake_qr_browser(
        monkeypatch,
        tmp_path,
        cookies=[
            {
                "name": "z_c0",
                "value": "tok",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
    )
    work = auth_session._session_dir("auth-qr")
    work.mkdir(parents=True)
    assert auth_session._worker("https://www.zhihu.com/signin", "auth-qr", work, 30, False) == 0
    assert launched[0]["args"] == [
        "--disable-dev-shm-usage",
        "--no-proxy-server",
        "--new-window",
    ]
    assert added and added[0][0]["name"] == "z_c0"
    assert added[0][0]["expires"] > time.time()
    assert auth_session.read_state("auth-qr")["status"] == "signed_in"
    assert authflow.profile_session_known("zhihu.com") is True


def test_a_profile_held_by_another_browser_fails_without_claiming_a_sign_in(tmp_path, monkeypatch):
    # Two Chromium instances on one profile corrupt each other, so the second
    # sign-in must stop at the lease instead of launching anyway.
    work = auth_session._session_dir("auth-busy")
    work.mkdir(parents=True)
    busied: list[str] = []

    class _Busy:
        def __enter__(self):
            raise TimeoutError("Another Envoy browser is using this profile.")

        def __exit__(self, *exc):
            return False

    def fake_lease(seconds):
        busied.append("lease")
        return _Busy()

    monkeypatch.setattr(auth_session.auth_window, "_lease", fake_lease)
    code = auth_session._worker("https://www.zhihu.com/signin", "auth-busy", work, 30, False)
    assert busied == ["lease"]
    assert code == 2
    state = auth_session.read_state("auth-busy")
    assert state["status"] == "failed"
    assert state["reason"].startswith("profile_busy:")
    assert auth_session.read_state("auth-busy").get("screenshot") in (None, "")


def test_auth_shot_tells_the_person_both_ways_past_the_wall(monkeypatch, capsys):
    args = types.SimpleNamespace(
        url="https://zhuanlan.zhihu.com/p/1",
        action_id=None,
        timeout=300,
        visible=False,
        json=False,
    )
    monkeypatch.setattr(
        auth_session,
        "start",
        lambda url, **kwargs: {
            "action_id": "auth-abc",
            "host": "zhuanlan.zhihu.com",
            "source_url": url,
            "status": "waiting",
            "screen_path": "/tmp/sign-in.png",
            "screen_mime": "image/png",
            "timeout_seconds": 300,
        },
    )
    assert cli._run_auth_shot(args) == 0
    printed = capsys.readouterr().out
    assert "QR code" in printed and "SMS code" in printed

    monkeypatch.setattr(
        auth_session,
        "start",
        lambda url, **kwargs: {
            "action_id": "auth-abc",
            "status": "failed",
            "reason": "profile_busy: another Envoy browser is using this profile",
            "timeout_seconds": 300,
        },
    )
    assert cli._run_auth_shot(args) == 2
    assert "profile_busy" in capsys.readouterr().err


def test_auth_payload_open_offers_the_phone_form_as_well_as_the_code(monkeypatch):
    monkeypatch.setattr(
        auth_session,
        "start",
        lambda url, **kwargs: {
            "action_id": "auth-abc",
            "host": "www.xiaohongshu.com",
            "source_url": url,
            "status": "waiting",
            "screen_path": "/tmp/sign-in.png",
            "screen_mime": "image/png",
        },
    )
    payload = mcp._auth_payload({"action": "open", "url": "https://www.xiaohongshu.com/explore/1"})
    assert "SMS code" in payload["instruction"]
    assert "picture_markdown" in payload["instruction"]
