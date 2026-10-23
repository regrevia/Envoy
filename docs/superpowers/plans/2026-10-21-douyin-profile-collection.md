# Douyin Profile Collection Implementation Plan

> **For agentic workers:** Execute inline using focused tests after each task. Preserve independent-runtime isolation; do not read personal browser cookies.

**Goal:** Deliver a bounded Douyin collection flow that discovers stable candidates, lets the host select them, reads each selected item with source-bound evidence, and resumes without repeating completed media work.

**Architecture:** A dedicated adapter creates immutable bounded discovery snapshots. A local collection store provides revision-checked manifests and idempotent operation records. The host remains the selector, interpreter, and artifact author; existing per-resource readers remain responsible for source evidence.

**Tech Stack:** Python 3.10+, yt-dlp metadata extraction, Playwright isolated Chromium, Pydantic, JSON local state with atomic writes and restrictive permissions, pytest.

---

## Files And Boundaries

- `packages/envoy-cli/src/envoy_cli/douyin.py`: URL canonicalization, bounded public-profile discovery, candidate normalization and immutable page snapshots.
- `packages/envoy-cli/src/envoy_cli/collections.py`: snapshot and manifest persistence, revision checks, cumulative usage, operation idempotency and evidence availability.
- `packages/envoy-cli/src/envoy_cli/reader.py`: candidate-bound media selection, final-URL rerouting and material evidence replay.
- `packages/envoy-cli/src/envoy_cli/browser.py`: distinguish primary page video URLs from incidental captured requests.
- `packages/envoy-cli/src/envoy_cli/mcp.py`, `cli.py`: expose `envoy_discover`, `envoy_collection_checkpoint`, and `envoy discover`.
- `packages/envoy-core/src/envoy_core/schemas/read_v1.py`: strict discovery/checkpoint request and response contracts.
- `integrations/skills/envoy/SKILL.md`: host workflow, clarification, title selection, checkpointing and final-artifact guidance.
- `tests/cli/test_browser_runtime.py`, `tests/cli/test_douyin_collection.py`, `tests/cli/test_mcp_bridge.py`, `tests/core_protocol/test_read_protocol.py`, `tests/test_envoy_cli_distribution.py`: focused regression and contract tests.
- `docs/superpowers/specs/2026-10-21-douyin-profile-collection-skill-design.md`: approved boundaries and acceptance contract.

## Task 1: Correct Single-Item Evidence Routing

- [x] Add failing regression tests for unbound media, `include_visual=false`, short-link final URL routing, and Windows clean-home setup.
- [x] Confirm the three runtime behavior regressions fail before implementation.
- [x] Keep primary `<video>` URLs separate from captured network requests; accept only one validated primary URL for local video processing.
- [x] Reroute browser-resolved video pages by their final URL and preserve the final URL as evidence source.
- [x] Require `scope.include_visual` in addition to frame-capable host declarations.
- [x] Set both `HOME` and `USERPROFILE` to an existing temporary directory in Windows distribution smoke.
- [x] Run browser, video-subtitle, distribution, and core protocol tests.

## Task 2: Immutable Bounded Douyin Discovery

- [x] Add tests for canonical video/note identity, tracking-parameter deduplication, mixed item types, page replay, unknown continuation, maximum limits, access errors, and no-media-work discovery.
- [x] Implement candidate metadata normalization and URL identity; defaults are 20 and the hard limit is 100.
- [x] Implement canonicalization for `/video/<id>` and `/note/<id>` without retaining tracking or signed query parameters.
- [x] Extract at most 100 public candidate metadata entries with yt-dlp flat metadata using `ignoreconfig`, no cookies, no playlist media download, and the existing public URL validator.
- [ ] If metadata extraction cannot enumerate the page, use the isolated browser for bounded DOM candidate discovery; do not solve login/CAPTCHA or read an external profile.
- [x] Persist candidate pages before returning. Encode the cursor as snapshot revision plus offset; replaying a cursor returns the same page.
- [x] Return `has_more` as true/false/unknown and report ordering, cursor mode, stop reason, and discovery timestamp.
- [x] Verify mocked discovery normalization and zero download/ASR/frame invocations.

## Task 3: Revision-Checked Collection Checkpoints

- [x] Add tests for run identity isolation, expected-revision conflicts, duplicate operation IDs, state transitions, atomic persistence, and cumulative budget exhaustion.
- [x] Implement owner-only collection state storage with atomic JSON replacement and symlink/path checks.
- [ ] Separate `collection_id`, `run_id`, `discovery_revision`, `selection_plan_revision`, `manifest_revision`, and `operation_id`.
- [x] Add bounded incremental checkpoint operations: get, discovery snapshot, select, item state, evidence, usage, and cursor. Require `expected_revision` for mutations.
- [x] Make repeated mutation operation IDs return the original result without double-counting usage.
- [x] Track per-item work state separately from evidence availability, retaining material ID, source revision, expiry and availability.
- [x] Track cumulative pages, candidates, selected items, work time, downloaded bytes, returned characters, visual inputs/pixels, and retries; refuse work beyond budget and preserve the resume cursor.
- [ ] Recover an expired `processing` lease as retryable; preserve `needs_host` as a pending handoff and preserve completed observations.
- [x] Run store tests in separate Python processes to verify restart persistence and revision conflict behavior.

## Task 4: Idempotent Reads And Evidence Replay

- [x] Add failing tests proving a repeated operation ID does not download/transcribe twice and a fresh host context can receive evidence again within a new bounded budget.
- [ ] Persist a pending operation before expensive download/ASR/frame work, then atomically persist its material reference and terminal operation result.
- [ ] On retry, return the completed operation result before starting network or media processing.
- [x] Add a bounded material replay operation that returns source-located cached blocks/resources while charging the new response budget, without reusing the old session's returned-range ledger.
- [x] Report expired, evicted, missing, or session-unavailable evidence explicitly; never call stale evidence reusable completion.
- [x] Preserve frame resource IDs and already submitted host observations; return only unobserved resources on continuation.
- [ ] Exercise process exit after material completion but before host checkpoint and confirm retry recovers the material without repeating ASR.

## Task 5: Multi-Image Note Evidence

- [ ] Add fixtures for ordered multi-image notes, avatars/recommendations, lazy-loaded images, unknown album totals, and note URLs that must not trigger ASR.
- [ ] Bind image candidates to the canonical note ID and exclude profile/recommendation regions.
- [ ] Persist ordered image resource IDs and return discovered/read/uncovered counts, using unknown when the source total cannot be established.
- [ ] Enforce collection-wide image/visual budgets and retain each image's original order and source locator.
- [ ] Run image-note tests together with video tests to ensure type routing remains disjoint.

## Task 6: MCP, Skill, And Acceptance

- [x] Add `envoy_discover` and `envoy_collection_checkpoint` to MCP tools/list schema and tools/call dispatch; add JSON CLI discovery.
- [x] Update the Skill to ask only material clarification questions, filter titles as hints, select IDs explicitly, checkpoint each item, resume from evidence state, and compose the requested artifact itself.
- [ ] Add host guidance that title matches are not content evidence and collection output must state its discovered/selected coverage.
- [ ] Run focused and full standalone Skill/protocol suites, clean wheel build/install smoke, skill bundle tests, and Windows CI.
- [ ] Run a live public profile and one video plus one multi-image note against the exact installed runtime; record build identity, runtime capability, candidate/page counts, work, recovery behavior and access boundaries.
- [ ] Do not claim real-platform acceptance from fixtures; if the site blocks enumeration or the network fails, report the exact unverified acceptance item.
