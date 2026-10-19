# Evidence Bridge and Budget Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Envoy's agent-facing evidence path trustworthy across visual handoff, OCR, and incremental material reads.

**Architecture:** Keep source retrieval and host pixel inspection separate. Envoy returns a source-linked image need, then accepts a bounded host observation as a new source-located material block. Material snapshots store an explicit response budget ledger so expansion cannot repeat ranges or exceed the original task budget. Website export passes OCR results from semantic units into the shared material protocol.

**Tech Stack:** Python, Pydantic, stdio MCP, SQLAlchemy semantic units, pytest.

---

### Task 1: Visual observation contract

**Files:**
- Modify: `packages/envoy-core/src/envoy_core/schemas/read_v1.py`
- Modify: `packages/envoy-cli/src/envoy_cli/mcp.py`
- Test: `tests/cli/test_mcp_bridge.py`
- Test: `tests/core_protocol/test_read_protocol.py`

- [x] Add a request model for a bounded host visual observation and a test that it produces a source-located `host_visual` block.
- [x] Run the focused test and confirm the new MCP tool/model is absent.
- [x] Add `envoy_observe_visual` to the MCP tool list and return a compact structured response with the observation as untrusted source material.
- [x] Verify that an observation cannot contain credentials, arbitrary tool instructions, or an unbounded payload.

### Task 2: Incremental budget ledger

**Files:**
- Modify: `packages/envoy-core/src/envoy_core/schemas/read_v1.py`
- Modify: `packages/envoy-cli/src/envoy_cli/reader.py`
- Test: `tests/cli/test_envoy_cli.py`

- [x] Add failing tests for repeated offsets, cumulative character budget, and `max_expansion_rounds=0`.
- [x] Store the initial range, cumulative returned characters, configured total budget, and expansion count in new material snapshots.
- [x] Reject overlapping/repeated ranges and further expansion after the configured round or cumulative budget limit.
- [x] Return `offset`, `returned_chars`, `next_offset`, `remaining_chars`, and `expansion_rounds` in every continuation response.

### Task 3: OCR and profile propagation

**Files:**
- Modify: `backend/app/workers/jobs.py`
- Test: `tests/test_public_page_ingestion.py`
- Test: `tests/test_ocr_profile.py`

- [x] Add a failing export test where image semantic units contain OCR text but `materials.json` currently lacks OCR blocks.
- [x] Query OCR semantic units for public-page image assets and pass normalized OCR records to `public_page_material_response`.
- [x] Pass `pro` through `_run_ocr_for_task` without silently converting it to the default profile.
- [x] Run focused backend tests.

### Task 4: Coverage truthfulness and regression

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/reader.py`
- Modify: `integrations/skills/envoy/SKILL.md`
- Modify: `docs/architecture/agent-link-reader-status.md`
- Test: `tests/cli/test_envoy_cli.py`
- Test: full repository suite

- [x] Add tests for image-only pages, verification/challenge text, and pages exposing video URLs without video extraction.
- [x] Return explicit partial/blocked coverage for these cases instead of unconditional ready.
- [x] Document the new observation tool, budget ledger, and remaining standalone video gap.
- [x] Run focused tests, full suite, diff checks, and an installed-source CLI/MCP smoke.
