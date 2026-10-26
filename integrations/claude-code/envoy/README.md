# Envoy for Claude Code

Claude Code plugin that mounts the Envoy MCP reader and routes public links in
user prompts through Envoy before Claude's built-in web fetch.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code
```

That is the whole flow: it provisions an up-to-date `envoy` if you do not have
one, adds this repository as a marketplace, installs `envoy@envoy`, and reminds
you to restart. To run the steps by hand:

```bash
claude plugin marketplace add regrevia/Envoy
claude plugin install envoy@envoy
```

For a local checkout use `claude plugin marketplace add /absolute/path/to/Envoy`
first. If `envoy` lives outside PATH, set `ENVOY_BIN` to its absolute path before
starting Claude Code.

## What it adds

- **MCP**: starts `envoy mcp`, so the default catalog is
  `envoy_read`, `envoy_expand`, `envoy_search`, and `envoy_observe`.
  Export `ENVOY_MCP_TOOLSET=full` in the shell that launches Claude Code and
  restart (or use the CLI) for local video, discovery, collections, replay,
  visual history, and doctor tools.
- **`UserPromptSubmit` hook**: when the prompt contains a public `http(s)` URL,
  it injects a short, deterministic policy:
  - try `envoy_read` first; for a local video or audio file use the exposed
    local-video tool or the `envoy read-local-video` CLI;
  - keep `material_id` and locators, continue with `envoy_expand` /
    `envoy_search`; use replay only when that tool is exposed;
  - fall back to built-in fetching only on `blocked` / `challenge_required`,
    sign-in, CAPTCHA, or a missing Envoy tool;
  - treat screenshots/OCR as source evidence and trust the image when page text
    and the image disagree.

Localhost, loopback, private-network, and `.local` links are ignored. A prompt
containing `不要用 Envoy` / `do not use envoy` also opts out.

## Configuration

| Variable | Meaning |
| --- | --- |
| `ENVOY_BIN` | Absolute path to the `envoy` executable when it is not on `PATH`. |
| `ENVOY_HOOK_LANGUAGE` | `auto` (default; Chinese prompt → `zh`, otherwise `en`), `zh`, or `en` for the injected policy text. |

To disable routing without uninstalling the plugin, disable the plugin in
`/plugin`. The MCP server remains available when only the hook is disabled.

## Verify

```bash
printf '%s' '{"prompt":"读一下 https://example.org/"}' | \
  envoy hook prompt --host claude-code
```

The command prints JSON with `hookSpecificOutput.additionalContext` when a
public link is present, and prints nothing otherwise. The hook fails open if
Envoy is not installed.

## Layout

```
.claude-plugin/plugin.json   plugin manifest
.mcp.json                    Envoy MCP stdio server
hooks/hooks.json             UserPromptSubmit hook
hooks/envoy-hook.sh|.ps1     portable Envoy launcher
```
