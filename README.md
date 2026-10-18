# Envoy

**Let AI watch everything.**

Envoy turns videos into clean, theme-first Markdown/HTML knowledge-base packages for Wiki, RAG, and agents.

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

The first build is large because it includes FFmpeg, Whisper, OCR, browser automation, and media dependencies. Later starts reuse Docker volumes.

### Windows Native Development

Requirements:

- Python 3.11+
- Node.js 20+
- FFmpeg and FFprobe on `PATH`
- Docker Desktop for MySQL, Redis, and MinIO

```powershell
Copy-Item .env.example .env
.\scripts\start_env.ps1
```

Or:

```bat
scripts\start_env.bat
```

The script finds free ports, starts dependencies, starts the API and frontend, and writes local logs under `logs/`.

### Source Development

```bash
cp .env.example .env
docker compose up -d mysql redis minio

cd backend
python -m venv .venv
./.venv/bin/pip install -r requirements-media.txt
python -m uvicorn app.main:app --reload --port 8140

# another terminal
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`.

## Configure A Model

Envoy starts without a model or API key. After the app starts, open the settings icon in `/video` and enter:

- API Base URL
- Model name
- Protocol: Chat Completions or Messages
- API key

The key is encrypted into local runtime state. It is not included in Docker images, Git, Markdown exports, browser responses, or unrelated model prompts.

## Outputs

Each completed task produces a downloadable package:

- `index.md`: text-first Wiki/agent entry point
- `index.html`: visual review with keyframes
- `themes/`: topic pages
- `assets/frames/`: selected video frames
- `manifest.json`: machine-readable index
- `evidence.jsonl`: traceable evidence
- `versions/`: incremental append history

The package is also available as a ZIP archive.

## Privacy And Secrets

The repository intentionally excludes:

- `.env` files
- API keys and tokens
- encrypted vision configuration and encryption keys
- browser profiles and cookies
- SQLite/MySQL databases
- downloaded media and generated exports
- logs and local runtime state

Before publishing or sharing a build, run:

```bash
git status --short
git ls-files | rg '(^|/)(tmp|Check|\.env$|.*\.db$|.*\.enc$|.*\.key$|.*\.mp4$)'
```

User-provided media and generated exports stay in local Docker volumes or the configured local storage directory. Do not mount a personal browser profile into a public container.

## Development

```bash
backend/.venv/bin/pytest -q
cd frontend && npm run build
```

The main API is available at `http://localhost:8140`; frontend development defaults to `http://localhost:3000`.

## Scope

The current public scope is intentionally video-first: public video URLs, creator pages, local video uploads, incremental video libraries, browser-assisted login recovery, and progressively disclosed Wiki packages. Image, PDF, audio, and other formats are planned as later extensions on the same pipeline. Platform behavior still depends on the source site's access controls, DRM, regional availability, and network conditions.
