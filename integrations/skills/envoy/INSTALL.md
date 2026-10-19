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

The standalone runtime currently reads public static pages and documents,
attempts public subtitle extraction for URLs with a specialized yt-dlp
extractor, hands images to capable hosts, and passes public direct-video URLs
to hosts that advertise native video input. Subtitle access varies by source;
it does not download platform video files or inspect video frames.
