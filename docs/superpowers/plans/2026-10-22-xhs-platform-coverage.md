# Xiaohongshu And Common Platform Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe Xiaohongshu public image-note extraction and broaden common video-page browser fallback while preserving truthful evidence coverage.

**Architecture:** Add a small platform classification module shared by the reader and tests. Xiaohongshu notes use a post-bound ordered image parser with JSON/script and DOM inputs; video platforms continue through the existing yt-dlp, browser, local ASR, and frame pipeline. Envoy reports access and evidence gaps instead of bypassing challenges.

**Tech Stack:** Python 3.11+, `HTMLParser`, JSON, existing Pydantic protocol, Playwright/yt-dlp/FFmpeg pipeline, pytest.

---

## Release Checkpoint (2026-10-22)

Implemented platform routing, bounded source-note JSON/gallery parsing, retryable
operation results (including legacy completed failures), HTTP 404 diagnostics,
and Skill/capability guidance. The standalone/protocol/distribution suite passes
250 tests. Source-bound gallery tests reject avatars, unrelated state, and
recommendation videos. No new live-platform acceptance was obtained for this
release; Xiaohongshu remains experimental, and the existing acceptance matrix
must not be interpreted as new acceptance evidence. The checklists below retain
the original plan, including live verification work not yet completed.

### Task 1: Platform classification and XHS parser contract

**Files:**
- Create: `packages/envoy-cli/src/envoy_cli/platforms.py`
- Test: `tests/cli/test_platforms.py`

- [ ] Write failing tests for Xiaohongshu `/explore/<id>`, `/discovery/item/<id>`, `xhslink.com` redirects, common video paths, and ordered XHS image extraction excluding avatars/recommendations.
- [ ] Run the focused tests and confirm the missing module/functions fail.
- [ ] Implement canonical URL predicates, common video-page predicates, and an XHS parser that reads structured JSON/script image URLs plus DOM images without returning credentials or signed media parameters.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Reader routing for XHS notes and broader video pages

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/reader.py`
- Test: `tests/cli/test_platforms.py`
- Test: `tests/cli/test_browser_runtime.py`

- [ ] Add failing tests proving XHS notes bypass the single-video subtitle extractor, preserve image order, return `image_note` coverage, and do not run ASR.
- [ ] Add failing tests for `/reel/`, `/shorts/`, `/status/`, Dailymotion `/video/`, Vimeo numeric pages, and XHS final URLs entering browser fallback when content is dynamic.
- [ ] Implement routing through the shared predicates and XHS gallery extractor; keep signed/session-only URLs local and mark incomplete albums partial.
- [ ] Run focused reader/browser tests and confirm they pass.

### Task 3: Capability and Skill guidance

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/capabilities.py`
- Modify: `integrations/skills/envoy/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_skill_bundle.py`

- [ ] Add explicit capability language for public XHS image notes and common extractor-first video URLs.
- [ ] Document that platform names indicate routing candidates, not guaranteed live acceptance; explain `partial`, `blocked`, and challenge boundaries.
- [ ] Test the Skill bundle contains XHS and common-video workflow guidance.

### Task 4: Verification and acceptance record

**Files:**
- Modify: `docs/acceptance/platform-coverage-matrix-2026-10-21.md`

- [ ] Run the complete standalone suite and distribution/Skill checks.
- [ ] Run one public XHS sample and one common video sample when network access permits; record exact result and evidence counts.
- [ ] Update the matrix with accepted, partial, blocked, and unverified states without claiming coverage from extractor recognition alone.
