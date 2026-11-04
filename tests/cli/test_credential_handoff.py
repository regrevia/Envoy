"""Credential handoff is opt-in, local-only, and never shows cookie values."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from envoy_cli import authflow, browser, cookies, features, mcp, permissions, reader
from envoy_core.schemas.read_v1 import ReadRequest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "imported-cookies.json"
    monkeypatch.setattr(cookies, "cookie_store_path", lambda: path)
    return path


def _store_a_cookie(store) -> None:
    store.write_text(
        json.dumps({
            "source": "chrome",
            "domains": ["example.com"],
            "imported_at": "2026-01-01T00:00:00+00:00",
            "cookies": [{
                "name": "sid",
                "value": "super-secret-value",
                "domain": ".example.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            }],
        }),
        encoding="utf-8",
    )
    os.chmod(store, 0o600)


def test_cookie_import_is_off_by_default_while_the_sign_in_is_on():
    status = features.credential_handoff_status()
    assert status["enabled"] is False
    assert status["source"] == "default"
    assert features.credential_handoff_enabled() is False
    # Reading the user's own browsers stays opt-in; opening the site's sign-in
    # page in Envoy's own profile is the default way out of a login wall.
    assert status["in_chat_sign_in"] is True
    assert features.sign_in_enabled() is True


def test_the_tool_is_advertised_because_the_sign_in_is_on():
    names = [tool["name"] for tool in mcp.tools_for_toolset("core")]
    assert "envoy_auth" in names


def test_the_tool_disappears_only_when_both_switches_are_off(monkeypatch):
    monkeypatch.setenv("ENVOY_IN_CHAT_SIGN_IN", "0")
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    names = [tool["name"] for tool in mcp.tools_for_toolset("core")]
    assert "envoy_auth" not in names


def test_turning_it_on_advertises_the_tool_and_persists_owner_only():
    report = features.set_credential_handoff(True)
    assert report["enabled"] is True and report["source"] == "config"
    assert features.credential_handoff_enabled() is True
    assert [tool["name"] for tool in mcp.tools_for_toolset("core")][-1] == "envoy_auth"

    path = features.config_path()
    assert json.loads(path.read_text(encoding="utf-8"))["credential_handoff"] is True
    assert permissions.is_owner_only(path)
    assert permissions.is_owner_only(path.parent)

    features.set_credential_handoff(False)
    assert features.credential_handoff_enabled() is False


def test_the_environment_wins_over_the_file(monkeypatch):
    features.set_credential_handoff(True)
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    assert features.credential_handoff_enabled() is False
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "on")
    assert features.credential_handoff_enabled() is True


def test_an_import_call_is_blocked_while_the_handoff_switch_is_off():
    payload = mcp._auth_payload({
        "action": "import",
        "url": "https://example.com/gated",
        "browser": "chrome",
    })
    assert payload["result"] == "blocked"
    assert payload["credential_handoff"]["enabled"] is False
    # The instruction must steer to the path that works without the switch.
    assert "action 'open'" in payload["instruction"]
    assert "envoy auth handoff on" in payload["instruction"]


def test_an_open_call_is_blocked_only_when_the_sign_in_is_switched_off(monkeypatch):
    monkeypatch.setenv("ENVOY_IN_CHAT_SIGN_IN", "0")
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    payload = mcp._auth_payload({"action": "open", "url": "https://example.com/gated"})
    assert payload["result"] == "blocked"
    assert payload["credential_handoff"]["in_chat_sign_in"] is False
    assert "screen_path" not in payload


def test_a_read_hint_offers_the_in_chat_sign_in_by_default():
    hint = reader._login_user_action("https://example.com/gated")
    assert hint["credential_handoff"]["enabled"] is False
    assert hint["credential_handoff"]["in_chat_sign_in"] is True
    first = hint["options"][0]
    assert first["action"] == "in_chat_sign_in"
    assert "envoy_auth" in first["instruction"]
    # Copying a browser's cookies is the opt-in half and is not offered here.
    assert "import_cookies" not in [option["action"] for option in hint["options"]]
    assert "envoy auth handoff on" in hint["credential_handoff"]["how_to_enable"]
    assert hint.get("action_id")


def test_an_enabled_read_hint_offers_both_paths(monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    hint = reader._login_user_action("https://example.com/gated")
    assert hint["credential_handoff"]["enabled"] is True
    actions = [option["action"] for option in hint["options"]]
    assert actions[0] == "in_chat_sign_in"
    assert "import_cookies" in actions


def test_a_stored_session_is_not_attached_while_disabled(store):
    from envoy_cli import browser

    _store_a_cookie(store)

    class Context:
        def __init__(self):
            self.added = []

        def add_cookies(self, values):
            self.added.extend(values)

    context = Context()
    assert browser._apply_imported_cookies(context, "https://example.com/x") == 0
    assert context.added == []
    assert "super-secret-value" not in json.dumps(context.added)


def test_an_enabled_static_fetch_sends_the_stored_cookie(store, monkeypatch):
    _store_a_cookie(store)
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>ok</body></html>"
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    reader._fetch_html("https://example.com/article", 1024 * 1024, {})

    assert seen and "sid=super-secret-value" in seen[0]


def test_a_disabled_static_fetch_sends_no_imported_cookie(store, monkeypatch):
    _store_a_cookie(store)
    # The imported store needs the switch; with it off no cookie may come from it.
    monkeypatch.setattr(browser, "profile_cookie_header", lambda host: "")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie", ""))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>ok</body></html>"
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    reader._fetch_html("https://example.com/article", 1024 * 1024, {})

    assert seen == [""]


def test_an_import_call_reports_counts_never_values(monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    monkeypatch.setattr(
        cookies,
        "import_cookies",
        lambda source, source_url=None: {
            "source": source,
            "imported": 2,
            "domains": ["example.com"],
            "path": "/home/u/.envoy/imported-cookies.json",
            "cookies": ["sid", "csrf"],
        },
    )

    payload = mcp._auth_payload({
        "action": "import",
        "url": "https://example.com/x",
        "browser": "chrome",
    })

    assert payload["result"] == "ready"
    assert payload["imported"] == 2
    text = json.dumps(payload)
    assert "sid" in text  # names are fine
    assert "super-secret-value" not in text
    assert payload["use_local_store"] is True


def test_an_import_call_needs_a_browser_name(monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    with pytest.raises(ValueError):
        mcp._auth_payload({"action": "import", "url": "https://example.com/x"})


def test_cookie_values_never_reach_a_read_result(store, monkeypatch):
    _store_a_cookie(store)
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, headers={"content-type": "text/html"}, text="<html><body>sign in</body></html>"
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    request = ReadRequest.model_validate({
        "request_id": "secrets",
        "source": {"url": "https://example.com/private"},
    })
    response = reader._read_public_page_once(request)

    # The read may fail or pause for a human; either way the payload a host sees
    # must not contain the cookie the user never agreed to disclose.
    dumped = json.dumps(response.model_dump(mode="json"), default=str)
    assert "super-secret-value" not in dumped
    # The request was answered, so the check is on a real read path, not a stub.
    assert response.request_id == "secrets"


def test_a_read_uses_the_session_the_visible_window_stored(store, monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    seen: list[str] = []
    monkeypatch.setattr(authflow, "profile_session_known", lambda host: True)
    monkeypatch.setattr(
        browser, "profile_cookie_header", lambda host: seen.append(host) or "user_session=abc"
    )
    header = reader._credential_cookie_header("https://github.com/settings/profile")
    assert header == "user_session=abc"
    assert seen == ["github.com"]


def test_the_sign_in_session_is_sent_with_the_handoff_switch_off(store, monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    seen: list[str] = []
    monkeypatch.setattr(authflow, "profile_session_known", lambda host: True)
    monkeypatch.setattr(
        browser, "profile_cookie_header", lambda host: seen.append(host) or "user_session=abc"
    )
    assert (
        reader._credential_cookie_header("https://github.com/settings/profile")
        == "user_session=abc"
    )
    assert seen == ["github.com"]


def test_no_known_session_means_the_profile_is_never_opened(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_IN_CHAT_SIGN_IN", "1")
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    monkeypatch.setattr(authflow, "auth_root", lambda: tmp_path / "auth")
    monkeypatch.setattr(cookies, "load_cookies_for", lambda url: [])
    monkeypatch.setattr(cookies, "cookie_store_path", lambda: tmp_path / "none.json")

    def _boom(*_args, **_kwargs):
        raise AssertionError("a public link must not pay for a profile read")

    monkeypatch.setattr(browser, "profile_cookie_header", _boom)
    monkeypatch.setattr(browser, "profile_cookie_rows", _boom)
    assert reader._credential_cookie_header("https://www.youtube.com/watch?v=abc") == ""
    assert cookies.yt_dlp_cookie_file("https://www.youtube.com/watch?v=abc") is None


def test_a_completed_sign_in_marks_the_host_and_its_subdomains(tmp_path, monkeypatch):
    monkeypatch.setattr(authflow, "auth_root", lambda: tmp_path / "auth")
    assert authflow.profile_session_known("www.youtube.com") is False

    authflow.mark_profile_session("youtube.com")

    assert authflow.profile_session_known("www.youtube.com") is True
    assert authflow.profile_session_known("m.youtube.com") is True
    assert authflow.profile_session_known("example.com") is False
    assert permissions.is_owner_only(authflow.profile_session_path())


def test_the_sign_in_is_not_read_when_the_user_switched_it_off(store, monkeypatch):
    monkeypatch.setenv("ENVOY_IN_CHAT_SIGN_IN", "0")
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")

    def _boom(host):
        raise AssertionError("the profile must not be opened when both switches are off")

    monkeypatch.setattr(browser, "profile_cookie_header", _boom)
    assert reader._credential_cookie_header("https://github.com/settings/profile") == ""


def test_a_sign_in_session_reaches_the_yt_dlp_jar(store, monkeypatch, tmp_path):
    from envoy_cli import cache

    monkeypatch.setattr(authflow, "profile_session_known", lambda host: True)
    monkeypatch.setenv("ENVOY_IN_CHAT_SIGN_IN", "1")
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "0")
    monkeypatch.setattr(cache, "envoy_home", lambda: tmp_path)
    monkeypatch.setattr(cookies, "load_cookies_for", lambda url: [])
    monkeypatch.setattr(
        browser,
        "profile_cookie_rows",
        lambda host: [
            {
                "domain": ".youtube.com",
                "name": "SID",
                "path": "/",
                "secure": True,
                "expires": 1893456000,
                "value": "profile-secret",
            }
        ],
    )

    path = cookies.yt_dlp_cookie_file("https://www.youtube.com/watch?v=abc")

    assert path
    body = Path(path).read_text(encoding="utf-8")
    assert "\tSID\tprofile-secret" in body
    assert body.splitlines()[0] == "# Netscape HTTP Cookie File"
    assert permissions.is_owner_only(Path(path))


def test_an_imported_cookie_wins_over_the_visible_profile(store, monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    _store_a_cookie(store)

    def _boom(host):
        raise AssertionError("the profile is a fallback, not a second opinion")

    monkeypatch.setattr(browser, "profile_cookie_header", _boom)
    header = reader._credential_cookie_header("https://example.com/page")
    assert header == "sid=super-secret-value"


def test_a_profile_that_will_not_open_is_not_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "browser_profile_dir", lambda create=False: tmp_path / "missing")
    browser.reset_profile_cookie_cache()
    assert browser.profile_cookie_header("github.com") == ""
    assert browser.profile_cookie_rows("github.com") == []
