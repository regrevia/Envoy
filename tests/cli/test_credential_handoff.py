"""Credential handoff is opt-in, local-only, and never shows cookie values."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from envoy_cli import cookies, features, mcp, permissions, reader
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


def test_the_switch_is_off_by_default():
    status = features.credential_handoff_status()
    assert status["enabled"] is False
    assert status["source"] == "default"
    assert features.credential_handoff_enabled() is False


def test_the_tool_is_not_advertised_while_the_switch_is_off():
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


def test_a_disabled_tool_call_opens_nothing_and_says_how_to_enable():
    payload = mcp._auth_payload({"action": "open", "url": "https://example.com/gated"})
    assert payload["result"] == "blocked"
    assert payload["credential_handoff"]["enabled"] is False
    assert "envoy auth handoff on" in payload["instruction"]
    assert "screen_path" not in payload


def test_a_disabled_read_hint_does_not_offer_the_tool():
    hint = reader._login_user_action("https://example.com/gated")
    assert hint["credential_handoff"]["enabled"] is False
    assert [option["action"] for option in hint["options"]] == ["human_sign_in"]
    assert "action_id" not in hint
    assert "envoy auth handoff on" in hint["credential_handoff"]["how_to_enable"]


def test_an_enabled_read_hint_offers_the_in_chat_sign_in(monkeypatch):
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")
    hint = reader._login_user_action("https://example.com/gated")
    assert hint["credential_handoff"]["enabled"] is True
    first = hint["options"][0]
    assert first["action"] == "in_chat_sign_in"
    assert "envoy_auth" in first["instruction"]


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


def test_a_disabled_static_fetch_sends_no_cookie(store, monkeypatch):
    _store_a_cookie(store)
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
