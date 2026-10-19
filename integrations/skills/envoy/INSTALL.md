# Install Envoy

Envoy has two install parts: the Skill instructions and the local MCP runtime.
The runtime keeps link fetching and material processing on the user's machine.

## 1. Install the Skill

With the Skills CLI, from the current GitHub repository:

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
```

## 2. Install the runtime

After the first GitHub Release is published, download its
`envoy_cli-*.whl` asset and install it with `uv`:

```bash
uv tool install ./envoy_cli-<version>-py3-none-any.whl
envoy doctor --json
```

The wheel contains the matching `envoy-cli` and `envoy-core` code. It does not
contain user configuration, API keys, cookies, browser profiles, or source
materials.

## 3. Connect the MCP server

Add the contents of `hosts/mcp.json` to the selected agent's MCP configuration.
The command is `envoy` with argument `mcp`. Restart or reload the agent, then
confirm it can discover `envoy_read` and `envoy_doctor`.

The Skill installer does not install the runtime or edit host MCP settings.
Those steps are kept explicit so users can review what runs locally.

## Update or remove

Install the newer Release wheel with `uv tool install --force <wheel-path>`.
Remove the runtime with `uv tool uninstall envoy-cli`; remove the Skill using
the Skills CLI's uninstall command or the selected agent's Skill manager.

The standard runtime reads public static pages and documents, extracts public
timed subtitles when supported, hands images to capable hosts, and can pass
public direct-video URLs to hosts that advertise native video input. Platform
video download, local ASR, and budgeted frame extraction require the optional
media runtime and appropriate host capability.

Install local ASR support and prepare its default model with:

```bash
uv tool install --force --with "openai-whisper==20250625" ./envoy_cli-<version>-py3-none-any.whl
envoy asr setup --model base
envoy doctor --json
```

Install FFmpeg and FFprobe with the operating system's package manager if they
are not already available.

Video downloads enforce byte budgets. Elapsed-work limits are checked between
extractor stages and during transfers; a site metadata request may last up to
its bounded socket timeout. Envoy does not read browser cookies or bypass login,
CAPTCHA, or anti-bot challenges. Direct media may still be rejected by its host
or access policy.
