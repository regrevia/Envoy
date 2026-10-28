"""The in-chat sign-in handoff: screenshot, state, and the tool that carries it.

These tests never open a browser. They cover the parts a model depends on:
which page is worth photographing, what a stale action must not report, and
that the MCP tool exists in the toolset every host loads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from envoy_cli import auth_session, mcp


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
