# Envoy for DeepSeek Harness

This package is a DeepSeek Harness bundle for [Envoy](../../../README.md). It
does three things in one install:

1. mounts Envoy's stdio MCP bridge with `@deepseek-ai/dsh-mcp-client`;
2. registers an adapted Envoy Agent Skill on the public `ctx.skills` service;
3. listens to the `agent/pre-step` event and, on the first step of a turn whose
   user message contains a public link, prepends one short routing message.

The adaptation keeps Envoy's reading, discovery, expansion, visual handoff, and
capability behavior. It changes only the integration surface: the skill uses
the `mcp__envoy__*` tool names that DeepSeek Harness assigns to the mounted
server.

[中文快速安装](../README.md) · [Envoy 安装指南](../../skills/envoy/INSTALL.md)

## What one link gets you

Paste a Douyin or Bilibili share link — a 图文 note, a short video, a course
episode — and Envoy returns material with source locators: transcript text,
timestamps, frames, and a `material_id` you can quote from. Ask for a summary,
notes, or a 干货 writeup and the agent composes it from that material instead of
guessing from the page title. Articles, PDFs, and audio links go through the
same path. A batch job over many posts runs `envoy collect`, which checkpoints
every item, so an interrupted run resumes from where it stopped.

## Requirements

- DeepSeek Harness Desktop with the Plugin Manager.
- The Envoy CLI runtime, version `0.1.6` or a later compatible development
  build. Follow [INSTALL.md](../../skills/envoy/INSTALL.md) for the wheel,
  browser setup, and optional ASR/OCR dependencies.
- `envoy` reachable from one of the PATH entries in the launcher, or an
  absolute path configured as described below.
- macOS or Linux. The patch uses `/bin/sh`; Windows users must replace the
  launcher with a PowerShell or `cmd.exe` equivalent.

## Install

Install the Envoy runtime first, then install this directory as a bundle:

```text
Plugin Manager → Install bundle → /absolute/path/to/Envoy/integrations/dsh/envoy-dsh
```

From a DeepSeek Harness agent session, the equivalent action is
`plugin_manager` with `action: install_bundle` and that absolute directory as
`target`. The Plugin Manager performs package linking and patch composition;
do not edit the profile's `package.json` or `cordis.patch.yml` by hand.

The bundle inserts two rows:

| Row | Package | Purpose |
|---|---|---|
| `envoy-mcp` | `@deepseek-ai/dsh-mcp-client` | Starts `envoy mcp` (core toolset) and exposes four link tools as `mcp__envoy__*`. |
| `envoy-skill` | `envoy-dsh` | Registers the Envoy skill provider and the link-routing `agent/pre-step` hook. |

`core` exposes `envoy_read`, `envoy_expand`, `envoy_search`, and
`envoy_observe_visual`. The `full` toolset adds local-video, discovery,
collection, replay, visual-history, and doctor tools. Export
`ENVOY_MCP_TOOLSET=full` in the Harness environment and restart the bundle
when those are needed. Starting with
`core` keeps the MCP tool declarations, which enter every request prefix, small
for ordinary link reading. The choice is static for the process lifetime.

## Launcher PATH

The Harness GUI may not inherit a login shell's PATH, so the patch uses:

```sh
/bin/sh -c 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH; exec envoy mcp'
```

This covers `uv tool install` (`~/.local/bin`), Apple Silicon Homebrew
(`/opt/homebrew/bin`), and Intel Homebrew or manual installs
(`/usr/local/bin`). If `envoy` lives elsewhere, edit the `args` in
`cordis.patch.yml` to use its absolute path, then reinstall or let the Plugin
Manager reload the bundle. `toolCallTimeoutMs` is 300 seconds, above Envoy's
default 180-second work budget.

## Verify

After installation, check that:

- Plugin Manager shows **Envoy Link Reader** / **Envoy 链接阅读器** with the
  bundled bird icon.
- The agent's tool list contains `mcp__envoy__envoy_read`,
  `mcp__envoy__envoy_expand`, `mcp__envoy__envoy_search`, and
  `mcp__envoy__envoy_observe_visual`. The advanced tools appear only after a
  restart with `ENVOY_MCP_TOOLSET=full` in the Harness environment.
- The skill catalog contains `envoy`; loading it shows
  `# Envoy Link Reader in DeepSeek Harness` and `mcp__envoy__` tool names.
- `envoy doctor --json` reports `0.1.6` and the expected capability catalog.
  When the bridge runs with `full`, `mcp__envoy__envoy_doctor` is equivalent.
- On the **first step** of a turn that contains a public URL, the model sees one
  injected `[envoy-link-routing]` message. A linkless prompt, a follow-up step,
  a localhost/private URL, or a prompt containing `不要用 Envoy` gets no
  injection. The hook fails open: if anything throws, the original turn
  continues unchanged.

`failOnStartupError: true` makes a missing or broken runtime visible as a
failed `envoy-mcp` row instead of silently leaving the agent without the
tools. The rest of the profile remains usable.

## Layout

```text
integrations/dsh/envoy-dsh/
├── package.json           # bundle manifest and display exports
├── cordis.patch.yml       # MCP client + skill provider rows
├── index.js               # skill provider + agent/pre-step link hook
├── locale/{en,zh}.json    # Plugin Manager title and description
├── icon.png               # downscaled Envoy bird artwork
├── skill/
│   ├── envoy.md           # generated DSH skill body
│   ├── manifest.json      # generated name/description
│   └── references/
│       └── protocol.md    # generated protocol reference
├── README.md
├── LICENSE
└── NOTICE
```

`index.js` is hand-written and mirrors the shared policy in
`packages/envoy-cli/src/envoy_cli/hooks.py` (URL extraction, private-address
filtering, opt-out, six-point wording) without importing host internals. Keep
the two in sync when the policy changes; `tests/test_dsh_plugin.py` runs the
same parity corpus through both implementations.

The skill assets are generated from the canonical
`integrations/skills/envoy/SKILL.md` and
`integrations/skills/envoy/references/protocol.md`. Do not edit them by hand.

## Regenerate and test

```bash
python3 scripts/build_envoy_dsh_plugin.py
python3 scripts/build_envoy_dsh_plugin.py --check
pytest -q tests/test_dsh_plugin.py
```

The generator prefixes Envoy tool names, keeps the CLI commands unchanged,
replaces the generic “MCP-compatible host” launch section with the
DeepSeek Harness host description, and synchronizes the package version with
`packages/envoy-cli/pyproject.toml`.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). The icon is the Envoy
bird artwork from `frontend/public/brand/envoy-bird.png`, redistributed under
the same license.
