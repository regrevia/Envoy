# 安装 Envoy

Envoy 分为 Skill 指令、本地 CLI runtime 和宿主 MCP 连接三部分。按以下步骤完成后，AI 才能实际调用 Envoy 读取链接。当前开发版为 `0.1.12`，发行说明以 `CHANGELOG.md` 为准。

## 1. 安装 Skill

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
```

## 2. 安装本地 runtime

从 GitHub Release 下载与版本匹配的 wheel 和校验文件。以下以 `v0.1.9` 为例：

```bash
gh release download v0.1.9 --repo regrevia/Envoy --dir envoy-v0.1.9
cd envoy-v0.1.9
# macOS/Linux
shasum -a 256 -c SHA256SUMS
# Windows PowerShell: 使用 Get-FileHash 对照 SHA256SUMS
uv tool install --python 3.11 ./envoy_cli-0.1.9-py3-none-any.whl
envoy browser setup
envoy doctor --json
```

需要预先安装 GitHub CLI 和 `uv`。没有 `gh` 时，可从
[GitHub Releases](https://github.com/regrevia/Envoy/releases/tag/v0.1.9)
下载相同文件并核对 `SHA256SUMS`。Python 3.10+ 可用，推荐 Python 3.11。
只安装 Skill 不包含 runtime，也不会自动修改宿主配置。

## 3. 连接 MCP

将 `hosts/mcp.json` 中的配置合并到所用 Agent 的 MCP 设置。默认配置以 `envoy`
为命令、`mcp` 为唯一参数，因此只声明 `envoy_read`、`envoy_expand`、
`envoy_search` 和 `envoy_observe`。重启或重载 Agent 后，确认其能发现这
四个工具；需要本地视频、发现、合集、回放、视觉历史或诊断工具时，在启动宿主的
环境里导出 `ENVOY_MCP_TOOLSET=full` 并重启（也可临时改用 `--toolset full`）。

本机文件不需要上传：`envoy_read` 的 `source.url` 可以直接是绝对路径或 `file://`
URL，视频会用本机 Whisper 转写。同一链接重复读取会复用本会话已保留的物料（不发
网络请求），确实要重抓时传 `refresh: true`；重抓遇到登录或验证时同样回退到保留
物料，并在 `notices` 里说明原因。每条读取结果文本都带 `material_id` 与
`source_revision`，`content` 与 `structuredContent` 都带正文、命中片段和资源描述（DSH 只读前者，Codex / Claude Code 只读后者），用 `envoy_search` / `envoy_expand` 接着读即可。本服务不声明 MCP resources 能力，
`list_mcp_resources` 为空是设计如此。

若宿主找不到命令，请在 MCP 配置中使用已安装 `envoy` 的绝对路径。重载后用
`envoy doctor --json` 检查版本（`full` 下也可调用 `envoy_doctor`）；旧 MCP
进程可能仍在运行。

DeepSeek Harness Desktop 用户可在插件管理器中安装本仓库的 [Envoy DSH 插件](../../dsh/README.md)，一次完成 MCP 桥接、Skill 注册和链接优先 hook；该方式仍要求先安装上面的 runtime。

### Claude Code 与 Codex 插件

Claude Code 插件直接提供 MCP 桥接和 `UserPromptSubmit` 链接优先 hook。
Codex 0.156 起不再加载插件自带的生命周期 hook，因此 Codex 插件只负责 MCP，
hook 需再执行一次 `envoy hook install --host codex` 写入用户级
`~/.codex/hooks.json`（该子命令自 `0.1.7` 起提供，`0.1.6` 没有；本页步骤安装的
`0.1.9` 已包含）。两者都会先尝试 `envoy_read`，只在 Envoy 返回
`blocked` / `challenge_required`、需要登录/验证码或工具不可用时才回退到宿主
自带抓取。

```bash
# Claude Code
claude plugin marketplace add regrevia/Envoy
claude plugin install envoy@envoy

# Codex：插件安装 MCP，另外安装用户级 hook
codex plugin marketplace add https://github.com/regrevia/Envoy.git
codex plugin add envoy@envoy
envoy hook install --host codex
codex plugin list
```

Codex 首次运行会要求确认 hook 信任；`codex exec` 可用
`--dangerously-bypass-hook-trust`，但仅限你理解该参数含义时。

本地开发时把仓库绝对路径传给 `plugin marketplace add`。若 `envoy` 不在
`PATH` 中，设置 `ENVOY_BIN` 为可执行文件绝对路径；hook 在找不到 Envoy 时会
静默跳过，不影响正常提问。详见
[Claude Code 插件](../../claude-code/envoy/README.md)和
[Codex 插件](../../codex/envoy/README.md)。

## 4. 验证首次读取

```bash
printf '%s' '{"protocol_version":"envoy.read/1","request_id":"install-check","source":{"url":"https://example.org/"}}' | envoy read --request-json -
```

此检查只验证公开网页/网络读取，不代表视频或 ASR 已就绪。按需启用 ASR、FFmpeg 或 OCR：

```bash
envoy asr install --model base
envoy doctor --json --verify-asr
```

技术中文语音建议使用 `large-v3` 以提高识别质量；它比默认 `base`
占用更多磁盘、内存和处理时间。只有确认本机资源足够且中文技术词准确度重要时再安装：

```bash
envoy asr install --model large-v3
envoy doctor --json --verify-asr
```

`base` 是轻量速度档，不是中文技术逐字稿质量保证。任何 Whisper 档位都可能误识术语；
保留 ASR 来源标记，并对不确定词回听或引用原始视频证据。

## CLI 视觉观察

读取结果中的状态字段是 `result`（`ready`、`partial`、`needs_host`、`blocked`、
`empty`）；`execution_state` 是独立的生命周期字段。CLI 可用
`observe-visual` 完成逐帧检查后的观察记录：

```bash
printf '%s' '{"protocol_version":"envoy.observe/1","request_id":"observe-frame-1","material_id":"mat-0123456789abcdef01234567","session_id":"agent-session","resource_id":"resource-frame-1","source_block_id":"blk-video-frame-001","observation":"00:02.000 shows a three-column architecture diagram.","locator_quote":"00:02.000"}' | envoy observe-visual --request-json -
```

协议版本必须是 `envoy.observe/1`。`observation` 是简短纯字符串，不是对象；
内容只能描述宿主实际检查过的图像。保留的材料会根据
`material_id + resource_id + source_block_id` 验证资源归属，并从已交付资源推导
`source_url`；`source_url` 因此可省略。MCP 与 CLI 使用同一观察协议。

抖音页面有时只能公开提供页面文字/章节摘要，而签名或会话绑定的媒体 URL
不能安全地作为视频级证据处理。此时 `envoy_read` 会保持 `partial`，不会返回 ASR
或视频帧；如果用户另有本地文件，可用 `envoy read-local-video` CLI 单独读取
（`full` 工具集下也可调用 `envoy_read_local_video`），但不能假定本地文件与
链接内容相同。

浏览器默认 headless，读取过程中不会弹出任何窗口。页面需要登录或验证时，`envoy_read` 返回 `challenge_required` / `auth_required` 并附 `needs_user_action`，由人类按提示完成一次登录或验证；读到页面要求登录但终端里没有出现窗口属于预期行为，不是故障。可见浏览器只属于人类自己的交互式终端：需要 stdin 和 stdout 同时是 TTY 且显式设置 `ENVOY_VISIBLE_BROWSER=1`，agent 与 MCP 宿主环境下运行时一律拒绝，agent 不得设置该变量。Envoy 不读取个人浏览器 Cookie，也不绕过 CAPTCHA、登录或反爬限制。仅使用用户配置或明确认可的可信代理，不搜索或使用公共代理。

更新时从 Release 获取新 wheel，再运行 `uv tool install --force <wheel-path>`。Whisper/ASR 是可选依赖，更新时需按指南保留已配置的 ASR engine。

Envoy 负责提取有来源定位的证据；宿主模型负责筛选、理解和最终交付。稀疏视频帧或缺少 ASR 时，结果会明确标为不完整。小红书图文与抖音主页发现仍属实验能力，具体边界见[验收矩阵](../../docs/acceptance/platform-coverage-matrix-2026-10-21.md)。

## English

Envoy has two install parts: the Skill instructions and the local MCP runtime.
The runtime keeps link fetching and material processing on the user's machine.

## 1. Install the Skill

With the Skills CLI, from the current GitHub repository:

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
```

## 2. Install the runtime

Download the matching wheel and checksums from GitHub Release **v0.1.9**.
With the GitHub CLI and `uv` already installed:

```bash
gh release download v0.1.9 --repo regrevia/Envoy --dir envoy-v0.1.9
cd envoy-v0.1.9
# macOS/Linux; on Windows compare Get-FileHash output with SHA256SUMS.
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.11 ./envoy_cli-0.1.9-py3-none-any.whl
envoy browser setup
envoy doctor --json
```

Without `gh`, download the same assets from
https://github.com/regrevia/Envoy/releases/tag/v0.1.9 and verify `SHA256SUMS`
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
The bundled config uses `envoy` with the single argument `mcp`, which
declares only `envoy_read`, `envoy_expand`, `envoy_search`, and
`envoy_observe`. Restart or reload the agent, then confirm it can
discover those four. Export `ENVOY_MCP_TOOLSET=full` in the environment that
starts the host (or pass `--toolset full` once) when local video, discovery,
collections, replay, visual history, or doctor tools are needed.

The Skill installer does not install the runtime or edit host MCP settings.
Those steps are kept explicit so users can review what runs locally.
Use an absolute path to the installed `envoy` executable if the host does not
inherit the terminal's PATH. Verify the version with `envoy doctor --json`
after reloading (`envoy_doctor` under `full`); an old connection can keep
running the old process.

DeepSeek Harness Desktop users can install the repository's
[Envoy DSH plugin](../../dsh/README.md) from the Plugin Manager to mount the MCP
bridge, register the skill, and enable link-first routing in one step. It still
requires the runtime above.

### Claude Code and Codex plugins

The Claude Code plugin ships both the MCP bridge and a `UserPromptSubmit`
link-first hook. Codex 0.156 no longer loads plugin-supplied lifecycle hooks, so
its plugin only mounts MCP; run `envoy hook install --host codex` once to merge
the hook into the user-level `~/.codex/hooks.json`. That subcommand exists from
0.1.7 on; the older instructions installed 0.1.6, which cannot run it. The hook asks the model to
try `envoy_read` first and fall back to native fetching only on `blocked` /
`challenge_required`, sign-in or CAPTCHA, or an unavailable Envoy tool.

```bash
# Claude Code
claude plugin marketplace add regrevia/Envoy
claude plugin install envoy@envoy

# Codex: plugin mounts MCP; the hook is installed at user level
codex plugin marketplace add https://github.com/regrevia/Envoy.git
codex plugin add envoy@envoy
envoy hook install --host codex
codex plugin list
```

Codex asks you to trust the hook on first use. For `codex exec`, pass
`--dangerously-bypass-hook-trust` only when you understand that flag.

For local development pass the absolute checkout path to
`plugin marketplace add`. If `envoy` is not on `PATH`, set `ENVOY_BIN` to the
absolute executable path; the hook silently skips when Envoy is missing. See the
[Claude Code plugin](../../claude-code/envoy/README.md) and
[Codex plugin](../../codex/envoy/README.md).

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
or access policy. Every outbound path — HTTP read, Chromium, and yt-dlp media
download — connects **directly first**; a system or environment proxy is only an
automatic fallback after a direct attempt fails, so a proxy never silently
changes what a source returns. Set `ENVOY_BROWSER_PROXY` to an unauthenticated
proxy URL to pin that proxy, or to `none`/`off` to forbid proxying entirely.
Proxy credentials are not passed in browser command-line arguments.

Only use configured or user-approved trusted proxies. Never search for or use
public proxy lists. A 404 alone does not establish deletion, region blocking,
or a proxy problem: ask whether the user can open the link, then diagnose.

Reading runs headlessly by default and never opens a window: a page that still
requires a login or verification action is reported as `challenge_required` /
`auth_required` with a `needs_user_action` block, and the human completes that
action once. A login prompt with no window on screen is expected, not a fault.
Visible mode belongs to a human at their own interactive terminal: both stdin
and stdout must be a TTY and `ENVOY_VISIBLE_BROWSER=1` must be set explicitly;
the runtime refuses it under an agent or MCP host, and an agent must never set
that variable.

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
