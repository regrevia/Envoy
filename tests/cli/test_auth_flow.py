from __future__ import annotations

import json

import pytest

from envoy_cli import authflow, auth_window, cli, profile_state, reader
from envoy_core.schemas import ErrorCode, ReadRequest


@pytest.fixture()
def auth_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(tmp_path / "profile"))
    return tmp_path


def test_a_pause_is_persisted_and_reused_for_the_same_link(auth_home):
    first = authflow.create_auth_action("https://www.douyin.com/video/1")
    second = authflow.create_auth_action("https://www.douyin.com/video/1")
    other = authflow.create_auth_action("https://vimeo.com/2")

    assert first["action_id"] == second["action_id"]
    assert other["action_id"] != first["action_id"]
    assert authflow.load_auth_action(first["action_id"])["status"] == "pending"
    assert authflow.resolve_resume_token(first["resume_token"])["action_id"] == first["action_id"]


def test_a_pause_expires_and_stops_being_resumable(auth_home):
    action = authflow.create_auth_action("https://vimeo.com/9")
    path = authflow._action_path(action["action_id"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["expires_at"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = authflow.load_auth_action(action["action_id"])

    assert loaded["status"] == "expired"
    assert authflow.auth_status()["pending"] == []


def test_verification_needs_headless_reuse_to_count_as_success(auth_home):
    action = authflow.create_auth_action("https://vimeo.com/3")
    page_only = authflow.record_verification(
        action["action_id"], {"page_access": True, "headless_reusable": False}
    )
    assert page_only["status"] == "failed"

    ok = authflow.record_verification(
        action["action_id"], {"page_access": True, "headless_reusable": True}
    )
    assert ok["status"] == "ok"


def test_clearing_credentials_invalidates_live_pauses(auth_home):
    action = authflow.create_auth_action("https://vimeo.com/4")

    assert authflow.invalidate_actions() == 1
    assert authflow.load_auth_action(action["action_id"])["status"] == "canceled"


def test_a_blocked_read_carries_a_top_level_authenticate_need(auth_home):
    request = ReadRequest(
        request_id="r1",
        source={"url": "https://www.douyin.com/video/766"},
    )

    response = reader._blocked(
        request,
        ErrorCode.AUTH_REQUIRED,
        "This page needs a signed-in session",
        action_required=True,
    )

    assert [need.type for need in response.needs] == ["authenticate"]
    need = response.needs[0]
    assert need.action_id.startswith("auth-")
    assert need.resume_token
    action = response.errors[0].details["needs_user_action"]
    assert action["action_id"] == need.action_id
    assert action["open_command"].endswith(need.action_id)
    assert authflow.load_auth_action(need.action_id)["source_url"] == "https://www.douyin.com/video/766"


def test_cli_status_lists_pauses_and_clear_cancels_them(auth_home, capsys):
    action = authflow.create_auth_action("https://vimeo.com/5")

    assert cli.main(["auth", "status", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pauses"]["pending"][0]["action_id"] == action["action_id"]

    assert cli.main(["auth", "clear", "--json"]) == 0
    cleared = json.loads(capsys.readouterr().out)
    assert cleared["pauses_invalidated"] == 1
    assert authflow.load_auth_action(action["action_id"])["status"] == "canceled"


def test_cli_verify_reports_the_reason_without_opening_a_window(auth_home, capsys, monkeypatch):
    action = authflow.create_auth_action("https://vimeo.com/6")
    monkeypatch.setattr(
        auth_window,
        "verify_source",
        lambda url, timeout_ms=45000: {
            "page_access": True,
            "media_access": True,
            "headless_reusable": True,
            "reason": "readable",
        },
    )

    assert cli.main(["auth", "verify", "--action-id", action["action_id"], "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["verification"]["reason"] == "readable"


def test_cli_resume_uses_the_token_to_find_the_paused_link(auth_home, capsys, monkeypatch):
    action = authflow.create_auth_action("https://vimeo.com/7", purpose="collect_item")
    monkeypatch.setattr(
        auth_window,
        "verify_source",
        lambda url, timeout_ms=45000: {
            "page_access": False,
            "media_access": False,
            "headless_reusable": False,
            "reason": "still_behind_login",
        },
    )

    assert cli.main(["auth", "resume", "--token", action["resume_token"], "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_url"] == "https://vimeo.com/7"
    assert payload["status"] == "failed"


def test_clearing_a_host_removes_only_that_sites_profile_cookies(auth_home):
    import sqlite3

    profile = profile_state.browser_profile_dir()
    database = profile / "Default" / "Cookies"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE cookies (host_key TEXT)")
    connection.executemany(
        "INSERT INTO cookies VALUES (?)",
        [("vimeo.com",), (".vimeo.com",), ("example.com",)],
    )
    connection.commit()
    connection.close()

    result = profile_state.clear_site_cookies("vimeo.com")

    assert result["cleared"] is True
    connection = sqlite3.connect(database)
    remaining = [row[0] for row in connection.execute("SELECT host_key FROM cookies")]
    connection.close()
    assert remaining == ["example.com"]
