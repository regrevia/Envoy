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

Whatever you are looking at, hand it over and get the thing you wanted out:
Markdown, a wiki page, a knowledge-base entry. Douyin, Bilibili, Xiaohongshu,
Zhihu, WeChat posts, articles, PDFs and podcasts all take the same path. A read
returns a source-linked transcript with timestamps, frames, and a `material_id`
you can quote, so the agent writes from what was actually said, not from the title. For a whole feed,
`envoy collect` checkpoints every item, so an interrupted run resumes instead
of restarting.

Measured on 92 live links across those platforms: **75% usable as source
material** (20 fully readable, 49 partially, the gap named), **0 failures**.
The harness is in `docs/experiments/link-compatibility.md`; rerun it with
`scripts/acceptance/link_coverage.py`.

The same Envoy skill ships for Codex and Claude Code; this package is the
DeepSeek Harness build, and the deliverables are the same Markdown, wiki, or
knowledge-base material on all three hosts.

## What changes when it is installed

Same model, same links. The difference is what a read puts in front of the
agent.

| Ask | Harness on its own | With this bundle |
| --- | --- | --- |
| Long article, WeChat post, Zhihu column | Truncated text, or only the digest | Body text with headings and source locators |
| PDF | First pages, or a screenshot of the page | Per-page text with page numbers and locators |
| Video (Douyin, Bilibili) | Title, cover, description, danmaku | Bound player file; captions or ASR with a coverage figure; frames when the host declares it can show them |
| Audio | Nothing usable | Timestamped transcript; low confidence and no-speech are stated, not hidden |
| Login wall | 403, empty body, or a captcha page | `challenge_required` plus the human step: import cookies for one site scope, or sign in once |
| Unreadable page | A plausible guess | `partial` with the part that was read, and the gap named |

The turn-level A/B we ran is exploratory, not a claim: the two arms had
different time limits (900 s and 2400 s) and one arm was graded terminal-only.
Article-level citation counts came out level within noise over 60 turns per arm.
What this bundle guarantees is the per-read behaviour above, and the tests
assert it.

## What stays the same without the bundle

Reading a link without Envoy still works. What the bundle adds is not a new
capability the model lacks, but a bounded one: declared visual input, a
retained material you can search instead of re-fetching, an explicit gap when
a source cannot be read, and a human step instead of an executable command
when the page needs one.

## Requirements

- DeepSeek Harness with the Plugin Manager (`engines.dsh` is `>=0.1.5`).
- macOS or Linux today. The launcher runs under `/bin/sh`; a Windows launcher
  is not shipped yet, so Windows is not supported.

Nothing else. No separate CLI install, no `envoy browser setup`, no Python
preflight: on first use the bundle provisions the pinned runtime wheel into
`~/.local/share/envoy-dsh/runtime` with `uv` or `python3 -m venv`, and the
isolated Chromium is installed the first time a page actually needs a browser.
An `envoy` already on PATH is used as-is when it is new enough. The one machine
it cannot cover has neither `envoy` nor `uv` nor `python3`; the mount then fails
with a single line naming `uv`. Browser profiles, ASR, and OCR stay optional and
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

In the app: Plugin Manager → search `Envoy` → Install. That is the whole flow —
the bundle ships its own Harness patch, so nothing in the profile is edited by
hand.

From a shell, one command (it finds the CLI inside the desktop app when `dsh`
is not on PATH):

```bash
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh
```

Without the script it is the same single command, optionally with
`--profile <name>`:

```text
dsh plugin --profile web add envoy-dsh
```

A release tarball (`envoy-dsh-<version>.tgz` on the GitHub release) installs
with the same command and does not wait on the npm registry, which publishes
separately.

Developing against a checkout instead: Plugin Manager → Install bundle →
`/absolute/path/to/Envoy/integrations/dsh/envoy-dsh`, or `plugin_manager` with
`action: install_bundle` and that directory as `target`.

The first link read is slower than the rest: that is the one-time runtime
download, plus the Chromium install when the page needs a browser. Set
`ENVOY_DISABLE_AUTO_BROWSER_SETUP=1` before Harness starts to keep the browser
install manual (`envoy browser setup`) instead.

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
`envoy_observe`. The `full` toolset adds local-video, discovery,
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
  `mcp__envoy__envoy_observe`. The advanced tools appear only after a
  restart with `ENVOY_MCP_TOOLSET=full` in the Harness environment.
- The skill catalog contains `envoy`; loading it shows
  `# Envoy Link Reader in DeepSeek Harness` and `mcp__envoy__` tool names.
- `envoy doctor --json` reports `0.1.9` and the expected capability catalog.
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
