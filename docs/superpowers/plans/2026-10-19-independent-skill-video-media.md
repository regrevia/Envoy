# Independent Skill Video Media Plan

> **For agentic workers:** Execute inline in this task using focused tests after each task. Keep the supported website pipeline unchanged.

**Goal:** Let the standalone Envoy CLI/MCP turn public platform videos without captions into time-located ASR evidence and, only when the host requests frame input, deliver a small budgeted set of source frames.

**Architecture:** Keep subtitle extraction first and leave final interpretation to the host model. Add an isolated local media adapter for bounded yt-dlp retrieval, FFmpeg audio/frame extraction, and an installed local ASR provider. Persist only transcript/index plus leased frame files; never read cookies or send media, signed URLs, or credentials to a model provider.

**Tech Stack:** Python 3.10+, yt-dlp, FFmpeg/ffprobe, optional OpenAI Whisper local runtime, Pydantic read protocol, pytest.

---

## Files And Boundaries

- `packages/envoy-cli/src/envoy_cli/media.py`: bounded media acquisition, probing, audio extraction, ASR adapter, timestamped frame sampling, and cleanup-safe local outputs.
- `packages/envoy-cli/src/envoy_cli/reader.py`: choose subtitle, ASR, and optional frame paths; normalize all speech into source-located transcript material; enforce request coverage and budgets.
- `packages/envoy-core/src/envoy_core/schemas/read_v1.py`: declare host frame-input capability and any explicit media range needed by the reader.
- `packages/envoy-cli/pyproject.toml` and `backend/requirements-media.txt`: align Python and yt-dlp minimums with the current supported extractor runtime; declare local ASR as an installable capability.
- `packages/envoy-cli/src/envoy_cli/capabilities.py`, `integrations/skills/envoy/SKILL.md`, `integrations/skills/envoy/INSTALL.md`: report when local media/ASR/frame capabilities are available and how the Agent requests frames.
- `tests/cli/test_video_subtitles.py`, `tests/cli/test_envoy_cli.py`, `tests/core_protocol/test_read_protocol.py`: deterministic protocol, safety, budget, and material-lifecycle tests.
- `docs/architecture/agent-link-reader-status.md`: record real accepted/rejected platform results and the exact standalone runtime boundary.
- `scripts/probe_platform_urls.py`: repeatable credential-free platform metadata probe with structured access-boundary classifications.

## Task 1: Runtime Compatibility And Media Adapter

- [x] Add tests for missing ffmpeg/ASR capability, byte-limit enforcement, deadline handling, timestamp-preserving ASR cues, false no-speech output, and exact frame count.
- [x] Run focused tests and verify they fail because the standalone media adapter is absent.
- [x] Implement `media_runtime_status()`, bounded audio/video download, timestamped local Whisper ASR, FFmpeg duration probing and frame extraction. yt-dlp uses `ignoreconfig`, `noplaylist`, no cookies, zero retries, a process-level total-work watchdog plus bounded per-request socket timeouts, a max-file-size hint, and a progress hook enforcing observed bytes. FFmpeg uses argv lists, explicit timeout, no shell, and a bounded frame count/resize.
- [x] Keep local ASR optional at import time. If its package/model is unavailable, return a typed missing-capability result; do not silently report successful transcription with empty or non-speech segments.
- [x] Set the published CLI minimum to Python 3.10 and yt-dlp 2026.08.19, matching the verified extractor runtime. Align the backend yt-dlp floor.
- [x] Run focused adapter tests and the clean wheel metadata/entrypoint compatibility check.
- [x] Add a process-level watchdog for yt-dlp metadata extraction so a multi-request extractor cannot exceed the total work budget by accumulating individually bounded socket waits.

## Task 2: ASR Transcript And Goal Retrieval

- [x] Add reader tests where a public video has no subtitle track but a mocked local ASR adapter returns cue timestamps; assert `origin=asr`, `video_time` locators, partial work-budget coverage, and missing-capability reporting.
- [x] Run those tests and confirm the current no-subtitle path fails before implementation.
- [x] Reuse the transcript material store and range ledger for ASR text. Record model/language/source metadata and sampled time ranges; keep original captions distinguishable from generated ASR.
- [x] Feed ASR cues through the same goal-ranked search path as captions. Keep subtitle-first behavior so ASR is not run when usable captions are available.
- [x] Run subtitle, search, expansion, and ASR fallback tests together.
- [x] Serialize same-material search/expand/visual-observation ledger updates across threads and processes; test concurrent expansion against one cumulative response budget.
- [x] Limit subtitle cues and local ASR work to an explicitly requested video-time range; return a structured empty result when no cues overlap.
- [x] Bound local Whisper model concurrency with a cross-process model-cache lock and include lock wait in the ASR work budget.
- [x] Use a killable worker watchdog around yt-dlp metadata/download work, not only post-stage elapsed-time checks.
- [x] Keep Whisper child process and pipe cleanup in `finally`; exercise no-speech and checkpoint exits.

## Task 3: Explicit Host Frame Handoff

- [x] Add protocol tests for `video_input=frames`, `image_input=local_file`, and the default no-frame capability.
- [x] Run the tests and verify the current protocol rejects or ignores frame input.
- [x] Extend the protocol to let the host explicitly declare frame capability and optionally request a video-time range. Do not infer image inspection solely from the URL or user prose.
- [x] Publish every core `ReadRequest` field through MCP's strict tool schema and assert schema parity for scope, budget, retention, and host capabilities.
- [x] When requested, extract no more than `max_visual_inputs` frames, scale each within `max_visual_pixels_per_input`, preserve actual source timestamps, and return frame blocks/resources with `local_file` delivery and source-linked locators.
- [x] Persist derived frame files under the material lease; clean them with expired material cleanup. Store matching resource/block/source records so `envoy_observe` can persist a host observation against the correct frame.
- [x] If no frames are produced, the host cannot consume local images, the media URL is unsafe, or the budget is insufficient, return explicit `partial`/`blocked` coverage and do not produce a false `needs_host` result.
- [x] Run frame delivery, resource binding, lease survival, and explicit observation/history tests.
- [x] Validate all yt-dlp DNS resolutions and redirect targets against global IP addresses while allowing configured proxy endpoints; constrain the HTTP handler to the guarded urllib path.

## Task 4: Real Video Acceptance And Release Gate

- [x] Run the latest-supported yt-dlp against one real captioned YouTube video and one real no-caption public platform video; record exact URLs, extractor/runtime versions, result codes, transcript/frame counts, and time locators without copying long copyrighted transcript text into the repository. YouTube `iG9CE55wbtY` returned 427 public English subtitle cues; Bilibili `BV1zj411K7zL` returned 17 English ASR cues for 0-60 seconds without subtitle tracks. Earlier Bilibili `BV1xx411c7mD` remains a recorded `no_speech` sample.
- [x] Run local ASR on an actual bounded speech clip and compare its timed output to the public-domain source. A separate real Bilibili clip returned only non-speech hallucination; it is now rejected as `no_speech`. Run frame handoff only with a host declaring the relevant capability.
- [x] Run the synthetic goal-directed search benchmark against sequential transcript exposure; record returned chars, estimated tokens, tool calls, and latency. Host/provider token use remains `unknown` without host usage telemetry.
- [x] Add a repeatable multi-URL platform probe that continues after per-site failures and classifies accepted, authentication, network, and unsupported results.
- [x] Run the full Python suite, frontend build, Skill ZIP build, and final CLI wheel build/install smoke.
- [x] Update the capability catalog, Skill installation requirements, and architecture status to match only verified behavior.
- [x] Push `dev` and pass GitHub CI, including the Windows standalone job.
- [ ] Extend platform matrix acceptance for Douyin/Vimeo/Dailymotion and any release-specific target matrix, then fast-forward `main` only after review and CI pass.
