# Envoy

**Let AI watch everything.**

Envoy 正在演进为面向 AI Agent 的通用链接读取与材料处理引擎。当前 `dev` 可处理视频及部分公开文章/图文页面，并生成带来源和资源的 Markdown / HTML 交付物。

[English](#english)

## 快速开始

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

### 独立 Skill（开发版）

当前 `dev` 已包含一个不依赖网站数据库的最小 Skill 运行包，支持公开静态 HTML：

```bash
python -m pip install --upgrade pip
python -m pip install -e packages/envoy-core -e packages/envoy-cli
envoy doctor --json
printf '%s' '{"protocol_version":"envoy.read/1","request_id":"req_01","source":{"url":"https://example.org/"}}' | envoy read --request-json -
```

宿主 Skill 文件位于 `integrations/skills/envoy/SKILL.md`。动态网页、视频、OCR、ASR、浏览器恢复和长任务目前仍使用网站入口，独立 Skill 尚未宣称覆盖这些能力。

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

Envoy Skill、自包含安装包、通用版本化材料协议、微信公众号专项验收以及多用户 Worker 隔离仍在开发中，尚未发布。实施范围、验收门槛与状态见[产品架构方案](docs/product/agent-link-reader-v1.md)和[执行状态](docs/architecture/agent-link-reader-status.md)。

来源访问取决于网站权限和网络条件；遇到登录、验证码、失效内容或 DRM 时应明确报告实际状态。

---

<a id="english"></a>

# English

**Let AI watch everything.**

Envoy is evolving into a general link-reading and material-processing engine for AI agents. The current `dev` branch handles videos and some public articles/image posts, producing source-linked Markdown / HTML packages.

[中文](#envoy)

## Quick Start

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

The Envoy Skill, self-contained installer, versioned shared material protocol, dedicated WeChat validation, and multi-user worker isolation are still in development and have not been released. See the [product architecture](docs/product/agent-link-reader-v1.md) and [implementation status](docs/architecture/agent-link-reader-status.md). Source access remains subject to site permissions and network conditions.
