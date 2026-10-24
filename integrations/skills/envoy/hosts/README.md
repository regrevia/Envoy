# Envoy Host Setup

See [`../INSTALL.md`](../INSTALL.md) for GitHub Skill and runtime installation.
The bundled MCP configuration assumes the `envoy` command is available on
`PATH`:

```json
{
  "mcpServers": {
    "envoy": {
      "command": "envoy",
      "args": ["mcp"]
    }
  }
}
```

## Link-first hooks

Envoy also ships host hooks that tell the model to try `envoy_read` before a
native web fetch whenever a user prompt contains a public URL. The shared policy
lives in `envoy hook prompt`; the host packages only wrap it. Claude Code loads
plugin hooks directly, DSH uses `agent/pre-step`, and Codex 0.156+ requires the
user-level install shown below because it no longer loads plugin-supplied
lifecycle hooks.

| Host | Install | Hook mechanism |
| --- | --- | --- |
| Claude Code | `claude plugin marketplace add regrevia/Envoy` then `claude plugin install envoy@envoy` | plugin `UserPromptSubmit` |
| Codex | `codex plugin marketplace add https://github.com/regrevia/Envoy.git`, `codex plugin add envoy@envoy`, then `envoy hook install --host codex` | user-level `~/.codex/hooks.json` `UserPromptSubmit` |
| DeepSeek Harness | install `integrations/dsh/envoy-dsh` as a bundle | Cordis `agent/pre-step` |

Inspect the hook without a host:

```bash
printf '%s' '{"prompt":"读一下 https://example.org/"}' | \
  envoy hook prompt --host codex
```

The hook ignores localhost/private URLs, honours an explicit “不要用 Envoy” /
“do not use envoy” opt-out, and fails open when Envoy is missing. Set
`ENVOY_BIN` to an absolute path if the host does not inherit your shell `PATH`.
The plugin does not hard-deny native web tools because interactive sign-in and
anti-bot challenges need a documented fallback.

After installing or updating Envoy, verify the runtime:

```bash
envoy browser setup
envoy doctor --json
```

The host configuration contains no API keys, cookies, browser paths, or model
configuration. The host is responsible for declaring image input capability in
an `envoy_read` request only when it can inspect URI image resources. Envoy's
own isolated Chromium profile is local to the CLI; it does not use or import
the host's browser profile. Public pages run headlessly, and a visible Envoy
window opens only when user login or verification is required. The browser
profile is private to the OS account (POSIX mode `0700`; Windows inherits the
user's home-directory ACL).
