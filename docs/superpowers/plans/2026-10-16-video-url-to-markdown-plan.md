# Video URL to Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user submit one accessible video URL and receive a Markdown knowledge artifact through one resumable task.

**Architecture:** Add a URL ingestion endpoint that creates a video asset and one `video_to_markdown` task. The worker materializes remote media with yt-dlp, reuses the existing media preprocessing pipeline, records stage checkpoints, and exports Markdown while preserving raw failures. The later VLM stage will consume timestamped continuous frame windows and structured state rather than whole-video prompts.

**Tech Stack:** FastAPI, SQLAlchemy, Celery/local task dispatcher, ffmpeg/ffprobe, yt-dlp, Whisper/OCR, existing Markdown export service, pytest.

---

### Task 1: URL ingestion contract

**Files:**
- Create: `backend/app/services/video_source_service.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/requirements-media.txt`
- Modify: `backend/app/api/v1/tasks.py`
- Test: `tests/test_video_url_ingestion.py`

- [ ] Write failing tests for URL validation, yt-dlp command construction, and raw downloader error propagation.
- [ ] Run the focused tests and confirm they fail because the service and endpoint do not exist.
- [ ] Implement URL validation and a subprocess-backed yt-dlp downloader with metadata and checkpoint-safe output paths.
- [ ] Add `POST /api/v1/tasks/video-to-markdown` accepting `project_id`, `source_url`, and optional processing configuration.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Single-task orchestration and resume checkpoints

**Files:**
- Modify: `backend/app/workers/jobs.py`
- Modify: `backend/app/api/v1/tasks.py`
- Modify: `backend/app/services/crud_service.py`
- Test: `tests/test_video_to_markdown_flow.py`

- [ ] Write failing tests for stage order, retry resume behavior, original error preservation, and terminal task payloads.
- [ ] Run the focused tests and confirm they fail.
- [ ] Implement `video_to_markdown_job` with `download`, `preprocess`, `understand`, and `export` checkpoints in `input_config`.
- [ ] Reuse existing semantic-unit and Markdown export functions without changing unrelated export types.
- [ ] Ensure retries skip successful stages and never duplicate semantic units or artifacts.
- [ ] Run the focused tests and confirm they pass.

### Task 3: URL-first frontend

**Files:**
- Modify: `frontend/app/upload/page.tsx`
- Test: `tests/test_frontend_upload_actions.py`

- [ ] Write a failing contract test for the single URL input and one-submit request.
- [ ] Implement a video URL mode with one input, one action, task link, and raw error display.
- [ ] Preserve the existing local upload flow for non-URL inputs.
- [ ] Run the frontend contract tests.

### Task 4: Continuous-window VLM scheduling

**Files:**
- Create: `backend/app/services/video_window_service.py`
- Modify: `backend/app/services/media_service.py`
- Modify: `backend/app/workers/jobs.py`
- Modify: `backend/app/services/ollama_service.py`
- Test: `backend/tests/test_video_window_service.py`

- [ ] Write failing tests for adaptive frame intervals, overlapping windows, timestamp preservation, and state carry-over.
- [ ] Implement candidate scanning and window planning without model calls.
- [ ] Add Chat and Messages provider adapters for text plus multiple images.
- [ ] Persist structured event results and retry only uncertain or conflicting windows.
- [ ] Run focused tests with mocked providers.

### Task 5: Acceptance and documentation

**Files:**
- Modify: `backend/README.md`
- Modify: `README.md`
- Create: `scripts/run_video_url_markdown_e2e.py`
- Test: `tests/test_video_url_markdown_e2e.py`

- [ ] Add a no-network fixture mode for the complete task contract.
- [ ] Add a real URL acceptance command with timeout, artifact validation, and original error reporting.
- [ ] Verify focused tests, backend startup, frontend build, and a real accessible video URL.
- [ ] Commit the feature changes and push `dev` to `origin`.
