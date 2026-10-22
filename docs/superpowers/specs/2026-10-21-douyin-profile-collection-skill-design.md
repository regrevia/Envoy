# Douyin Profile Collection Skill Design

## Goal

Let an agent start from a public Douyin creator/profile URL, discover a bounded
set of public video and image-note posts, select relevant posts from the user's
goal, process selected sources with existing Envoy readers, resume interrupted
work, and deliver the requested artifact through the host's artifact workflow.

Envoy provides source-located evidence and coverage. The host model selects
items, asks follow-up questions when requirements are underspecified, performs
semantic analysis, and composes the requested artifact.

## User Workflow

1. The host model checks whether the user's goal is actionable. It asks only
   for missing decisions that materially affect the result, such as date range,
   maximum number of posts, inclusion criteria, or artifact format.
2. The host calls `envoy_discover` on the public profile URL. Discovery returns
   a bounded, ordered page of candidates with stable IDs, canonical URLs,
   title, publication time when available, item type, and source metadata.
3. The model compares candidate titles and metadata against the user's goal.
   Titles are selection hints, not evidence that the post contains the requested
   fact. If selection confidence is low or the batch is large, the model asks
   the user to confirm or narrow the selection.
4. The host records selected candidate IDs, then reads selected candidates
   individually with `envoy_read`. Video posts use public subtitles or local
   ASR and explicitly requested sparse frames; image-note posts use a
   post-bound, ordered album reader. Per-item source blocks, timestamps,
   material IDs, and coverage gaps remain separate.
5. Before an expensive read, the host starts or resumes a stable operation ID.
   Envoy returns a persisted completed operation when the same ID is retried,
   including after a process restart. The host checkpoints the returned material
   reference and status with a revision-checked incremental update.
6. On interruption the host resumes from the collection manifest. Envoy
   distinguishes work completion from evidence availability and can replay
   cached evidence within the new response budget without downloading or
   transcribing the source again. Expired/evicted evidence is explicitly
   unavailable and may be reacquired only as a new operation.
7. The host composes the requested artifact from selected evidence, preserving
   source links and time locators and reporting skipped, blocked, or partial
   items. It does not imply that title matching or sparse frames are complete
   source verification.

## Architecture

### Independent Skill/MCP path

Add `envoy_discover` to the standalone MCP surface and the `envoy discover`
CLI. It uses a dedicated Douyin adapter, with bounded public metadata extraction
attempted first and the isolated headless browser used when needed. Discovery
persists the complete bounded candidate snapshot before returning its first
page. Cursors address that immutable revision, not a live-page offset. A
refreshed discovery creates a new revision; dynamic source ordering or unknown
continuation is reported rather than claimed complete. It never reads a
personal browser profile or automates login, CAPTCHA, or anti-bot verification.

Add `envoy_collection_checkpoint` to persist and retrieve a bounded collection
manifest via incremental operations (`get`, `discovery_snapshot`, `select`,
`item_state`, `record_evidence`, `record_usage`, `set_cursor`). The manifest contains distinct
collection, run, discovery, selection-plan, and manifest revision identifiers;
the current cursor; candidate metadata; selected candidate IDs; per-item work
status; material references and their availability/expiry; and cumulative
actual usage. Updates require `expected_manifest_revision`; duplicate
`operation_id` updates are idempotent. It contains no cookies, signed media
URLs, or downloaded media. Apply restrictive local permissions and explicit
retention.

Add a stable idempotent operation record keyed by collection/run/candidate and
operation ID before starting media work. A retry first checks for a completed
record and reuses it; in-progress records have an attempt lease and can be
retried after lease expiry. Material evidence is replayable within budgets even
when it was already delivered in a previous host context. Completed work with
missing or expired evidence is not represented as reusable completion.

The host remains the orchestrator. It filters candidates by title/metadata,
calls `envoy_read` per selected post, and writes the final artifact with its
normal document/artifact tools. This keeps Envoy useful without the website
backend and lets the host adapt the final format to the user's request.

### Candidate contract

Each candidate has:

- A stable `candidate_id` derived from a canonical public post identity; Douyin
  video and note IDs normalize away tracking/share parameters.
- `source_url`, canonicalized and safe for `envoy_read`.
- `item_type`: `video` or `image_note`; never infer post type from title text.
- `title`, optional `published_at`, optional `duration_seconds`, and creator
  display name when publicly exposed.
- `position` within the discovered page and optional thumbnail URI.

Discovery defaults to 20 and is hard-capped at 100 candidates per snapshot.
Each response includes `discovery_revision`, `cursor_mode`, `ordering`,
`has_more` (true/false/unknown), `stop_reason`, and `discovered_at`. Cursors
replay the same persisted snapshot page. If the source cannot provide a stable
continuation signal, report unknown rather than false. An optional
publication-time range uses match/nonmatch/unknown; it may stop early only when
the adapter confirms newest-first ordering and no pinned-item ambiguity.
Duplicate canonical IDs are removed while preserving source order.

### Checkpoint states

Work states are `selected`, `processing`, `completed`, `partial`, `blocked`,
`skipped`, and `failed`. Completion state is separate from evidence
availability (`available`, `expired`, `evicted`, `missing`). `needs_host` is a
handoff action, not a terminal work state; pending visual resources remain
individually addressable and already submitted observations are not repeated.
Processing attempts include a lease and attempt count so stale `processing`
items can resume. Network failures are retryable only within remaining
collection budget; a user skip records its reason. An accepted partial result
can complete the current requested scope while preserving explicit coverage
gaps.

Collection budgets are cumulative across all pages and operations: scanned
pages/candidates, selected items, elapsed work, downloaded bytes, returned
characters, visual inputs/pixels, and retry attempts. Defaults are 20
candidates/page and a 100-candidate hard snapshot maximum. Budget exhaustion
returns a resumable cursor and `stop_reason`, never an empty/no-more result.

Video media, frames, transcript segments, note images, and host observations
must bind to the current candidate identity. A browser-captured media request
that is not uniquely associated with the page's primary video is not processed.
Short-link redirects are reclassified by final URL and enter the same source
evidence pipeline. Frame capability does not imply frame request:
`scope.include_visual=true` is also required.

Image notes retain album order and stable per-image resource IDs. The response
reports discovered/read/uncovered image counts; unknown total is represented
as unknown. Video ASR is never run for an image note.

## Alternatives Considered

1. Reuse the website backend's existing discovery and video-batch artifact
   endpoints. This already supports creator discovery and batch exports, but it
   requires the website service, project identity, and authentication, so an
   installed independent Skill would not work on its own.
2. Extend standalone MCP with bounded discovery and checkpoint tools while the
   host model orchestrates selection, reads, and artifact composition. This
   reuses the independent reader, preserves the model's role, and supports
   multi-turn clarification and recovery. **Recommended.**
3. Make one `envoy_read` call automatically process every post on a profile.
   This is simple to invoke but processes irrelevant items before the model can
   screen titles, makes user confirmation awkward, and weakens per-item budget
   and recovery behavior.

## Access And Evidence Boundaries

- Publicly readable profile items only. If the page requires user login or
  verification, expose the normal isolated-browser action; never import
  personal cookies or bypass the challenge.
- Candidate titles and publication metadata are untrusted selection hints.
  They are not proof of spoken, on-screen, location, or employer facts.
- A video result combines page chapters, subtitles/ASR, and optional sampled
  frames as distinct evidence. Sparse frames never imply complete visual
  coverage.
- Per-item errors do not discard successful items. The final artifact lists
  each item's status and provenance.
- The model, not Envoy, decides which titles match the user's intent, whether
  evidence supports a claim, and how to produce the requested artifact.

## Scope

Included in the first implementation:

- Public Douyin creator/profile works pages.
- Public video posts and public image-note posts.
- Bounded pagination and publication-time filtering when metadata is
  available.
- Title/metadata selection by the host model after optional clarification.
- Per-item read results, material reuse, checkpoint/resume, and artifact
  composition guidance.

Not included:

- Live streams, private/follower-only posts, comment/follower harvesting, or
  account analytics.
- CAPTCHA solving, signature forging, device impersonation, or personal-cookie
  extraction.
- Envoy-side semantic ranking, location verification, company matching, or
  final artifact composition.

## Acceptance Criteria

- `envoy_discover` accepts a public Douyin profile URL and returns a bounded
  ordered candidate page with stable IDs and distinct video/image-note types.
- A short share URL and a canonical profile URL resolve to the same collection
  identity when they identify the same profile.
- Pagination resumes without duplicates or skipped positions, subject to the
  source site's publicly exposed ordering.
- The model can clarify missing date/count/format decisions, select from
  titles, and read only the chosen candidates.
- `envoy_collection_checkpoint` survives process restart, idempotently updates
  per-item state with revision checks, and allows the host to resume unfinished
  candidates without overwriting concurrent updates.
- Repeating an operation ID after a crash returns the existing completed
  material without repeating download or ASR; a new host context can replay
  cached evidence under a fresh bounded response budget.
- Dynamic pagination replays a persisted candidate revision and never claims
  that an unknown continuation state is exhausted.
- Cumulative collection budgets stop additional work with an explicit cursor,
  usage, and stop reason.
- Media/ASR/frame evidence is tied to the requested candidate; unbound
  recommendation/ad media is not processed.
- `include_visual=false` yields zero frame extraction even when the host
  advertises frame capability.
- Multi-image notes preserve order and report unknown/remaining album coverage;
  they never enter the video ASR pipeline.
- Video and image-note items route through their existing source-specific
  readers; a blocked item does not erase completed item materials.
- The host can compose a user-requested Markdown or other supported artifact
  with per-item source URLs, ASR/subtitle timestamps, visual observations, and
  explicit coverage gaps.
- Deterministic fixtures cover creator-page discovery, redirects, duplicate
  entries, mixed video/note pages, missing publication dates, pagination,
  access challenges, partial item reads, and resume behavior.
- A final installed-runtime acceptance uses a public Douyin creator page and a
  public image-note/video sample where network access allows. The acceptance
  record distinguishes live-site results from mocks and extractor-only tests.
