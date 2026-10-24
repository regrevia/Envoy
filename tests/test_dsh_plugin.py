"""Tests for the DeepSeek Harness adaptation of Envoy."""

from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "integrations" / "dsh" / "envoy-dsh"
GENERATOR = ROOT / "scripts" / "build_envoy_dsh_plugin.py"
INDEX = PACKAGE_DIR / "index.js"
PATCH = PACKAGE_DIR / "cordis.patch.yml"

ENVOY_TOOLS = (
    "envoy_read",
    "envoy_read_local_video",
    "envoy_discover",
    "envoy_collection_checkpoint",
    "envoy_expand",
    "envoy_replay",
    "envoy_search",
    "envoy_doctor",
    "envoy_observe_visual",
    "envoy_visual_history",
)
BARE_TOOL = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(name) for name in ENVOY_TOOLS) + r")(?![\w-])"
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_declares_bundle_and_display_resources():
    package = _load_json(PACKAGE_DIR / "package.json")
    assert package["name"] == "envoy-dsh"
    assert package["private"] is True
    assert package["type"] == "module"
    assert package["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert package["exports"]["."] == "./index.js"
    assert package["exports"]["./locale/*.json"] == "./locale/*.json"
    assert package["exports"]["./icon"] == "./icon.png"

    files = package["files"]
    for required in (
        "index.js",
        "skill",
        "cordis.patch.yml",
        "locale/*.json",
        "icon.png",
        "README.md",
        "LICENSE",
        "NOTICE",
    ):
        assert required in files

    assert (PACKAGE_DIR / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert (PACKAGE_DIR / "NOTICE").read_bytes() == (ROOT / "NOTICE").read_bytes()


def test_patch_mounts_envoy_mcp_and_skill_provider():
    patch = PATCH.read_text(encoding="utf-8")
    assert "@deepseek-ai/dsh-mcp-client" in patch
    assert "serverName: envoy" in patch
    assert "transport: stdio" in patch
    assert "command: /bin/sh" in patch
    assert "exec envoy mcp" in patch
    assert "toolCallTimeoutMs: 300000" in patch
    assert "failOnStartupError: true" in patch
    assert "id: envoy-skill" in patch
    assert "name: 'envoy-dsh'" in patch


def test_host_plugin_has_no_harness_package_imports():
    index = INDEX.read_text(encoding="utf-8")
    assert "export const inject = ['skills']" in index
    assert "registerProvider" in index
    assert "BUNDLED_SKILL_RANK = 600" in index
    assert "from '@deepseek-ai" not in index
    assert 'from "@deepseek-ai' not in index


def test_generated_skill_is_current():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["stale"] == []
    assert payload["version"] == _load_json(PACKAGE_DIR / "package.json")["version"]


def test_generated_skill_uses_prefixed_tool_names_only():
    skill = (PACKAGE_DIR / "skill" / "envoy.md").read_text(encoding="utf-8")
    protocol = (
        PACKAGE_DIR / "skill" / "references" / "protocol.md"
    ).read_text(encoding="utf-8")

    assert skill.startswith("# Envoy Link Reader in DeepSeek Harness\n")
    assert not skill.startswith("---")
    compact_skill = " ".join(skill.split())
    assert "do not launch a second process" in compact_skill
    assert "envoy read --request-json -" in skill
    assert "envoy.read/1" in skill
    assert "mcp__envoy__" in protocol

    for tool in ENVOY_TOOLS:
        assert f"mcp__envoy__{tool}" in skill
    assert "mcp__envoy__envoy_read" in protocol
    assert "mcp__envoy__envoy_replay" in protocol

    for text in (skill, protocol):
        assert BARE_TOOL.search(text) is None, BARE_TOOL.search(text).group(0)


def test_locales_and_icon_are_safe_for_the_plugin_manager():
    en = _load_json(PACKAGE_DIR / "locale" / "en.json")["meta"]
    zh = _load_json(PACKAGE_DIR / "locale" / "zh.json")["meta"]
    assert en["title"] != zh["title"]
    assert "Envoy" in en["title"] and "Envoy" in zh["title"]
    assert en["description"] and zh["description"]

    icon = (PACKAGE_DIR / "icon.png").read_bytes()
    assert len(icon) <= 256 * 1024
    assert icon[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", icon[16:24])
    assert width <= 256 and height <= 256
    assert width == height


def test_package_version_matches_cli():
    version_match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (ROOT / "packages" / "envoy-cli" / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert version_match is not None
    version = version_match.group(1)
    assert _load_json(PACKAGE_DIR / "package.json")["version"] == version
    assert _load_json(PACKAGE_DIR / "skill" / "manifest.json")["version"] == version


def test_host_plugin_registers_and_serves_the_skill(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")

    script = tmp_path / "probe.mjs"
    script.write_text(
        """
import { pathToFileURL } from 'node:url'

const mod = await import(pathToFileURL(process.argv[2]).href)
let provider
const ctx = {
  skills: {
    registerProvider(create) {
      provider = create({ invalidate() {} })
      return () => {}
    },
  },
}
mod.apply(ctx)
const candidates = await provider.list()
const loaded = await provider.get(candidates[0])
console.log(JSON.stringify({
  inject: mod.inject,
  pluginName: mod.name,
  candidate: {
    name: candidates[0].name,
    rank: candidates[0].rank,
    source: candidates[0].source,
    provider: candidates[0].provider,
    resourceKind: candidates[0].resourceBase.kind,
  },
  heading: loaded.content.split('\\n')[0],
  hasReadTool: loaded.content.includes('mcp__envoy__envoy_read'),
}))
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(script), str(INDEX)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["inject"] == ["skills"]
    assert payload["pluginName"] == "envoy-dsh"
    assert payload["candidate"]["name"] == "envoy"
    assert payload["candidate"]["rank"] == 600
    assert payload["candidate"]["source"] == "bundled"
    assert payload["candidate"]["provider"] == "envoy-dsh"
    assert payload["candidate"]["resourceKind"] == "directory"
    assert payload["heading"] == "# Envoy Link Reader in DeepSeek Harness"
    assert payload["hasReadTool"] is True
