# Envoy

**Let AI watch everything.**

Envoy 正在演进为面向 AI Agent 的通用链接读取与材料处理引擎。当前 `dev` 网站可处理视频及部分公开文章/图文页面；独立 Skill/MCP 还支持静态网页、PDF 文本层、纯文本/Markdown/CSV/JSON 链接和公开图片视觉交接。

[English](#english)

## 快速开始

### Agent Skill + MCP（推荐）

Envoy 已发布独立 Skill/MCP：为 Agent 提取公开链接和用户指定本地视频的材料、
时间戳、字幕及可选 ASR/视频帧。Envoy 提供证据，宿主模型负责理解与交付。

```bash
npx skills add regrevia/Envoy --skill envoy --global --yes
gh release download v0.1.6 --repo regrevia/Envoy --dir envoy-v0.1.6
cd envoy-v0.1.6
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.11 ./envoy_cli-0.1.6-py3-none-any.whl
envoy browser setup
envoy doctor --json
```

要求已有 Node.js、GitHub CLI 和 uv；Windows 用 `Get-FileHash` 核对校验和。
接着把 `envoy mcp` 加入宿主的 stdio MCP 配置并重载连接。
**安装 Skill 不等于安装 runtime 或配置 MCP。** 完整步骤、无 `gh` 安装方式、
首读检查、可选 FFmpeg/ASR 和更新说明见
[安装文档](integrations/skills/envoy/INSTALL.md)。

支持 macOS、Linux、Windows；浏览器默认 headless，必要的登录/验证才请求用户操作。
单链接与本地视频是主要使用路径。小红书图文适配、抖音主页集合为实验能力，
不承诺任意分享链接、全量作品或日期范围扫描；按[真实验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)判断平台状态。
批量预算仍需宿主记账，不等同于全批次原子预算保证。

[版本更新](CHANGELOG.md) · [安装或读取问题反馈](https://github.com/regrevia/Envoy/issues/new/choose)

### 网站部署（可选）

以下 Docker 和前后端步骤用于运行完整网站，不是安装 Agent Skill 的前置条件。

### Docker Desktop

```bash
cp .env.example .env
docker compose up --build
```

打开 `http://localhost:3000`。

Windows PowerShell：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

首次构建会安装 FFmpeg、Whisper、OCR、浏览器自动化和媒体处理依赖，之后启动会复用 Docker volumes。

### Windows 本地运行

要求：Python 3.11+、Node.js 20+、FFmpeg 和 FFprobe 在 `PATH` 中，以及 Docker Desktop。

```powershell
Copy-Item .env.example .env
.\scripts\start_env.ps1
```

也可以运行 `scripts\start_env.bat`。脚本会寻找可用端口、启动依赖/API/前端，并把日志写入 `logs/`。

### 本地开发

要求：

- Python 3.11+
- Node.js 20+
- FFmpeg 与 FFprobe 在 `PATH` 中
- Docker Desktop 用于 MySQL、Redis 和 MinIO

```bash
cp .env.example .env
docker compose up -d mysql redis minio

cd backend
python -m venv .venv
./.venv/bin/pip install -r requirements-media.txt
./.venv/bin/python -m uvicorn app.main:app --reload --port 8140

# 另开终端
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:3000`。

本机默认最多并行处理两个任务；资源充足时可在 `.env` 中把 `ENVOY_LOCAL_TASK_MAX_CONCURRENCY` 调高至 8。

## 使用方式

1. 打开首页，粘贴一个视频或公开网页链接。
2. Envoy 自动识别已适配的来源类型；主页作品可批量选择，也可拖入本地视频。
3. 选择 Markdown、HTML 或两者。
4. 点击开始，任务会在后台运行，刷新页面后可从最近任务继续查看。

任务详情页支持查看进度、错误、断点、重试和浏览器接管。公开页面能力仍在扩展；登录、验证码、地区限制或 DRM 来源不会被承诺绕过。

### 独立 Skill 与开发

当前 `dev` 包含独立 Skill/MCP 运行包，支持公开静态/动态 HTML、PDF 文本层、纯文本类链接、图片视觉交接，以及公开视频字幕、受预算约束的本地媒体下载/Whisper ASR 和宿主请求的视频帧。页面读取、媒体下载、字幕/ASR 和帧是不同证据层；`envoy_doctor` 会返回构建来源与本机能力，`envoy_read` 会明确标记缺少的证据。平台覆盖按真实验收矩阵跟踪，不把 extractor 可识别等同于站点已验收。

```bash
python -m pip install -e packages/envoy-core -e packages/envoy-cli
envoy browser setup
envoy doctor --json
printf '%s' '{"protocol_version":"envoy.read/1","request_id":"req_01","source":{"url":"https://example.org/"}}' | envoy read --request-json -
```

`envoy browser setup` installs Chromium for Envoy's isolated page reader. Public
pages are read headlessly; the Envoy browser window appears only when a page
needs a user login or verification action.

开发环境可构建无凭据 Skill 包；日常开发通过当前源码运行和验收，不需要安装第二套 Envoy runtime：

```bash
python scripts/package_envoy_skill.py --output dist/envoy-skill.zip --json
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src envoy doctor --json
```

面向用户的安装步骤见 [`integrations/skills/envoy/INSTALL.md`](integrations/skills/envoy/INSTALL.md)。在 `main` 上同步提升 `envoy-cli` 和 `envoy-core` 版本后，GitHub Actions 会自动创建对应的 `v<version>` tag；tag 触发 Release workflow，发布 Skill ZIP、自包含 CLI wheel 和 `SHA256SUMS`。普通代码提交不会创建 Release，同一版本只发布一次。wheel 内含匹配的 `envoy-cli` 与 `envoy-core`。Skill 包不含 `.env`、API Key、Cookie、数据库、浏览器配置或下载素材。宿主 MCP 配置位于 `hosts/mcp.json`，默认调用 `envoy mcp`。

用户侧始终使用同一个 `envoy` 命令和 MCP 服务。内部构建仍记录包版本、源码 revision、构建通道和 dirty 状态；正式发行只从干净源码和匹配版本构建，便于回溯具体产物来源。

独立评测：

```bash
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src \
  python benchmarks/video_evidence_eval.py --materials-dir /tmp/envoy-eval
```

该固定样本测量字幕证据命中、时间定位、Envoy 调用数、返回字符/估算 token 和检索延迟；不运行宿主 LLM，所以不会虚报实际模型用量。若宿主能提供 usage JSON，可用 `--host-usage-file` 纳入实际输入/输出 token。

宿主 Skill 文件位于 `integrations/skills/envoy/SKILL.md`。独立 runtime 提供公开抖音主页候选发现与集合检查点、用户选择的本地视频读取，以及动态网页、平台视频字幕/ASR/抽帧和隔离浏览器恢复。站点访问仍受公开可读性限制；独立 Skill 尚未支持扫描版 PDF OCR。

支持 stdio 的 MCP 宿主可以直接运行 `envoy mcp`，提供 `envoy_read`、`envoy_read_local_video`、`envoy_discover`、`envoy_collection_checkpoint`、`envoy_expand`、`envoy_search`、`envoy_doctor`、`envoy_observe_visual` 和 `envoy_visual_history`，返回格式与 CLI 相同。MCP bridge 支持 2026-07-28 无会话协议，并兼容旧式初始化协商：

普通链接可直接按用户目标调用 `envoy_read`。`envoy_doctor` 提供版本化能力目录，仅在可选能力不明确时查询；模型根据覆盖信息决定是否续读或请求宿主检查图片，不按网站硬编码提示流程。

```json
{
  "mcpServers": {
    "envoy": {
      "command": "/absolute/path/to/envoy",
      "args": ["mcp"]
    }
  }
}
```

工具 schema 只公布独立运行时可处理的输入字段；版本化能力目录会明确列出条件能力和暂不支持的类型，避免把网站侧能力误报给 Agent，也减少 schema 占用的上下文。

新版 MCP 返回完整结构化材料一次，文本内容仅提供短状态说明；旧版仍保留文本 JSON 兼容输出。

图片视觉交接完成后，宿主可用 `envoy_observe_visual` 提交有限、带来源的观察；该观察仍被视为不可信材料，不会变成 Agent 指令。

Hook 不是必需依赖。宿主可以在工具调用外层增加日志、缓存和用量统计，但不应改变 `envoy.read/1` 请求/响应契约。

独立 Skill 也支持公开图片链接的视觉交接：宿主声明图片输入后，Envoy 返回图片 URI 和 `inspect_visual`，不会把图片转成 base64 塞进上下文。

长页面首读按请求预算限制返回；若结果标记 `partial` 且覆盖信息给出 `continuation_available`，使用 `envoy expand` 按 `next_offset` 读取下一段。正文快照在本机限额缓存，不会为每段重新下载页面。

## 模型配置

Envoy 默认不带模型和 API Key。打开 `/video` 右上角齿轮，填写：

- API Base URL
- 模型名
- 协议：Chat Completions 或 Anthropic Messages
- API Key

密钥只会加密保存到本机运行时配置，不会写入 Git、Docker 镜像、Markdown 产物、浏览器响应或无关模型请求。

## 输出内容

每个完成的任务会生成一个可下载的目录或 ZIP：

- `index.md`：适合 Wiki、Agent 和 RAG 的文字入口
- `index.html`：带关键帧图片的可视化阅读页
- `materials.json`：`envoy.read/1` 材料协议，供 Agent 按需读取正文、资源和覆盖信息
- `themes/`：按主题组织的页面
- `assets/frames/`：精选关键帧
- `manifest.json`：机器可读索引
- `evidence.jsonl`：可追溯证据
- `versions/`：追加内容的版本记录

Markdown 适合直接交给大模型继续生成 Wiki；HTML 适合人工复核画面和关键帧。两者可以同时生成。

## 安全与隐私

仓库不会包含：

- `.env`、API Key、Token
- 加密模型配置和本地密钥
- 浏览器配置、Cookie、数据库
- 下载的视频和生成产物
- 日志与本地运行状态

发布前检查：

```bash
git status --short
git ls-files | rg '(^|/)(tmp|Check|\.env$|.*\.db$|.*\.enc$|.*\.key$|.*\.mp4$)'
```

不要把个人浏览器目录挂载到公开 Docker 容器中。

## 开发与测试

```bash
PYTHONPATH=backend backend/.venv/bin/pytest -q
cd backend && PYTHONPATH=. .venv/bin/pytest -q
cd frontend && npm run build
```

开发分支为 `dev`，验收通过后合并到 `main`。用户从 `main` 获取稳定版本。

## 当前状态

当前开发版已通过一条抖音公开图文的真实链路验收：提取正文与 4 张配图，生成 OCR 语义、Markdown/HTML 和 ZIP 产物。单个样本不代表所有网站均已适配。

独立 Skill/MCP 已通过 GitHub Release 发布，包含视频字幕、可选 Whisper ASR 和有限帧交接；缺失模态时明确返回 partial。扫描版 PDF OCR、微信公众号专项验收以及多用户 Worker 隔离仍未完成。实施范围、验收门槛与状态见[产品架构方案](docs/product/agent-link-reader-v1.md)和[执行状态](docs/architecture/agent-link-reader-status.md)。

OCR 默认使用 `balanced` 档位，用户可在高级设置中选择 `fast / balanced / quality / pro`；其中 `pro` 使用 PP-OCRv6 medium，是当前 Envoy 的最高通用精度档，代价是更慢、更占资源。Skill/MCP 请求也可以传入 `ocr_profile`。切换档位时按需下载对应模型，不把全部模型预装进镜像。OCR 属于 Envoy 的派生材料，网站产物可以记录 OCR 文本及来源关系；Skill/MCP 默认不把所有 OCR 原文一次性塞进 Agent 上下文，而是按目标和材料范围逐步读取。

来源访问取决于网站权限和网络条件；遇到登录、验证码、失效内容或 DRM 时应明确报告实际状态。

## 许可证

Envoy 原创代码及文档采用 [Apache License 2.0](LICENSE)，声明见 [NOTICE](NOTICE)。
依赖、浏览器、外部工具、模型权重与用户处理的源材料保留各自许可。

---

<a id="english"></a>

# English

**Let AI watch everything.**

Envoy is evolving into a general link-reading and material-processing engine for AI agents. The current website handles videos and some public articles/image posts. The independent Skill/MCP runtime also supports static HTML, PDF text layers, and visual handoff for public images.

[中文](#envoy)

## Quick Start

For Agent Skill/MCP installation (no Docker required), follow
[INSTALL.md](integrations/skills/envoy/INSTALL.md). Releases include a
self-contained CLI wheel, Skill ZIP, and checksums. Installing the Skill alone
does not install the runtime or configure the host MCP. ASR/FFmpeg are optional.
Envoy's original code is Apache-2.0; dependencies retain their own licenses.
The following instructions run the optional full website.

### Docker Desktop

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:3000`.

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

### Windows Native Run

Requirements: Python 3.11+, Node.js 20+, FFmpeg and FFprobe on `PATH`, and Docker Desktop.

```powershell
Copy-Item .env.example .env
.\scripts\start_env.ps1
```

Alternatively run `scripts\start_env.bat`. The script finds available ports, starts dependencies, the API, and the frontend, and writes logs under `logs/`.

### Local Development

Requirements:

- Python 3.11+
- Node.js 20+
- FFmpeg and FFprobe on `PATH`
- Docker Desktop for MySQL, Redis, and MinIO

```bash
cp .env.example .env
docker compose up -d mysql redis minio

cd backend
python -m venv .venv
./.venv/bin/pip install -r requirements-media.txt
./.venv/bin/python -m uvicorn app.main:app --reload --port 8140

# another terminal
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`.

Local concurrency defaults to two tasks. Set `ENVOY_LOCAL_TASK_MAX_CONCURRENCY` in `.env` up to 8 if your machine has enough resources.

## Usage

1. Paste a video or public web page URL.
2. Envoy automatically routes supported sources. Creator pages and playlists can be selected in batches; local videos can be dropped in.
3. Select Markdown, HTML, or both.
4. Start the task. It continues in the background and remains available after refresh.

Task details provide progress, errors, checkpoints, retry, and browser-session recovery. Sources protected by login, CAPTCHA, regional restrictions, or DRM still depend on the source site's access rules.

## Model Configuration

Envoy starts without a model or API key. Open the gear icon in `/video` and enter:

- API Base URL
- Model name
- Chat Completions or Anthropic Messages protocol
- API Key

Keys are encrypted in local runtime state and are excluded from Git, Docker images, exports, browser responses, and unrelated model prompts.

## Outputs

Each completed task produces a downloadable folder or ZIP:

- `index.md`: text-first entry point for Wiki, agents, and RAG
- `index.html`: visual review with keyframes
- `themes/`: topic pages
- `assets/frames/`: selected frames
- `manifest.json`: machine-readable index
- `evidence.jsonl`: traceable evidence
- `versions/`: incremental append history

Markdown is best for downstream Wiki generation. HTML is best for reviewing visual evidence. Both can be generated together.

## Scope

The current development branch has passed one real public Douyin image-post flow: article text and four images were processed into Markdown/HTML and a ZIP package. This single sample does not imply universal site support.

The standalone Skill/MCP reader supports public static and browser-rendered HTML, text/Markdown/CSV/JSON, PDF text layers with page locators and incremental reads, direct-image visual handoff, timed public subtitles, bounded public-video downloads, optional local Whisper ASR, and host-requested sparse frame handoff. Video-level outputs are explicitly partial when a modality is unavailable. Platform behavior and real acceptance results are tracked in the [platform coverage matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md). See the [product architecture](docs/product/agent-link-reader-v1.md) and [implementation status](docs/architecture/agent-link-reader-status.md). Source access remains subject to site permissions and network conditions.

For MCP agents, ordinary links can go directly to `envoy_read` with the user's goal. `envoy_doctor` provides a versioned capability catalog when optional support is unclear; the model uses coverage to decide whether to continue reading or request host visual/video inspection, without site-specific prompt workflows.
