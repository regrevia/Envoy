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


def _run_node_probe(tmp_path: Path, name: str, source: str) -> dict:
    """Import the host plugin in node and return the probe's JSON payload."""

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")

    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [node, str(script), str(INDEX)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


PROBE_HEADER = """
import { pathToFileURL } from 'node:url'
const mod = await import(pathToFileURL(process.argv[2]).href)
"""


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
    assert "exec envoy mcp --toolset core" in patch
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
const listeners = []
const ctx = {
  skills: {
    registerProvider(create) {
      provider = create({ invalidate() {} })
      return () => {}
    },
  },
  on(event, listener) {
    listeners.push({ event, listener })
    return () => {}
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


# ---------------------------------------------------------------------------
# Link routing: the host-side mirror of envoy_cli.hooks
# ---------------------------------------------------------------------------


def test_link_routing_extraction_matches_the_shared_module(tmp_path):
    payload = _run_node_probe(
        tmp_path,
        "extract.mjs",
        PROBE_HEADER
        + """
const corpus = [
  '读 https://example.com/a?b=1。再看 https://example.org/x，最后 https://example.net/y',
  '(see https://en.wikipedia.org/wiki/Foo_(bar)).',
  'https://example.com/x).',
  '看看这个链接：https://example.com/news?id=1，说的什么',
  'https://example.com/a https://EXAMPLE.com/a https://example.org/b',
  '[x](https://example.com/x)',
]
const local = 'https://localhost:3000/a https://127.0.0.1/a https://0.0.0.0/a '
  + 'https://10.0.0.2/a https://172.16.4.4/a https://192.168.1.2/a '
  + 'https://169.254.1.1/a https://printer.local/a https://intranet/a '
  + 'https://[::1]/a ftp://example.com/a'
console.log(JSON.stringify({
  extracted: corpus.map((text) => mod.inspectPrompt(text).urls),
  local: mod.inspectPrompt(local),
  optOut: mod.inspectPrompt('不要用 Envoy，直接读 https://example.com/a'),
  aliases: mod.extractPublicUrls === mod.detectPublicUrls,
}))
""",
    )
    assert payload["extracted"] == [
        ["https://example.com/a?b=1", "https://example.org/x", "https://example.net/y"],
        ["https://en.wikipedia.org/wiki/Foo_(bar)"],
        ["https://example.com/x"],
        ["https://example.com/news?id=1"],
        ["https://example.com/a", "https://example.org/b"],
        ["https://example.com/x"],
    ]
    assert payload["local"]["urls"] == []
    assert payload["local"]["shouldRoute"] is False
    assert payload["optOut"]["urls"] == ["https://example.com/a"]
    assert payload["optOut"]["optedOut"] is True
    assert payload["optOut"]["shouldRoute"] is False
    assert payload["aliases"] is True


def test_link_routing_policy_covers_the_envoy_contract(tmp_path):
    payload = _run_node_probe(
        tmp_path,
        "policy.mjs",
        PROBE_HEADER
        + """
console.log(JSON.stringify({
  marker: mod.LINK_ROUTING_MARKER,
  sourceKind: mod.LINK_ROUTING_SOURCE_KIND,
  zh: mod.buildRoutingText(['https://example.com/a'], 'zh'),
  en: mod.buildRoutingText(['https://example.com/a'], 'en'),
}))
""",
    )
    assert payload["marker"] == "[envoy-link-routing]"
    assert payload["sourceKind"] == "envoy-link-routing"

    zh = payload["zh"]
    en = payload["en"]
    for text in (zh, en):
        assert text.startswith("[envoy-link-routing]")
        assert "https://example.com/a" in text
        # Envoy first, with the exact MCP tool names the Harness exposes.
        assert "mcp__envoy__envoy_read" in text
        assert "mcp__envoy__envoy_read_local_video" in text
        # Advanced tools are explicitly qualified by what the toolset exposes.
        assert "toolset exposes it" in text or "工具集暴露" in text
        assert "envoy read-local-video" in text
        # Keep provenance and widen with bounded reads instead of re-fetching.
        assert "material_id" in text
        assert "coverage" in text
        assert "mcp__envoy__envoy_expand" in text
        assert "mcp__envoy__envoy_search" in text
        assert "mcp__envoy__envoy_replay" in text
        assert "token" in text
        # Fallback is only for explicit Envoy failures.
        assert "blocked" in text
        assert "challenge_required" in text
        # Private destinations and opt-out stay with the host.
        assert "localhost" in text

    # Envoy must not be presented as a replacement for native web search.
    assert "不替代宿主的联网搜索" in zh
    assert "没有给链接的检索仍用宿主搜索工具" in zh
    assert "视频" in zh
    assert "以图片为准" in zh
    assert "不用 Envoy" in zh
    assert "节省 token" in zh

    assert "not a replacement for the host's web search" in en
    assert "native search" in en
    assert "video/audio" in en
    assert "Images and OCR" in en
    assert "opts out" in en


def test_pre_step_hook_injects_envoy_routing_once(tmp_path):
    payload = _run_node_probe(
        tmp_path,
        "inject.mjs",
        PROBE_HEADER
        + """
const listeners = []
const ctx = {
  skills: { registerProvider() { return () => {} } },
  on(event, listener) {
    listeners.push({ event, listener })
    return () => {}
  },
}
mod.apply(ctx)
const listener = listeners.find((entry) => entry.event === 'agent/pre-step')?.listener
const user = {
  id: 'u1',
  role: 'user',
  content: [{ type: 'text', text: '看看 https://example.com/a?b=1 讲了什么' }],
  source: { kind: 'user' },
}
const payload = { agent: {}, messages: [user], turn: 1, step: 1, signal: { aborted: false } }
const decision = { kind: 'enter', messages: [user] }
const out = await listener(payload, async () => decision)
const injected = out.messages[0]
const second = await listener(payload, async () => out)
console.log(JSON.stringify({
  registered: listeners.map((entry) => entry.event),
  role: injected.role,
  sourceKind: injected.source.kind,
  text: injected.content[0].text,
  ids: out.messages.map((message) => message.id),
  payloadMessages: payload.messages.length,
  decisionMessages: decision.messages.length,
  sameDecision: out === decision,
  secondUnchanged: second === out,
}))
""",
    )
    assert payload["registered"] == ["agent/pre-step"]
    assert payload["role"] == "user"
    assert payload["sourceKind"] == "envoy-link-routing"
    assert payload["text"].startswith("[envoy-link-routing]")
    assert "https://example.com/a?b=1" in payload["text"]
    assert "mcp__envoy__envoy_read" in payload["text"]
    # The notice is prepended; the claimed user message is preserved verbatim.
    assert payload["ids"][0] != "u1"
    assert payload["ids"][1] == "u1"
    # Neither the payload nor the incoming decision is mutated.
    assert payload["payloadMessages"] == 1
    assert payload["decisionMessages"] == 1
    assert payload["sameDecision"] is False
    # A re-dispatched step must not stack a second notice.
    assert payload["secondUnchanged"] is True


def test_pre_step_hook_leaves_ineligible_turns_untouched(tmp_path):
    payload = _run_node_probe(
        tmp_path,
        "noop.mjs",
        PROBE_HEADER
        + """
const listeners = []
mod.apply({
  skills: { registerProvider() { return () => {} } },
  on(event, listener) {
    listeners.push({ event, listener })
    return () => {}
  },
})
const listener = listeners[0].listener
const message = (id, text) => ({
  id,
  role: 'user',
  content: [{ type: 'text', text }],
  source: { kind: 'user' },
})
const routing = mod.createRoutingUserMessage(['https://example.com/a'])
const unchanged = async ({ step = 1, messages, decisionMessages, aborted = false }) => {
  const payload = { agent: {}, messages, turn: 1, step, signal: { aborted } }
  const decision = { kind: 'enter', messages: decisionMessages }
  const out = await listener(payload, async () => decision)
  return out === decision
}
const linked = () => message('u2', 'read https://example.com/a')
console.log(JSON.stringify({
  noUrl: await unchanged({
    messages: [message('u1', 'hello there')],
    decisionMessages: [message('u1', 'hello there')],
  }),
  laterStep: await unchanged({
    step: 2,
    messages: [linked()],
    decisionMessages: [linked()],
  }),
  alreadyInjected: await unchanged({
    messages: [routing, linked()],
    decisionMessages: [routing, linked()],
  }),
  optedOutZh: await unchanged({
    messages: [message('u3', '不要用 Envoy，读 https://example.com/a')],
    decisionMessages: [message('u3', '不要用 Envoy，读 https://example.com/a')],
  }),
  optedOutEn: await unchanged({
    messages: [message('u4', 'do not use envoy, read https://example.com/a')],
    decisionMessages: [message('u4', 'do not use envoy, read https://example.com/a')],
  }),
  optedOutNoSpace: await unchanged({
    messages: [message('u4b', '不要用Envoy，读 https://example.com/a')],
    decisionMessages: [message('u4b', '不要用Envoy，读 https://example.com/a')],
  }),
  localOnly: await unchanged({
    messages: [message('u5', 'open http://localhost:5173/a')],
    decisionMessages: [message('u5', 'open http://localhost:5173/a')],
  }),
  emptyDecision: await unchanged({ messages: [linked()], decisionMessages: [] }),
  aborted: await unchanged({ messages: [linked()], decisionMessages: [linked()], aborted: true }),
}))
""",
    )
    assert all(payload.values()), payload


def test_pre_step_hook_handles_multipart_content_and_picks_language(tmp_path):
    payload = _run_node_probe(
        tmp_path,
        "multipart.mjs",
        PROBE_HEADER
        + """
const listeners = []
mod.apply({
  skills: { registerProvider() { return () => {} } },
  on(event, listener) {
    listeners.push({ event, listener })
    return () => {}
  },
})
const listener = listeners[0].listener
const run = async (content) => {
  const user = { id: 'u1', role: 'user', content, source: { kind: 'user' } }
  const decision = { kind: 'enter', messages: [user] }
  const out = await listener(
    { agent: {}, messages: [user], turn: 1, step: 1, signal: {} },
    async () => decision,
  )
  return out.messages[0].content[0].text
}
console.log(JSON.stringify({
  zh: await run([
    { type: 'text', text: '请读 https://example.com/a 并总结' },
    { type: 'text', text: '重点看结论' },
  ]),
  en: await run('read https://example.org/b and summarise'),
}))
""",
    )
    assert "https://example.com/a" in payload["zh"]
    assert "以图片为准" in payload["zh"]  # Chinese prompt -> zh policy
    assert "https://example.org/b" in payload["en"]
    assert "not a replacement for the host's web search" in payload["en"]


def test_link_routing_skips_turns_that_already_route_themselves(tmp_path):
    """A prompt that names an Envoy tool or the plugin needs no policy notice."""

    payload = _run_node_probe(
        tmp_path,
        "self-routing.mjs",
        PROBE_HEADER
        + """
const prompts = [
  '看看 https://example.com/a 讲什么',
  '用 envoy_read 检查 https://example.com/a 能否读取',
  'restart envoy-dsh then open https://example.com/b',
]
console.log(JSON.stringify(prompts.map((text) => mod.routableUrls(text))))
""",
    )
    assert payload == [
        ["https://example.com/a"],
        [],
        [],
    ]
