"""Every failed read names what kind of failure it was and what fixes it.

The host has one question after a failed read: what do I do next? An error code
answers "what went wrong inside Envoy", which is not the same thing -- a rate
limit and a sign-in wall both arrive as "not the page", and only one of them
yields to a session. These tests pin the classification, the next step it
carries, and that the two surfaces a host reads (the tool text and the JSON
payload) say the same thing.
"""

from __future__ import annotations

import io
import json
import types

import pytest

from envoy_cli import diagnosis, mcp, reader
from envoy_core.schemas.read_v1 import ErrorCode, ReadRequest


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate({
        "request_id": "diagnosis-1",
        "source": {"kind": "auto", "url": url},
        "goal": "read this page",
        "budget": {"max_work_seconds": 5},
    })


def _offline(monkeypatch, html: str, *, browser=None):
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
        browser or (lambda *a, **k: (None, None)),
    )
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda url, *a, **k: (url, html),
    )


def test_every_error_code_resolves_to_one_of_the_known_kinds():
    """A code without a kind is a failure the host cannot act on."""
    assert set(diagnosis.FAILURE_KINDS) == {
        item.kind for item in diagnosis._DIAGNOSES.values()
    }
    unmapped = []
    for code in ErrorCode:
        failure = diagnosis.diagnose(code)
        assert failure.kind in diagnosis.FAILURE_KINDS
        assert failure.summary
        assert failure.next_step
        if failure.kind == diagnosis.UNKNOWN:
            unmapped.append(code.value)
    assert unmapped == ["invalid_request"]
    assert diagnosis.diagnose(ErrorCode.INVALID_REQUEST).kind == diagnosis.UNKNOWN


def test_an_explicit_failure_kind_wins_over_the_error_code():
    """`challenge_required` covers both a captcha and an egress rate limit."""
    explicit = diagnosis.diagnose(
        ErrorCode.CHALLENGE_REQUIRED, {"failure_kind": "source_blocked"}
    )
    assert explicit.kind == diagnosis.SOURCE_BLOCKED
    assert explicit.retryable is True
    assert "sign-in does not lift this" in explicit.next_step

    default = diagnosis.diagnose(ErrorCode.CHALLENGE_REQUIRED)
    assert default.kind == diagnosis.LOGIN_REQUIRED
    assert "envoy_auth" in default.next_step


def test_an_empty_body_with_a_handoff_is_dynamic_content():
    """The same empty body means different things with and without a session."""
    with_action = diagnosis.diagnose(
        ErrorCode.EMPTY_CONTENT,
        {"needs_user_action": {"kind": "login_or_verification", "action_id": "auth-1"}},
    )
    assert with_action.kind == diagnosis.DYNAMIC_CONTENT
    assert with_action.needs_user_action is True

    without_action = diagnosis.diagnose(ErrorCode.EMPTY_CONTENT, {})
    assert without_action.kind == diagnosis.EMPTY_DOCUMENT
    assert "plain fetch" in without_action.next_step


def test_a_missing_capability_names_the_command_that_installs_it():
    failure = diagnosis.diagnose(
        ErrorCode.MISSING_CAPABILITY,
        {"needs_user_action": {"command": "envoy browser setup"}},
    )
    assert failure.kind == diagnosis.CAPABILITY_MISSING
    assert "envoy browser setup" in failure.next_step


def test_a_blocked_payload_offers_the_takeover_without_digging(monkeypatch):
    """The one actionable thing must not be reachable only through `errors[0]`."""
    url = "https://www.xiaohongshu.com/explore/0123456789abcdef"
    _offline(
        monkeypatch,
        '<html><head><title>小红书</title></head><body><div id="app"></div></body></html>',
    )

    response = reader._read_public_page_once(_request(url))
    payload = mcp._read_payload(response)
    action = payload["needs_user_action"]

    assert action["action_id"] == payload["needs"][0]["action_id"]
    assert action["resume_token"]
    assert action["requires_human"] is True
    assert action["open_command"].endswith(action["action_id"])
    assert action["verify_command"].endswith(action["action_id"])
    # Every route offered is something a human does, not a command the model
    # should run behind their back.
    assert [option["action"] for option in action["options"]] == [
        "in_chat_sign_in",
        "human_sign_in",
    ]
    assert all(option["requires_human"] is True for option in action["options"])

    text = mcp._material_content_text(payload, "envoy_read", types.SimpleNamespace(max_chars=4000))
    assert f"next: {payload['diagnosis']['next_step']}" in text


def test_a_platform_shell_reaches_the_host_with_a_diagnosis(monkeypatch):
    """The XHS empty shell: blocked, attributed, and with a next step in the text."""
    url = "https://www.xiaohongshu.com/explore/0123456789abcdef"
    _offline(
        monkeypatch,
        '<html><head><title>小红书</title></head><body><div id="app"></div></body></html>',
    )

    response = reader._read_public_page_once(_request(url))
    payload = mcp._read_payload(response)
    failure = payload["diagnosis"]

    assert payload["result"] == "blocked"
    assert failure["kind"] == diagnosis.DYNAMIC_CONTENT
    assert failure["needs_user_action"] is True
    assert "envoy_auth" in failure["next_step"]

    text = mcp._material_content_text(payload, "envoy_read", types.SimpleNamespace(max_chars=4000))
    assert "next: sign the user in once with envoy_auth" in text


def test_a_plain_empty_page_stays_empty_document(monkeypatch):
    _offline(monkeypatch, "<html><head><title>Blank</title></head><body></body></html>")

    response = reader._read_public_page_once(_request("https://example.com/blank"))
    payload = mcp._read_payload(response)
    failure = payload["diagnosis"]

    assert failure["kind"] == diagnosis.EMPTY_DOCUMENT
    assert failure["needs_user_action"] is False
    assert "plain fetch" in failure["next_step"]

    text = mcp._material_content_text(payload, "envoy_read", types.SimpleNamespace(max_chars=4000))
    assert "why: " in text
    assert "take it with your own plain fetch" in text


def test_a_rate_limit_page_is_source_blocked_not_a_sign_in(monkeypatch):
    """Signing in after "too many requests" wastes the one step the host has."""
    _offline(
        monkeypatch,
        "<html><head><title>访问过于频繁</title></head><body>"
        "<p>请求过于频繁，请稍后再试</p></body></html>",
    )

    response = reader._read_public_page_once(_request("https://www.xiaohongshu.com/explore/abc"))
    payload = mcp._read_payload(response)
    failure = payload["diagnosis"]

    assert payload["result"] == "blocked"
    assert failure["kind"] == diagnosis.SOURCE_BLOCKED
    assert failure["retryable"] is True
    assert "sign-in does not lift this" in failure["next_step"]
    # The code this refusal carries still says "challenge", but a session cannot
    # clear a rate limit, so no sign-in step may reach the host.
    assert payload.get("needs_user_action") is None
    assert payload.get("needs") is None
    # A human still has to act (change exit, come back later) -- but not by
    # signing in, which is exactly what the code alone would have suggested.
    assert payload["errors"][0]["action_required"] is True

    text = mcp._material_content_text(payload, "envoy_read", types.SimpleNamespace(max_chars=4000))
    assert f"next: {failure['next_step']}" in text
    assert "envoy_auth" not in text


def test_cli_stdout_lifts_the_handoff_and_the_diagnosis(monkeypatch, tmp_path, capsys):
    """A caller outside the MCP bridge must not have to dig in `errors[0].details`."""
    from envoy_cli import cli
    from envoy_cli.reader import PublicPageError

    request = _request("https://www.xiaohongshu.com/explore/0123456789abcdef")
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")

    action = reader._login_user_action(request.source.url)
    error = PublicPageError(
        "xiaohongshu.com served an empty shell instead of the page",
        code=ErrorCode.EMPTY_CONTENT,
        details={"needs_user_action": action, "material_id": "material-1"},
    )

    def raise_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(cli, "read_public_page", raise_error)

    exit_code = cli.main(["read", "--request-json", str(request_path)])
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert exit_code == 2
    assert payload["result"] == "blocked"
    assert payload["needs_user_action"]["action_id"] == action["action_id"]
    assert payload["material_id"] == "material-1"
    assert payload["diagnosis"]["kind"] == diagnosis.DYNAMIC_CONTENT


def test_a_dead_request_id_answers_with_the_transport_diagnosis():
    """The failure the host cannot attribute: it is Envoy's, not the source's."""
    message = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}}
    session = mcp.MCPProtocolSession()

    def boom(*_args, **_kwargs):
        raise RuntimeError("reader exploded")

    original = mcp.handle_message
    mcp.handle_message = boom
    try:
        first = mcp._dispatch_message(message, session, "core")
        second = mcp._dispatch_message(message, session, "core")
    finally:
        mcp.handle_message = original

    for response in (first, second):
        assert response["error"]["code"] == -32603
        assert response["error"]["data"]["diagnosis"]["kind"] == diagnosis.TRANSPORT_CLOSED
        assert response["error"]["data"]["retry_with_same_id"] is True
    assert "Send the same id again" in second["error"]["message"]


def test_an_unknown_kind_still_yields_a_usable_answer():
    failure = diagnosis.diagnose("some_future_code", {}, "unexpected payload")
    assert failure.kind == diagnosis.UNKNOWN
    assert "unexpected payload" in failure.summary
    assert failure.next_step
