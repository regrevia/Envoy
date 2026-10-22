# Install Envoy

Envoy has two install parts: the Skill instructions and the local MCP runtime.
The runtime keeps link fetching and material processing on the user's machine.

## 1. Install the Skill

With the Skills CLI, from the current GitHub repository:

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
```

## 2. Install the runtime

After the first Envoy release is published, download its
`envoy_cli-*.whl` asset and install it with `uv`:

```bash
uv tool install ./envoy_cli-<version>-py3-none-any.whl
envoy browser setup
envoy doctor --json
```

The wheel contains the matching `envoy-cli` and `envoy-core` code. It does not
contain user configuration, API keys, cookies, browser profiles, or source
materials. `envoy browser setup` installs Chromium for Envoy's isolated
browser. Public pages are handled headlessly; a foreground Envoy browser opens
only when a page needs the user to log in or complete a normal verification.
The isolated profile is stored under `~/.envoy/browser-profile` (mode `0700`
on POSIX; on Windows it inherits the current user's home-directory ACL). It is
separate from personal browser profiles, is never sent to the model, and is
not exported to MCP requests.

## 3. Connect the MCP server

Add the contents of `hosts/mcp.json` to the selected agent's MCP configuration.
The command is `envoy` with argument `mcp`. Restart or reload the agent, then
confirm it can discover `envoy_read` and `envoy_doctor`.

The Skill installer does not install the runtime or edit host MCP settings.
Those steps are kept explicit so users can review what runs locally.

## Update or remove

Install the newer Envoy wheel with `uv tool install --force <wheel-path>`.
Remove the runtime with `uv tool uninstall envoy-cli`; remove the Skill using
the Skills CLI's uninstall command or the selected agent's Skill manager.

The standard runtime reads public static pages and documents, extracts public
timed subtitles when supported, hands images to capable hosts, and can pass
public direct-video URLs to hosts that advertise native video input. Platform
video download, local ASR, and budgeted frame extraction require the optional
media runtime and appropriate host capability.

Install local ASR support and prepare its default model with:

```bash
HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}" \
HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}" \
ALL_PROXY="${ALL_PROXY:-socks5h://127.0.0.1:7897}" \
uv tool install --with "openai-whisper==20250625" \
  ./envoy_cli-<release-version>-py3-none-any.whl
envoy asr setup --model base
envoy doctor --json
```

Use the same Envoy runtime for normal use and updates. To get pip's resumable
download retries for large dependencies, use a dedicated temporary virtual
environment:

```bash
python3.12 -m venv "$HOME/.local/share/envoy-asr"
HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}" \
HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7897}" \
ALL_PROXY="${ALL_PROXY:-socks5h://127.0.0.1:7897}" \
"$HOME/.local/share/envoy-asr/bin/python" -m pip install \
  --resume-retries 20 --retries 10 --timeout 60 \
  ./envoy_cli-<release-version>-py3-none-any.whl "openai-whisper==20250625"
"$HOME/.local/share/envoy-asr/bin/envoy" asr setup --model base
"$HOME/.local/share/envoy-asr/bin/envoy" doctor --json
```

Browser page reading is available in the standard wheel. Run
`envoy browser setup` once after installing or updating the CLI to install or
update Chromium.

Install FFmpeg and FFprobe with the operating system's package manager if they
are not already available.

Video downloads enforce byte budgets. Elapsed-work limits are checked between
extractor stages and during transfers; a site metadata request may last up to
its bounded socket timeout. Envoy does not read browser cookies or bypass login,
CAPTCHA, or anti-bot challenges. Direct media may still be rejected by its host
or access policy. Chromium and yt-dlp use the system HTTP(S) proxy when present;
set `ENVOY_BROWSER_PROXY` to an unauthenticated proxy URL to override Chromium's
proxy selection. Proxy credentials are not passed in browser command-line
arguments.

Whisper model downloads are checksum-verified before `doctor` reports a model
ready. An interrupted download stays in a `.part` checkpoint and
`envoy asr setup --model base` resumes it with HTTP Range when the model host
supports it; a complete cached model is simply checksum-verified and reused.
ASR works in bounded time chunks; if a request budget expires, returned
transcript ranges and timestamps stay explicit so the host can request only
uncovered ranges rather than treating partial transcription as complete.

For a repeatable multi-platform connectivity check, use the repository probe:

```bash
python scripts/probe_platform_urls.py --file urls.txt --timeout-seconds 30
```

It returns structured per-URL classifications such as `accepted`,
`auth_required`, `network_error`, and `unsupported`; a blocked URL does not
prevent the remaining URLs from being tested.
