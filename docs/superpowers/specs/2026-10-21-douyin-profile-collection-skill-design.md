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
   ASR and optional sparse frames; image-note posts use the existing page/image
   reader path. Per-item source blocks, timestamps, material IDs, and coverage
   gaps remain separate.
5. After each item, the host checkpoints its state. On interruption it resumes
   from the collection manifest, reuses completed material IDs, and processes
   only unfinished items.
6. The host composes the requested artifact from selected evidence, preserving
   source links and time locators and reporting skipped, blocked, or partial
   items. It does not imply that title matching or sparse frames are complete
   source verification.

## Architecture

### Independent Skill/MCP path

Add `envoy_discover` to the standalone MCP surface and the `envoy discover`
CLI. It uses the existing yt-dlp extractor and isolated-browser discovery
patterns, with static public metadata attempted first and headless browser
discovery used when needed. It never reads a personal browser profile or
automates login, CAPTCHA, or anti-bot verification.

Add `envoy_collection_checkpoint` to persist and retrieve a bounded collection
manifest. The manifest contains the canonical profile URL, collection ID,
discovery revision, page cursor, candidate metadata, selected candidate IDs,
per-item status, and completed material IDs. It contains no cookies, signed
media URLs, or downloaded media. Apply the existing local retention policy and
file permission rules.

The host remains the orchestrator. It filters candidates by title/metadata,
calls `envoy_read` per selected post, and writes the final artifact with its
normal document/artifact tools. This keeps Envoy useful without the website
backend and lets the host adapt the final format to the user's request.

### Candidate contract

Each candidate has:

- A stable `candidate_id` derived from its canonical public post URL.
- `source_url`, canonicalized and safe for `envoy_read`.
- `item_type`: `video` or `image_note`.
- `title`, optional `published_at`, optional `duration_seconds`, and creator
  display name when publicly exposed.
- `position` within the discovered page and optional thumbnail URI.

Discovery is bounded. `limit` is capped at 100 per page, and a continuation
cursor is returned only when the platform exposes another page. An optional
publication-time range can be applied when timestamps are available; otherwise
the response marks the range filter as incomplete rather than silently
discarding candidates. Duplicate URLs are removed while preserving platform
order.

### Checkpoint states

Each selected item moves through `selected`, `processing`, `completed`,
`partial`, `blocked`, or `skipped`. Checkpoints retain the last completed
material ID and a redacted failure summary. A repeated checkpoint update is
idempotent. A resumed host can fetch the manifest, continue discovery from its
cursor, and process only items without a terminal state.

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
  per-item state, and allows the host to resume only unfinished candidates.
- Video and image-note items route through their existing source-specific
  readers; a blocked item does not erase completed item materials.
- The host can compose a user-requested Markdown or other supported artifact
  with per-item source URLs, ASR/subtitle timestamps, visual observations, and
  explicit coverage gaps.
- Deterministic fixtures cover creator-page discovery, redirects, duplicate
  entries, mixed video/note pages, missing publication dates, pagination,
  access challenges, partial item reads, and resume behavior.
- A final installed-preview acceptance uses a public Douyin creator page and a
  public image-note/video sample where network access allows. The acceptance
  record distinguishes live-site results from mocks and extractor-only tests.
