# Envoy Stability And Productization Sprint Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current Envoy runtime reliable across evidence retrieval, local material retention, and MCP request failures before expanding product capabilities.

**Architecture:** Keep the `envoy.read/1` protocol stable and make narrow fixes at the existing ownership boundaries. Each reported issue must first be reproduced with a focused test; preserve the distinction between coverage accounting and returned payload accounting, and between a failed request and the lifetime of the MCP process.

**Tech Stack:** Python 3.11+, Pydantic protocol models, pytest, JSON-RPC MCP stdio.

---

### Task 1: Reproduce and fix material search omissions and response-budget accounting

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/reader.py`
- Test: `tests/cli/test_envoy_cli.py`

- [x] Add a long single-line material fixture with two query hits separated by an already-returned window; assert a later search returns the second hit.
- [x] Add a partial-overlap search fixture where each result repeats context; assert `returned_tokens` and the single-call budget count the full serialized text block, not only unseen characters.
- [x] Run the two focused tests and record whether each reported defect reproduces on current `main`.
- [x] Fix only reproduced defects while preserving range coverage as a separate ledger.
- [x] Run the focused tests and the existing material search/expand tests.

### Task 2: Reproduce and fix material commit versus cleanup races

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/reader.py`
- Test: `tests/cli/test_envoy_cli.py`

- [x] Add a deterministic concurrent test that pauses material publication while a separate material-root cleanup runs.
- [x] Confirm cleanup could remove just-published JSON during the JSON/expiry publication gap.
- [x] Make writer publication and stale-material cleanup coordinate without unlinking live lock files or changing the cross-process lock contract.
- [x] Run the race test and material/cache tests.

### Task 3: Reproduce and isolate profile discovery failures in MCP

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/collections.py`
- Modify: `packages/envoy-cli/src/envoy_cli/mcp.py`
- Test: `tests/cli/test_douyin_collection.py`
- Test: `tests/cli/test_mcp_bridge.py`

- [x] Test extractor metadata without an `entries` field followed by a browser failure; discovery returns a bounded error and the next MCP request is handled.
- [x] Test a browser-success page that exposes no candidates and verify discovery fails instead of storing an empty snapshot.
- [x] Initialize `extractor_error` on every path and convert unexpected tool-handler exceptions to a sanitized JSON-RPC internal error while logging diagnostics only to stderr.
- [x] Run focused discovery/MCP tests, then the complete standalone/protocol/distribution suite.

### Task 4: Release gate

**Files:**
- No release metadata changes unless the verified fixes require a patch release.

- [x] Run `git diff --check`.
- [x] Run `uv run --project packages/envoy-cli --with pytest --with reportlab --with-editable packages/envoy-core --with-editable packages/envoy-cli --with pip -- python -m pytest -q tests/cli tests/core_protocol tests/test_envoy_cli_distribution.py tests/test_skill_bundle.py tests/test_video_evidence_eval.py`.
- [x] Run required repository CI on the exact stability candidate commit `195fe73`.
- [x] Require the Ubuntu, Windows, and macOS standalone runtime jobs, plus frontend and Compose checks.
- [ ] Do not publish if any platform job fails or a reported behavior lacks a stable regression test.

## Stage 1 Local Evidence

- Search: later unseen hits on one long line were omitted if the first unseen
  segment had no hit. Search now checks every unseen segment.
- Budget: repeated timestamp/context blocks were returned beyond the 64-token
  test budget because only newly covered ranges were charged. Actual returned
  text and newly covered ranges are now accounted independently.
- Cache: cleanup could remove JSON between its publication and the `.expires`
  publication. Cleanup now takes the same per-material interprocess lock and
  rechecks expiry/capacity while holding it.
- Discovery/MCP: extractor metadata without `entries` could leave
  `extractor_error` undefined. Unexpected tool failures now become sanitized
  per-request `-32603` errors, are logged to stderr, and do not stop the stream.
- Local verification: standalone/protocol/distribution/Skill/eval suite passed
  **256 tests**. Remote Linux, Windows, macOS, frontend, and Compose checks
  remain the merge gate for this candidate.

## Stage 2 Progress

- ASR model selection is persisted in `~/.envoy/asr.json` (or the configured
  `ENVOY_CONFIG_DIR`) with mode-restricted atomic writes. `ENVOY_ASR_MODEL`
  remains an explicit override.
- A prepared checkpoint's expected digest is retained with the selected model.
  `doctor` distinguishes configuration, engine presence, file presence, digest
  verification, and operational load status. `--verify-asr` performs the
  explicit checksum and model-load check and returns nonzero when it fails.
- Install instructions now preserve the optional Whisper dependency during a
  forced uv tool replacement with `--with openai-whisper==20250625`; settings
  and model cache remain outside the replaceable tool environment.
- Focused package-install/runtime-status tests pass.
- Isolated real upgrade acceptance completed using the measured
  `https://mirrors.aliyun.com/pypi/simple/` source: Whisper/PyTorch were
  installed, the `tiny` checkpoint was downloaded and checksum-verified,
  `uv tool install --force --with openai-whisper==20250625` replaced the tool,
  and post-upgrade `envoy doctor --verify-asr` returned
  `asr_operational: verified`. The production `~/.local` tool was not changed.

## Deferred Stages

- [x] Persist selected ASR model/checksum separately from the replaceable uv tool environment; make routine `doctor` distinguish installed engine, configured capability, model file, verified checkpoint, and an explicitly loaded operational check.
- [x] Exercise a real upgrade with Whisper enabled, verify `uv tool install --force --with openai-whisper==20250625 ...` restores the engine, and verify the selected model/checkpoint survive.
- Add standalone OCR only after selecting an optional engine and evidence schema that preserves source image, reading order, regions, timestamp where applicable, and quality.
- Establish versioned real-host evaluation for at least 20 sources and 40 tasks before making token/cost savings claims.
