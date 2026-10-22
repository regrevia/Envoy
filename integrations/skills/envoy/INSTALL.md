# Install Envoy

Envoy has two install parts: the Skill instructions and the local MCP runtime.
The runtime keeps link fetching and material processing on the user's machine.

## 1. Install the Skill

With the Skills CLI, from the current GitHub repository:

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
```

## 2. Install the runtime

Download the matching wheel and checksums from GitHub Release **v0.1.6**.
With the GitHub CLI and `uv` already installed:

```bash
gh release download v0.1.6 --repo regrevia/Envoy --dir envoy-v0.1.6
cd envoy-v0.1.6
# macOS/Linux; on Windows compare Get-FileHash output with SHA256SUMS.
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.11 ./envoy_cli-0.1.6-py3-none-any.whl
envoy browser setup
envoy doctor --json
```

Without `gh`, download the same assets from
https://github.com/regrevia/Envoy/releases/tag/v0.1.6 and verify `SHA256SUMS`
before installation. Python 3.10+ is required; Python 3.11 is recommended.
The Skill alone is not a complete runtime installation.

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
Use an absolute path to the installed `envoy` executable if the host does not
inherit the terminal's PATH. Verify the newly connected MCP's `envoy_doctor`
version after reloading; an old connection can keep running the old process.

## 4. Check the first read

```bash
printf '%s' '{"protocol_version":"envoy.read/1","request_id":"install-check","source":{"url":"https://example.org/"}}' | envoy read --request-json -
```

This checks public HTML/network access, not video/ASR readiness. Network or
source restrictions must be reported rather than interpreted as an install
success. `envoy doctor --json` reports each optional capability separately.

## Update or remove

Install the newer Envoy wheel with `uv tool install --force <wheel-path>`.
If `envoy doctor --json` reports `runtime.asr_configured: true`, preserve the
Whisper engine while replacing the uv tool environment:

```bash
uv tool install --force --python 3.11 \
  --with 'openai-whisper==20250625' ./envoy_cli-<version>-py3-none-any.whl
envoy doctor --json
```

The ASR model and selected model name remain in the user data directories; the
Python engine is part of the replaceable tool environment. `doctor` flags a
configured-but-missing engine and provides a recovery command. If the selected
model is not available, rerun `envoy asr install --model <model>`.
Remove the runtime with `uv tool uninstall envoy-cli`; remove the Skill using
the Skills CLI's uninstall command or the selected agent's Skill manager.

The standard runtime reads public static pages and documents, extracts public
timed subtitles when supported, hands images to capable hosts, and can pass
public direct-video URLs to hosts that advertise native video input. Platform
video download, local ASR, and budgeted frame extraction require the optional
media runtime and appropriate host capability.

Install local ASR support and prepare its default model with:

```bash
envoy asr install --model base
envoy doctor --json
```

To explicitly test that Whisper can validate the selected local model and run
a short local inference,
run `envoy doctor --json --verify-asr`. This can take time and memory; routine
doctor checks do not load the model.

Envoy probes PyPI and regional indexes for both Whisper and PyTorch, chooses the
fastest reachable index, and retries through other reachable indexes if an
install attempt fails. Explicit proxy settings are honored. To use a specific
index, set `ENVOY_PYPI_INDEX_URL`; to provide an ordered fallback list, set
`ENVOY_PYPI_INDEXES` to comma-separated index URLs. `UV_INDEX_URL`,
`UV_DEFAULT_INDEX`, and `PIP_INDEX_URL` are also honored as explicit
overrides. Index URLs must not embed credentials; use the proxy's normal
environment configuration for private network access.

Browser page reading is available in the standard wheel. Run
`envoy browser setup` once after installing or updating the CLI to install or
update Chromium.

Install FFmpeg and FFprobe with the operating system's package manager if they
are not already available.

Optional image OCR keeps the base runtime light. Install the Python image
helper and either PaddleOCR or the system Tesseract executable:

```bash
uv tool install --force --python 3.11 \
  --with 'Pillow>=10,<13' ./envoy_cli-<version>-py3-none-any.whl
```

PaddleOCR is preferred for Chinese documents and complex layouts; Tesseract is
the lightweight fallback. `envoy doctor --json` reports detected OCR backends.
OCR is only run when the request supplies `ocr_profile`; it returns source-bound
regions and does not replace host visual inspection.

Video downloads enforce byte budgets. Elapsed-work limits are checked between
extractor stages and during transfers; a site metadata request may last up to
its bounded socket timeout. Envoy does not read browser cookies or bypass login,
CAPTCHA, or anti-bot challenges. Direct media may still be rejected by its host
or access policy. Chromium and yt-dlp use the system HTTP(S) proxy when present;
set `ENVOY_BROWSER_PROXY` to an unauthenticated proxy URL to override Chromium's
proxy selection. Proxy credentials are not passed in browser command-line
arguments.
Only use configured or user-approved trusted proxies. Never search for or use
public proxy lists. A 404 alone does not establish deletion, region blocking,
or a proxy problem: ask whether the user can open the link, then diagnose.

## Scope and license

Envoy extracts evidence; the host model selects, interprets, and produces the
answer. Sparse frames, chapter summaries, and missing ASR never imply full video
coverage. Xiaohongshu image-note routing is experimental and depends on a
publicly readable note/gallery; it is not a promise of platform-wide access.
Douyin discovery is experimental: canonical `/user/...` URLs, up to 100 saved
candidates, snapshot pagination, and no date-range filter. Whole-batch budgets
still require host checkpoint accounting; they are not an atomic reservation.

Envoy's original code and Skill are Apache-2.0; see `LICENSE` and `NOTICE`.
Third-party dependencies and optional model weights retain their own licenses.
Report installation or reading failures at https://github.com/regrevia/Envoy/issues
with version, host/OS, stage, error code, and redacted diagnostics. Never attach
cookies, credentials, private files, signed media URLs, or browser profiles.

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
