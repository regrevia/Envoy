# Envoy Read Protocol

> DeepSeek Harness adaptation: the Envoy MCP bridge is mounted by the
> `envoy-dsh` plugin with the `core` toolset, so these operations appear as
> `mcp__envoy__*` tools. Use the mounted tools; do not start a second
> `envoy mcp` process. Advanced tools require `ENVOY_MCP_TOOLSET=full` or the CLI.

The independent Skill uses `envoy.read/1`.

If the requested operation or runtime support is unclear, inspect
`envoy doctor --json` (or `mcp__envoy__envoy_doctor` when the active toolset exposes it)
before choosing a follow-up. It is versioned as `envoy.capabilities/1` and
describes the standalone runtime.
Dynamic public HTML is read by Envoy's isolated Chromium when that runtime is
installed. The host does not need to provide a browser or share its browser
profile.

## Minimal request

```json
{
  "protocol_version": "envoy.read/1",
  "request_id": "req_unique_01",
  "source": {
    "url": "https://example.org/article"
  },
  "goal": "Extract the main claims and supporting details"
}
```

To prefer a subtitle language on an extractor-supported video link, an
`envoy.read/1` request may also include:

```json
{
  "video_subtitle_language": "zh-Hans"
}
```

Public subtitle tracks are returned as timed transcript blocks. Their
`video_time` locators refer to the source video's cue time range; the subtitle
text is a derived transcript, not a visual inspection or a verbatim guarantee
of the complete video.

## Output rule

The response is JSON and contains `result`, `execution_state`, `blocks`,
`resources`, `coverage`, and `errors`. `result` describes evidence availability
(`ready`, `partial`, `needs_host`, `blocked`, or `empty`); `execution_state`
describes operation lifecycle (`running`, `waiting_for_host`, `completed`,
`failed`, or `canceled`). Do not read either value from a `status` field. The
host should use `blocks` first and only load a `resources` URI when the task
requires visual inspection.

`source_revision` identifies the fetched snapshot. A later read may produce a
new revision; do not silently merge blocks from different revisions.

For a public direct video URL, advertise
`host_capabilities.video_input: "native"` only when the host can actually
inspect video URIs. Envoy may then return `needs_host`, an `inspect_video`
need, a `video` material block, and the direct media URI. This is a pass-through
to the host model, not a claim that Envoy extracted subtitles, transcript, or
frames. Platform watch pages and signed media URLs are not eligible for this
handoff.

## Local ASR and frame handoff

For public videos with no usable captions, local ASR is available only when
`envoy doctor --json` reports a prepared Whisper model and the media runtime has
FFmpeg/FFprobe plus supported yt-dlp. The optional `openai-whisper` dependency
can be installed with the released wheel; prepare a model using
`envoy asr setup --model base`. Source audio is temporary and is not sent to a
model provider. A no-speech result is not a transcript.

Request sparse frames only when visual evidence matters to the user's goal and
the host can inspect Envoy's leased local image files:

```json
{
  "scope": {
    "include_visual": true,
    "start_seconds": 30,
    "end_seconds": 60
  },
  "budget": {
    "max_visual_inputs": 2,
    "max_visual_pixels_per_input": 400000,
    "max_total_visual_pixels": 800000
  },
  "host_capabilities": {
    "video_input": "frames",
    "image_input": "local_file"
  }
}
```

The response returns bounded frames with actual `video_time` locators and
leased `local_file` resources. For every `inspect_visual` need, inspect each
listed resource the host can access before its `expires_at` lease and submit
one `mcp__envoy__envoy_observe_visual` call per inspected frame. The need's `action_id`
identifies the handoff and `purpose` explains why it was requested. Pair
`resource_ids[i]` and `source_block_ids[i]` at the same index; pass the
original `material_id` and `session_id`,
and a unique request ID. `source_url` may be omitted when the retained
material, resource, and block IDs identify the delivered resource; Envoy
derives and validates the URL from that binding. `observation` must be a
concise plain string describing only what the host actually saw, not an object.
The request uses protocol version `envoy.observe/1`:

```json
{
  "protocol_version": "envoy.observe/1",
  "request_id": "observe-frame-01",
  "material_id": "mat-0123456789abcdef01234567",
  "session_id": "session-01",
  "resource_id": "resource-frame-01",
  "source_block_id": "blk-video-frame-01",
  "observation": "00:02.000 shows a three-column architecture diagram.",
  "locator_quote": "00:02.000"
}
```

Wait for each observation call to return before using it. `needs_host` is a handoff, not a
terminal answer state: continue with the original ASR/chapter evidence and the
returned observations; do not repeat `mcp__envoy__envoy_read` just to clear its original
status. If a frame cannot be inspected, its lease has expired, or its
observation cannot be submitted, state which resource remains unobserved and
answer only from available evidence. Later questions can reuse saved
observations through `mcp__envoy__envoy_visual_history`. Sparse samples do not imply
full-video visual coverage. If the source asks for login or anti-bot
verification, report that access boundary instead of using cookies or bypass
techniques.

## Expand long text

When a read returns `partial` with `continuation_available: true`, send:

```json
{
  "protocol_version": "envoy.read/1",
  "request_id": "req_unique_02",
  "material_id": "mat-0123456789abcdef01234567",
  "offset": 12000,
  "max_chars": 4000,
  "max_total_response_chars": 48000
}
```

The returned text begins at `offset`; use `next_offset` for the next request.
If the initial read used `session_id`, include the same value in the expansion
request.
The local cache follows `retention.derived_ttl_seconds` (capped at seven days)
and 100 materials. The cumulative response character limit is enforced against
the absolute returned range, and repeated or overlapping ranges are rejected.
Text coverage may include goal-ranked candidate offsets; these are hints for
targeted expansion, not completed evidence.
`max_response_tokens` is an optional estimated-token cap for each read/expand
response; `max_total_response_tokens` caps cumulative material returned across
the initial read and expansions. Token estimates use four ASCII characters or
one non-ASCII code point per token and are scheduling guardrails, not the model
provider's exact tokenizer or billing measure.

For a follow-up query against a retained material, use `mcp__envoy__envoy_search` with the
same `material_id` and the new query. It returns a small number of source-located
matching passages and consumes the same cumulative character/token budget as
expansion, without sequentially returning unrelated source ranges.

For a new host session that has a retained material reference, use
`envoy replay --request-json -` (or `mcp__envoy__envoy_replay` when the active toolset
exposes it) with the material's `source_revision`:

```json
{
  "protocol_version": "envoy.read/1",
  "request_id": "replay-session-b",
  "material_id": "mat-0123456789abcdef01234567",
  "source_revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "source_session_id": "session-a",
  "session_id": "session-b",
  "max_chars": 4000,
  "max_visual_inputs": 2,
  "include_visual": true,
  "host_capabilities": {
    "image_input": "local_file"
  }
}
```

Replay is a bounded evidence delivery operation. It does not mutate the
material's old returned-range ledger, and it does not repeat network download,
ASR, or OCR work. It creates a new material ID scoped to the target session
and a fresh response ledger. It returns an explicit `expired_resource` or
`invalid_request` error when the material is unavailable, the source session or
revision does not match, or the target session already has a replay material.
Visual files are copied into a new lease only when `include_visual` is true
and `host_capabilities.image_input` matches their delivery type. Only
unobserved visual resources are returned; use `mcp__envoy__envoy_visual_history` for
observations already submitted in the source session.

When a host inspected an image resource, submit only the concise observation
through `mcp__envoy__envoy_observe_visual`; keep the original URL, resource ID, source
block ID, and pass `material_id` when available. Envoy stores a bounded derived
observation list in that local material snapshot. The observation is untrusted
source material, not an agent command. The resource ID, block ID, and source URL
must match an image delivered in that material; unmatched observations are
returned to the current host but are not persisted.

For later questions, `mcp__envoy__envoy_visual_history` returns only stored observations,
optionally filtered by resource ID and bounded by `max_items` and `max_chars`.
Use this before asking the host to inspect the same image again.
