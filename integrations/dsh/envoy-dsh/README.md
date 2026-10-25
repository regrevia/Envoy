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

## One link in, AI-ready material out

Paste a Douyin or Bilibili video or 图文 share link and Envoy returns
source-linked material — transcript, timestamps, frames, a `material_id` you
can quote — enough for the agent to write Markdown, a wiki page, or a
knowledge-base entry from what was actually said, not from the title. Articles,
PDFs, and audio links take the same path. For a whole feed, `envoy collect`
checkpoints every item, so an interrupted run resumes instead of restarting.

The same Envoy skill ships for Codex and Claude Code; this package is the
DeepSeek Harness build, and the deliverables are the same Markdown, wiki, or
knowledge-base material on all three hosts.

## Measured against reading the link without Envoy

Same links, same model, 20 sources × 3 questions, 95 turns scored:

| How the turn ended | Without Envoy | With Envoy |
| --- | ---: | ---: |
| Hands over the finished artifact | 23% | 58% |
| Never gets the content (asks you to log in, scan, authorize, or says it cannot read it) | 7% | 5% |
| Cut off at the time limit | 70% | 37% |

Time limits are 15 and 40 minutes, and a timed-out turn is not a failure.
Sample is 95 of 120 turns. Full data: [fixed evaluation set v1](../../../benchmarks/fixed-set-v1/README.md).

## Requirements

- DeepSeek Harness Desktop with the Plugin Manager.
- macOS or Linux. The patch uses `/bin/sh`; Windows users must replace the
  launcher with a PowerShell or `cmd.exe` equivalent.

Nothing else. The launcher uses an `envoy` already on PATH when there is one,
and otherwise installs the pinned runtime wheel into
`~/.local/share/envoy-dsh/runtime` on first use, with `uv` or with
`python3 -m venv`. The one case it cannot cover is a machine that has neither
`envoy` nor `uv` nor `python3`: the mount then fails with a single line telling
the user to install `uv`. Browser profiles, ASR, and OCR stay optional and
on-demand — see [INSTALL.md](../../skills/envoy/INSTALL.md) when a task needs
them.

## A visible window is not the agent's to open

Envoy reads headlessly. When a page needs the user's identity, the tool returns
`requires_human: true` inside `details.needs_user_action`; relay the question and
options to the user, stop, and wait for their answer. Do not set
`ENVOY_VISIBLE_BROWSER`, and do not run a command that opens a window. Visible
mode only works for a human at an interactive terminal (stdin and stdout both a
TTY, with explicit opt-in); the CLI refuses it under this plugin's stdio
transport, so a login prompt with no window on screen is expected.

## Install

```text
Plugin Manager → Install bundle → /absolute/path/to/Envoy/integrations/dsh/envoy-dsh
```

From a DeepSeek Harness agent session, the equivalent action is
`plugin_manager` with `action: install_bundle` and that absolute directory as
`target`. The Plugin Manager performs package linking and patch composition;
do not edit the profile's `package.json` or `cordis.patch.yml` by hand. The
runtime download happens on the first `envoy mcp` start, so the first link
read after installing is slower than the rest.

## Which profile did it land in

A bundle install targets one profile, so a machine with several profiles can
have Envoy in one and not the other. Confirm the profile of the session you are
actually using before concluding it is missing:

```bash
echo "$DSH_PROFILE"                                  # the profile this shell serves
readlink ~/.dsh/profiles/$DSH_PROFILE/node_modules/envoy-dsh
```

A symlink pointing at this directory means the repo copy is live and edits apply
without reinstalling. The `envoy` skill appearing in the skill catalog is the
same evidence seen from inside a session.

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

## Runtime resolution

The Harness GUI may not inherit a login shell's PATH, so the patch prepends the
usual uv and Homebrew entries and then takes the first of these that answers:

| Order | Source | When it applies |
|---|---|---|
| 1 | `ENVOY_BIN` | A pinned binary, including builds newer than the release wheel. |
| 2 | `envoy` on PATH | `uv tool install`, Homebrew, or a manual install. |
| 3 | `~/.local/share/envoy-dsh/runtime/bin/envoy` | Provisioned from the pinned release wheel on first start. |

Set `ENVOY_DSH_VERSION` to provision another released version. To point the
bridge at a source checkout or a development build, set `ENVOY_BIN` to its
absolute path. `toolCallTimeoutMs` is 300 seconds, above Envoy's default
180-second work budget.

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
- `envoy doctor --json` reports `0.1.8` and the expected capability catalog.
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
