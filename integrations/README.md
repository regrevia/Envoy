# Host integrations

Envoy ships one runtime and three thin host adapters. They share the same
routing policy (`envoy hook prompt`) and the same MCP server (`envoy mcp`), so
behaviour stays consistent across agents. All bundled MCP configs launch it
with `--toolset core`; use `--toolset full` when local-video, discovery,
collection, replay, visual-history, or doctor tools are needed.

| Host | Package | Mechanism |
| --- | --- | --- |
| Claude Code | [`claude-code/envoy`](claude-code/envoy/README.md) | plugin manifest, `UserPromptSubmit` hook, `.mcp.json` |
| Codex | [`codex/envoy`](codex/envoy/README.md) | plugin manifest + `.mcp.json`; `envoy hook install` writes the user-level `UserPromptSubmit` hook to `~/.codex/hooks.json` |
| DeepSeek Harness | [`dsh/envoy-dsh`](dsh/envoy-dsh/README.md) | Cordis bundle, `agent/pre-step` hook, MCP bridge |

The two coding-agent plugins can be installed directly from this repository:

```bash
# Claude Code
claude plugin marketplace add regrevia/Envoy
claude plugin install envoy@envoy

# Codex
codex plugin marketplace add https://github.com/regrevia/Envoy.git
codex plugin add envoy@envoy
envoy hook install --host codex   # Codex 0.156+ loads hooks at user level only
```

The repository root contains the marketplace entry points:

- `.claude-plugin/marketplace.json` → `integrations/claude-code/envoy`
- `.agents/plugins/marketplace.json` → `integrations/codex/envoy`

## Shared policy

`envoy hook prompt --host <claude-code|codex|dsh>` reads the host hook payload
on stdin and emits the host's `UserPromptSubmit` JSON. `envoy hook install
--host codex` installs the same hook into Codex's user-level `hooks.json`. It
only routes prompts that contain a public `http(s)` URL, ignores
localhost/private addresses, and fails open when Envoy is unavailable. DeepSeek
Harness applies the same policy in-process through its `agent/pre-step` event.
The notice language is `auto` by default (Chinese prompts get `zh`, others
`en`) unless `ENVOY_HOOK_LANGUAGE` overrides it.

The policy deliberately does **not** hard-deny native web tools. A hard block
would break pages that Envoy cannot read (interactive login, anti-bot
challenges); instead the model is told to try Envoy first and explain any
fallback. Set `ENVOY_BIN` when `envoy` is not on `PATH`.

## Development

```bash
pytest -q tests/test_link_routing_hooks.py tests/test_dsh_plugin.py
python3 scripts/build_envoy_dsh_plugin.py --check
```
