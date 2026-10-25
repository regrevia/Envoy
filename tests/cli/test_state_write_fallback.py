"""A confined host must lose the lock, not the read.

Reported: an agent running under a workspace-write sandbox asked Envoy to read a
Douyin page. Envoy tried to create ``~/.envoy/materials``, got ``Operation not
permitted``, and failed the whole read with ``invalid_request`` over MCP as
``[object Object]``. The page itself was reachable; only the scratch directory
was not. These tests pin the degradation path and the readable failure text.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import cli, mcp, media, reader, storage
from envoy_core.schemas.read_v1 import ReadRequest


def _request(include_visual: bool = False) -> ReadRequest:
    payload: dict = {
        "protocol_version": "envoy.read/1",
        "request_id": "state-test",
        "source": {"url": "https://example.org/video"},
    }
    if include_visual:
        payload["scope"] = {"include_visual": True}
    return ReadRequest.model_validate(payload)


def _readonly_root(tmp_path: Path) -> Path:
    parent = tmp_path / "confined"
    materials = parent / "materials"
    materials.mkdir(parents=True)
    os.chmod(materials, 0o500)
    return materials


def _restore(path: Path) -> None:
    for target in (path, path.parent):
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass


def test_unwritable_state_root_falls_back_to_a_writable_directory(
    tmp_path: Path, monkeypatch
) -> None:
    blocked = _readonly_root(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    os.chmod(fake_home, 0o500)
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(blocked))
    monkeypatch.setenv("HOME", str(fake_home))
    storage.reset_root_cache()
    try:
        root = storage.resolve_material_root()
        report = storage.fallback_report()

        assert root != blocked
        assert str(root).startswith(tempfile.gettempdir())
        assert report is not None
        assert report["requested"] == str(blocked)
        assert report["kind"] == "state_not_writable"
        (root / "material-probe.json").write_text("{}", encoding="utf-8")
    finally:
        _restore(blocked)
        os.chmod(fake_home, 0o700)
        storage.reset_root_cache()


def test_preferred_root_needs_no_fallback(tmp_path: Path, monkeypatch) -> None:
    preferred = tmp_path / "materials"
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(preferred))
    storage.reset_root_cache()
    try:
        assert storage.resolve_material_root() == preferred
        assert storage.fallback_report() is None
    finally:
        storage.reset_root_cache()


def test_lock_skips_instead_of_failing_the_read(tmp_path: Path, monkeypatch) -> None:
    blocked = _readonly_root(tmp_path)
    monkeypatch.setattr(reader, "_material_root", lambda: blocked)

    @reader._serialize_read_request
    def _fake_read(request: ReadRequest):
        return reader._no_speech_partial(
            request,
            "https://example.org/video",
            "The audio track holds no detectable speech",
            title="Clip",
        )

    try:
        response = _fake_read(_request())
    finally:
        _restore(blocked)

    video = response.coverage.modalities["video"]
    assert video.details["state_lock"] == "skipped"
    assert any("process serialization" in note for note in video.unresolved)


def test_optional_lock_reports_skipped_but_runs_the_body(tmp_path: Path) -> None:
    blocked = _readonly_root(tmp_path)
    seen: list[bool] = []
    try:
        with media.optional_interprocess_lock(
            blocked / ".read-aa.lock",
            max_wait_seconds=1,
            busy_message="busy",
        ) as held:
            seen.append(held)
    finally:
        _restore(blocked)

    assert seen == [False]


def _partial_payload() -> dict:
    action = {
        "kind": "login_required",
        "question": "This Douyin page needs a signed-in browser to publish its player.",
        "options": [{
            "kind": "import_cookies",
            "effect": "Reuse the cookies already in your browser",
            "command": "envoy auth import --from chrome",
        }],
    }
    return {
        "result": "partial",
        "blocks": [{
            "id": "blk-title",
            "kind": "heading",
            "content": {"type": "text", "text": "Douyin"},
        }],
        "resources": [],
        "coverage": {"modalities": {"video": {
            "extraction": "partial",
            "unresolved": ["The page player bound no downloadable source"],
            "details": {"needs_user_action": action, "page_player_bound": False},
        }}},
    }


def test_partial_read_text_carries_the_action_and_refuses_visual_claims() -> None:
    text = mcp._tool_content_text(_partial_payload(), True, _request(include_visual=True))

    assert "Envoy read partial" in text
    assert "Requires user action" in text
    assert "envoy auth import --from chrome" in text
    assert "The page player bound no downloadable source" in text
    assert "No frames or images were delivered" in text


def test_visible_gap_beats_a_cosmetic_exclusion() -> None:
    payload = _partial_payload()
    payload["coverage"]["modalities"]["video"]["unresolved"] = [
        "5 obvious interface decoration images were excluded",
        "The page player bound no downloadable source",
    ]

    text = mcp._tool_content_text(payload, True, _request(include_visual=True))

    first = text.index("Unresolved:")
    assert text.index("The page player bound", first) < text.index("decoration", first)


def test_plain_text_read_keeps_the_short_summary() -> None:
    payload = {
        "result": "ready",
        "blocks": [{"id": "b1", "content": {"type": "text", "text": "hello"}}],
        "resources": [],
        "coverage": {"modalities": {"text": {"extraction": "full"}}},
    }

    text = mcp._tool_content_text(payload, True, _request(include_visual=True))

    assert "No frames or images were delivered" not in text
    assert "Requires user action" not in text


def test_blocked_read_text_names_the_action() -> None:
    payload = {
        "result": "blocked",
        "errors": [{
            "code": "challenge_required",
            "message": "The page still requires login or security verification.",
            "details": {"needs_user_action": {
                "kind": "login_required",
                "question": "Sign in once in a visible browser.",
                "options": [{"kind": "visible_login", "command": "envoy auth login"}],
            }},
        }],
    }

    text = mcp._tool_content_text(payload, True, _request())

    assert "Sign in once in a visible browser." in text
    assert "envoy auth login" in text


def test_mcp_reports_an_unwritable_state_root_as_an_actionable_block(monkeypatch) -> None:
    def _raise(_request):
        raise storage.StateUnavailable(
            "Envoy cannot write its state directory. Tried: /ro/.envoy/materials"
        )

    monkeypatch.setattr(mcp, "read_public_page", _raise)
    response = mcp.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": "envoy_read",
            "arguments": {
                "request_id": "mcp-state",
                "source": {"url": "https://example.org/video"},
            },
        },
    })

    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["errors"][0]["code"] == "state_not_writable"
    assert "ENVOY_LOCAL_DATA_DIR" in result["content"][0]["text"]


def test_mcp_maps_an_eperm_state_error_to_the_same_block(monkeypatch, tmp_path) -> None:
    def _raise(_request):
        raise OSError(errno.EPERM, "Operation not permitted", str(tmp_path / ".read-6f.lock"))

    monkeypatch.setattr(mcp, "read_public_page", _raise)
    response = mcp.handle_message({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": "envoy_read",
            "arguments": {
                "request_id": "mcp-eprem",
                "source": {"url": "https://example.org/video"},
            },
        },
    })

    result = response["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["errors"][0]["code"] == "state_not_writable"
    text = result["content"][0]["text"]
    assert ".read-6f.lock" in text
    assert "Requires user action" in text


def test_cli_names_the_state_directory_instead_of_invalid_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def _raise(_request):
        raise OSError(errno.EPERM, "Operation not permitted", str(tmp_path / ".read-6f.lock"))

    monkeypatch.setattr(cli, "read_public_page", _raise)
    request_file = tmp_path / "req.json"
    request_file.write_text(json.dumps({
        "protocol_version": "envoy.read/1",
        "request_id": "cli-state",
        "source": {"url": "https://example.org/video"},
    }), encoding="utf-8")

    code = cli.main(["read", "--request-json", str(request_file)])

    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 2
    assert payload["error"] == "state_not_writable"
    assert payload["action_required"] is True
    assert "ENVOY_LOCAL_DATA_DIR" in payload["actions"][0]["command"]
