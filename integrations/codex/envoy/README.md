# Envoy for Codex

Codex plugin that mounts the Envoy MCP reader and routes public links in user
prompts through Envoy before Codex's built-in web search/fetch.

Codex 0.156 no longer loads lifecycle hooks supplied by plugins, but it does
load user-level hooks from `~/.codex/hooks.json`. This integration therefore
uses two pieces:

- the plugin below for the MCP server (and future plugin-hook support);
- `envoy hook install --host codex` for the `UserPromptSubmit` hook, which
  copies the wrappers to `~/.codex/envoy/hooks/` and merges one idempotent entry
  into `~/.codex/hooks.json` without touching your other hooks.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex
```

It provisions an up-to-date `envoy`, adds this repository as a marketplace,
installs `envoy@envoy`, then runs `envoy hook install --host codex`. By hand:

```bash
codex plugin marketplace add https://github.com/regrevia/Envoy.git
codex plugin add envoy@envoy
envoy hook install --host codex   # user-level hook; safe to re-run
codex plugin list
```

For a local checkout pass the checkout directory to `plugin marketplace add`.

## What it adds

- **MCP**: starts `envoy mcp`, so the default catalog is
  `envoy_read`, `envoy_expand`, `envoy_search`, and `envoy_observe`.
  Export `ENVOY_MCP_TOOLSET=full` in the shell that launches Codex and restart
  (or use the CLI) for local video, discovery, collections, replay, visual
  history, and doctor tools.
- **`UserPromptSubmit` hook**: injects a short routing policy when the prompt
  contains a public `http(s)` URL:
  - call `envoy_read` first; for a local video or audio file use the exposed
    local-video tool or the `envoy read-local-video` CLI;
  - keep `material_id` and locators, continue with `envoy_expand` /
    `envoy_search`; use replay only when that tool is exposed;
  - fall back only on `blocked` / `challenge_required`, sign-in, CAPTCHA, or a
    missing Envoy tool, and explain the fallback;
  - treat screenshots/OCR as source evidence and trust the image over page text.

The policy does **not** replace Codex's native web search. Queries without a
link stay with the built-in search; Envoy handles links the user supplied,
video/audio, documents, and source material that needs full-evidence reading.

Localhost, loopback, private-network, and `.local` links are ignored. A prompt
containing `不要用 Envoy` / `do not use envoy` opts out.

## Configuration

| Variable | Meaning |
| --- | --- |
| `ENVOY_BIN` | Absolute path to the `envoy` executable when it is not on `PATH`. |
| `ENVOY_HOOK_LANGUAGE` | `auto` (default; Chinese prompt → `zh`, otherwise `en`), `zh`, or `en` for the injected policy text. |

The hook is fail-open: if `envoy` cannot be found, Codex continues normally.

## Verify

```bash
printf '%s' '{"prompt":"读一下 https://example.org/"}' | \
  sh ~/.codex/envoy/hooks/envoy-hook.sh prompt --host codex
```

The command prints `hookSpecificOutput.additionalContext` for a public link and
prints nothing otherwise. End-to-end, ask Codex to report whether it can see
`[envoy-link-routing]`; a routed prompt answers yes, and a linkless prompt
answers no.

## Layout

```
.codex-plugin/plugin.json   plugin manifest (MCP)
.mcp.json                   Envoy MCP stdio server
hooks/hooks.json            reference hook file for hosts that load plugin hooks
hooks/envoy-hook.sh|.ps1    portable Envoy launcher (installed to ~/.codex/envoy/hooks)
```
