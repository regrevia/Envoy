# Host integrations

Envoy ships one runtime and three thin host adapters. They share the same
routing policy (`envoy hook prompt`) and the same MCP server (`envoy mcp`), so
behaviour stays consistent across agents. All bundled MCP configs launch it
as `envoy mcp`, which declares the bounded `core` toolset by default; export
`ENVOY_MCP_TOOLSET=full` before the host starts the bridge when local-video,
discovery, collection, replay, visual-history, or doctor tools are needed.

| Host | Package | Mechanism |
| --- | --- | --- |
| Claude Code | [`claude-code/envoy`](claude-code/envoy/README.md) | plugin manifest, `UserPromptSubmit` hook, `.mcp.json` |
| Codex | [`codex/envoy`](codex/envoy/README.md) | plugin manifest + `.mcp.json`; `envoy hook install` writes the user-level `UserPromptSubmit` hook to `~/.codex/hooks.json` |
| DeepSeek Harness | [`dsh/envoy-dsh`](dsh/envoy-dsh/README.md) | Cordis bundle, `agent/pre-step` hook, MCP bridge |

One line per host. It puts an up-to-date `envoy` on PATH (reusing one that is
new enough) and registers the plugin, so there is no separate runtime step:

```bash
# Claude Code
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code

# Codex (plugin for MCP, plus the user-level hook 0.156+ needs)
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex

# DeepSeek Harness
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh
```

`all` installs every host whose CLI is on PATH. The same commands without
`curl | sh` are listed in each host README below when you would rather read
them before running them.

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
