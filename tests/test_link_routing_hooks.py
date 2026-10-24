"""Tests for the multi-host link-routing hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "packages" / "envoy-cli" / "src", ROOT / "packages" / "envoy-core" / "src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from envoy_cli.hooks import (  # noqa: E402
    HOOK_MARKER,
    build_prompt_context,
    extract_public_urls,
    inspect_prompt,
    install_codex_hooks,
    normalize_host,
    prompt_hook_payload,
    run_prompt_hook,
)

CLAUDE_PLUGIN = ROOT / "integrations" / "claude-code" / "envoy"
CODEX_PLUGIN = ROOT / "integrations" / "codex" / "envoy"
CLAUDE_HOOKS = CLAUDE_PLUGIN / "hooks" / "hooks.json"
CODEX_HOOKS = CODEX_PLUGIN / "hooks" / "hooks.json"
CLI_PYPROJECT = ROOT / "packages" / "envoy-cli" / "pyproject.toml"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_extracts_public_urls_and_stops_at_cjk_punctuation():
    text = "读 https://example.com/a?b=1。再看 https://example.org/x，最后 https://example.net/y"
    assert extract_public_urls(text) == (
        "https://example.com/a?b=1",
        "https://example.org/x",
        "https://example.net/y",
    )


def test_strips_ascii_trailing_punctuation_and_balanced_brackets():
    assert extract_public_urls("(see https://en.wikipedia.org/wiki/Foo_(bar)).") == (
        "https://en.wikipedia.org/wiki/Foo_(bar)",
    )
    assert extract_public_urls("https://example.com/x).") == ("https://example.com/x",)


def test_rejects_local_and_private_destinations():
    text = (
        "https://localhost:3000/a https://127.0.0.1/a https://0.0.0.0/a "
        "https://10.0.0.2/a https://172.16.4.4/a https://192.168.1.2/a "
        "https://169.254.1.1/a https://printer.local/a https://intranet/a "
        "https://[::1]/a ftp://example.com/a"
    )
    assert extract_public_urls(text) == ()


def test_deduplicates_urls_preserving_first_seen_order():
    assert extract_public_urls(
        "https://example.com/a https://EXAMPLE.com/a https://example.org/b"
    ) == ("https://example.com/a", "https://example.org/b")


def test_opt_out_disables_routing():
    routing = inspect_prompt("不要用 Envoy，直接读 https://example.com/a")
    assert routing.urls == ("https://example.com/a",)
    assert routing.opted_out is True
    assert routing.should_route is False


def test_opt_out_tolerates_missing_spaces_and_case():
    for text in ("不要用Envoy，读 https://example.com/a", "DON'T   USE ENVOY: https://example.com/a"):
        routing = inspect_prompt(text)
        assert routing.opted_out is True
        assert routing.should_route is False


def test_build_context_mentions_envoy_first_fallback_and_image_priority():
    context = build_prompt_context(["https://example.com/a"], host="claude-code")
    assert context.startswith(HOOK_MARKER)
    assert "envoy_read" in context
    assert "envoy_expand" in context
    assert "envoy_search" in context
    assert "blocked" in context
    assert "challenge_required" in context
    assert "以图片为准" in context
    assert "https://example.com/a" in context


def test_policy_does_not_replace_native_search_and_handles_video():
    context = build_prompt_context(["https://example.com/a"], host="codex")
    assert "不替代宿主的联网搜索" in context
    assert "没有给链接的检索仍用宿主搜索工具" in context
    assert "视频" in context
    assert "节省 token" in context
    assert "search/expand" in context


def test_build_context_is_host_aware():
    assert "mcp__envoy__envoy_read" in build_prompt_context(["https://example.com"], host="dsh")
    assert "mcp__envoy__envoy_read" in build_prompt_context(["https://example.com"], host="claude-code")


def test_hook_payload_is_claude_compatible():
    payload = prompt_hook_payload(
        "claude-code",
        json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "读 https://example.com/a"}),
    )
    assert payload is not None
    output = payload["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert output["additionalContext"].startswith(HOOK_MARKER)


def test_hook_payload_is_empty_without_public_url():
    assert prompt_hook_payload("codex", json.dumps({"prompt": "hello"})) is None
    assert prompt_hook_payload("codex", json.dumps({"prompt": "读 https://localhost/a"})) is None


def test_hook_payload_auto_detects_prompt_language():
    english = prompt_hook_payload(
        "codex", json.dumps({"prompt": "Read https://example.com/a and summarize it"})
    )
    chinese = prompt_hook_payload(
        "codex", json.dumps({"prompt": "读一下 https://example.com/a 并总结"})
    )
    assert english is not None and chinese is not None
    english_context = english["hookSpecificOutput"]["additionalContext"]
    chinese_context = chinese["hookSpecificOutput"]["additionalContext"]
    assert "This turn contains public link(s)" in english_context
    assert "本轮用户消息包含公网链接" not in english_context
    assert "Envoy MCP 工具" not in english_context
    assert "`envoy_read`" in english_context
    assert "`envoy_read_local_video`" in english_context
    assert "本轮用户消息包含公网链接" in chinese_context
    assert "This turn contains public link(s)" not in chinese_context


def test_hook_payload_explicit_language_wins_over_auto_detection():
    payload = prompt_hook_payload(
        "codex",
        json.dumps({"prompt": "读 https://example.com/a"}),
        language="en",
    )
    assert payload is not None
    assert "This turn contains public link(s)" in payload["hookSpecificOutput"]["additionalContext"]


def test_hook_payload_is_idempotent():
    prompt = json.dumps({"prompt": f"{HOOK_MARKER}\n读 https://example.com/a"})
    assert prompt_hook_payload("codex", prompt) is None


def test_prompt_hook_accepts_user_prompt_and_messages_payloads():
    assert prompt_hook_payload("codex", json.dumps({"user_prompt": "读 https://example.com/a"}))
    assert prompt_hook_payload(
        "codex",
        json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": "读 https://example.com/a"}]}]}),
    )


def test_run_prompt_hook_always_returns_zero_for_known_hosts(capsys):
    assert run_prompt_hook("claude-code", json.dumps({"prompt": "读 https://example.com/a"})) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_run_prompt_hook_prints_nothing_without_url(capsys):
    assert run_prompt_hook("codex", json.dumps({"prompt": "hello"})) == 0
    assert capsys.readouterr().out == ""


def test_normalize_host_aliases():
    assert normalize_host("Claude") == "claude-code"
    assert normalize_host("deepseek-harness") == "dsh"
    assert normalize_host("unknown") is None


def _assert_hook_config(path: Path, source_var: str, host: str) -> None:
    config = _load_json(path)
    entries = config["hooks"]["UserPromptSubmit"]
    commands = [hook["command"] for entry in entries for hook in entry["hooks"]]
    assert len(commands) == 1
    command = commands[0]
    assert source_var in command
    assert "hooks/envoy-hook.sh" in command
    assert f"--host {host}" in command
    assert all(hook["type"] == "command" for entry in entries for hook in entry["hooks"])


def test_claude_plugin_hook_config():
    _assert_hook_config(CLAUDE_HOOKS, "${CLAUDE_PLUGIN_ROOT}", "claude-code")
    manifest = _load_json(CLAUDE_PLUGIN / ".claude-plugin" / "plugin.json")
    assert manifest["name"] == "envoy"
    # Claude Code auto-loads hooks/hooks.json; declaring manifest.hooks duplicates it.
    assert "hooks" not in manifest
    assert manifest["mcpServers"] == "./.mcp.json"
    assert _load_json(CLAUDE_PLUGIN / ".mcp.json")["mcpServers"]["envoy"]["args"] == ["mcp"]


def test_codex_plugin_hook_config():
    _assert_hook_config(CODEX_HOOKS, "${PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}", "codex")
    manifest = _load_json(CODEX_PLUGIN / ".codex-plugin" / "plugin.json")
    assert manifest["name"] == "envoy"
    # Codex 0.156 does not load plugin-supplied hooks; `envoy hook install --host
    # codex` merges them into ~/.codex/hooks.json instead. Keep the file as the
    # canonical hook source, and do not advertise a capability the plugin lacks.
    assert "hooks" not in manifest
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["capabilities"] == ["MCP"]
    assert "envoy hook install --host codex" in manifest["interface"]["longDescription"]
    assert _load_json(CODEX_PLUGIN / ".mcp.json")["mcpServers"]["envoy"]["args"] == ["mcp"]


def test_hook_scripts_are_identical_and_fail_open_without_envoy(tmp_path):
    claude_script = (CLAUDE_PLUGIN / "hooks" / "envoy-hook.sh").read_text(encoding="utf-8")
    codex_script = (CODEX_PLUGIN / "hooks" / "envoy-hook.sh").read_text(encoding="utf-8")
    assert claude_script == codex_script
    assert "ENVOY_BIN" in claude_script
    assert "exit 0" in claude_script
    assert '|| exit 0' in claude_script

    missing_env = {
        **os.environ,
        "PATH": "",
        "HOME": str(tmp_path),
        "ENVOY_BIN": str(tmp_path / "missing-envoy"),
    }
    missing = subprocess.run(
        ["/bin/sh", str(CLAUDE_PLUGIN / "hooks" / "envoy-hook.sh"), "prompt", "--host", "claude-code"],
        input='{"prompt":"read https://example.com/a"}',
        text=True,
        capture_output=True,
        env=missing_env,
    )
    assert missing.returncode == 0
    assert missing.stdout == ""

    broken_envoy = tmp_path / "broken-envoy"
    broken_envoy.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    broken_envoy.chmod(0o755)
    broken = subprocess.run(
        ["/bin/sh", str(CLAUDE_PLUGIN / "hooks" / "envoy-hook.sh"), "prompt", "--host", "claude-code"],
        input='{"prompt":"read https://example.com/a"}',
        text=True,
        capture_output=True,
        env={**os.environ, "ENVOY_BIN": str(broken_envoy)},
    )
    assert broken.returncode == 0
    assert broken.stdout == ""


def test_plugin_versions_match_cli_version():
    version = None
    for line in CLI_PYPROJECT.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            version = line.split("=", 1)[1].strip().strip('"')
            break
    assert version
    for manifest in (
        CLAUDE_PLUGIN / ".claude-plugin" / "plugin.json",
        CODEX_PLUGIN / ".codex-plugin" / "plugin.json",
    ):
        assert _load_json(manifest)["version"] == version


def test_marketplaces_point_at_both_plugin_packages():
    claude = _load_json(ROOT / ".claude-plugin" / "marketplace.json")
    assert claude["plugins"][0]["source"] == "./integrations/claude-code/envoy"
    codex = _load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    assert codex["plugins"][0]["source"] == {"source": "local", "path": "./integrations/codex/envoy"}



def test_codex_user_hook_install_is_idempotent_and_preserves_existing(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}),
        encoding="utf-8",
    )

    first = install_codex_hooks(home=home)
    assert first["installed"] is True and first["changed"] is True
    second = install_codex_hooks(home=home)
    assert second["changed"] is False

    data = _load_json(home / "hooks.json")
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo hi"
    entries = [
        entry for entry in data["hooks"]["UserPromptSubmit"]
        if "envoy-hook" in json.dumps(entry)
    ]
    assert len(entries) == 1
    wrapper = home / "envoy" / "hooks" / "envoy-hook.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111
    assert "prompt --host codex" in entries[0]["hooks"][0]["command"]


def test_codex_user_hook_uninstall_restores_clean_state(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    install_codex_hooks(home=home)
    removed = install_codex_hooks(home=home, uninstall=True)
    assert removed["changed"] is True
    assert not (home / "hooks.json").exists()


def test_codex_user_hook_install_refuses_malformed_config(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    path = home / "hooks.json"
    path.write_text("{not json\n", encoding="utf-8")

    result = install_codex_hooks(home=home)

    assert "error" in result
    assert result["changed"] is False
    assert result["installed"] is False
    # The user's file must not be silently replaced or backed up.
    assert path.read_text(encoding="utf-8") == "{not json\n"


def test_codex_user_hook_uninstall_preserves_sibling_hooks(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    path = home / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {"type": "command", "command": "echo mine"},
                                {
                                    "type": "command",
                                    "command": "sh /old/envoy-hook.sh prompt --host codex",
                                },
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    removed = install_codex_hooks(home=home, uninstall=True)

    assert removed["changed"] is True
    data = _load_json(path)
    surviving = data["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert [hook["command"] for hook in surviving] == ["echo mine"]


def test_codex_user_hook_install_refuses_malformed_hook_shape(tmp_path):
    home = tmp_path / "codex-home"
    home.mkdir()
    path = home / "hooks.json"
    original = json.dumps({"hooks": {"UserPromptSubmit": {"not": "a list"}}}) + "\n"
    path.write_text(original, encoding="utf-8")

    result = install_codex_hooks(home=home)

    assert "error" in result
    assert result["changed"] is False
    assert result["installed"] is False
    assert path.read_text(encoding="utf-8") == original
