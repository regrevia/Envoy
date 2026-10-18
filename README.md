# Envoy

**Let AI watch everything.**

Envoy 专注视频，把公开视频和本地视频整理成主题化 Markdown / HTML 知识库，适合 Wiki、RAG 和 Agent。

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

1. 打开首页，粘贴一个视频链接、B 站 BV 号或分享链接。
2. 也可以切换到主页作品，选择多个视频；或拖入本地视频。
3. 选择 Markdown、HTML 或两者。
4. 点击开始，任务会在后台运行，刷新页面后可从最近任务继续查看。

任务详情页支持查看进度、错误、断点、重试和浏览器接管。真正需要登录、验证码、地区限制或 DRM 的视频，仍取决于来源网站的访问权限。

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

## 当前范围

当前版本专注于视频：公开视频链接、创作者主页、本地视频、批量整理、增量追加、浏览器辅助恢复和渐进式 Wiki 产物。图片、PDF、音频等格式会在同一界面上继续扩展。

---

<a id="english"></a>

# English

**Let AI watch everything.**

Envoy turns public and local videos into theme-first Markdown / HTML knowledge-base packages for Wiki, RAG, and agents.

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

1. Paste a video URL, Bilibili BV ID, or share link.
2. Or choose a creator page, playlist, or local video files.
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

The current release is video-first: public video URLs, creator pages, local videos, batch processing, incremental append, browser-assisted recovery, and progressively disclosed Wiki packages. More formats will be added on the same interface.
