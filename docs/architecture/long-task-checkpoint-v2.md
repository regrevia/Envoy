# Long-task checkpoint v2: media state, evidence, and resume

Status: implementation notes for the checkpoint-v2 work on branch HEAD `419969f`
(working tree also carries `long_task.py`, `tasks.py` and test edits). Evidence
level for this document is **unit_checked**: every statement below is tied to a
file and line in this working tree or to a test that was run locally on
2026-11-01. No end-to-end long-media run exists yet; the acceptance document
(`docs/acceptance/long-video-acceptance-2026-11-01.md`) records all scenarios as
`not_run`.

Code anchors:

- `packages/envoy-core/src/envoy_core/long_task.py` — checkpoint protocol v2.
- `packages/envoy-cli/src/envoy_cli/tasks.py` — `create` / `apply` / `next` wiring.
- `packages/envoy-cli/src/envoy_cli/media.py` — the ASR checkpoint cache, which is
  a *different* v2 (`asr-checkpoint/v2`) and must not be confused with the
  checkpoint protocol version in this document.
- `tests/fixtures/long_task/` — four reader-shaped receipts captured from the real
  reader with fake ASR/subtitles; see the fixture README for what is real.

## 1. Version and compatibility

`CHECKPOINT_VERSION = 2` (`long_task.py:36`), `SUPPORTED_CHECKPOINT_VERSIONS = (1, 2)`
(`long_task.py:38`). `from_checkpoint` (`long_task.py:750`) refuses any other
version with an explicit `unsupported checkpoint_version` error rather than
guessing.

Compatibility with v1 is one-way and lossless: a v1 payload has no media fields,
so the loader leaves them unset (`_as_float(...)` on a missing key yields `None`,
counters stay 0). The material therefore folds as a text material until the next
receipt arrives; media progress from a v1 checkpoint is *unknown*, not zero. A
v1 task resumed on this build will not be marked finished while
`media_pending()` can be inferred from the new receipt, which is exactly the bug
v2 fixes (section 3). After the first v2 delivery the serialized payload is v2
and is rejected by a v1-only reader — there is no down-conversion.

## 2. Two levels of state, and which one is the truth

`MaterialCheckpoint.state` mirrors the word the last receipt used:
`result` in `{complete, partial, ready, blocked}` maps to `COMPLETED`, `PARTIAL`,
`PARTIAL`, `BLOCKED` (`long_task.py:398-415` region in `with_material`/fold).
That word describes *the call*, not the material. A finite video whose transcript
already covers the whole timeline still comes back as `result: "ready"` from the
reader, so the material's `state` stays `partial`.

The evidence functions are the truth a caller must read:

| function | says | anchor |
|---|---|---|
| `media_pending()` | a media material still has untranscribed speech | `long_task.py:296-314` |
| `remaining_chars()` | text still unseen (unbounded `None` when the total is unknown) | `long_task.py:291-295` |
| `remaining_seconds()` | timeline seconds still to transcribe, or `None` when there is no per-second evidence | `long_task.py:316-333` |
| `resume_kind()` | `read` while media is pending, otherwise `expand` | `long_task.py:335-338` |
| `is_finished()` | this material needs no further call | `long_task.py:354-360` |

`is_finished()` is, in order: `if self.media_pending(): return False`; then
`state in FINISHED_STATES` → `True`; then
`remaining_chars() == 0 and state in {PARTIAL, CHECKPOINTED, RESUMING}`.

`overall_state(task)` (`long_task.py:660-687`) also prefers evidence over words,
in this exact precedence:

1. no materials → `task.state` if open, else `CREATED`;
2. any material `BLOCKED` → `BLOCKED`;
3. any material `FAILED` → `FAILED`;
4. any material `CANCELLED` → `CANCELLED`;
5. task itself `CANCELLED` → `CANCELLED`;
6. `all(item.is_finished())` → `COMPLETED`;
7. every material in `FINISHED_STATES` → `COMPLETED` only if all are
   `COMPLETED`, otherwise `CANCELLED`;
8. any material in `{PARTIAL, CHECKPOINTED, RESUMING, READING}` → `PARTIAL`;
9. otherwise `CREATED`.

The consequence is intended: `get` can report the task `completed` while a
material's own `state` still reads `partial`. Worked example, from the committed
fixture `tests/fixtures/long_task/video_asr_complete.json` folded against the
current code: `result: "ready"`, `processed_until_seconds = 600` of
`duration_seconds = 600`, material `state = "partial"`,
`media_pending() = False`, `is_finished() = True`,
`remaining_seconds() = 0.0`, task state `completed`. Code that checks
`material.state == "completed"` is wrong; code must call `is_finished()`.

## 3. The three independent partial signals

`media_pending()` (`long_task.py:296-314`) is a union, not an `and`, and every
clause was added because some receipt dialect carries only that one field:

1. `transcription_status == "partial"` — the direct-video/ASR path
   (`reader.py:7898-7978`) always writes this status.
2. `unprocessed_seconds > 0` — only the browser page-video path writes
   `transcript_unprocessed_seconds` (`reader.py:2188-2205`).
3. `processed_seconds < total_seconds - 1.0` — the numeric comparison, for
   receipts that carry both numbers and no explicit partial status.

The union means no single missing field can make a partial transcript look
finished: the direct path has no `unprocessed_seconds`, the page path has no
`transcription_status`, and a status-only fixture would otherwise pass. The
0 seconds of the tolerance in clause 3 absorb float rounding between a
`processed_until_seconds` clamped to `duration + 1.0` and the duration itself.

For non-media kinds (`text`, `pdf`) `media_pending()` is always `False`; there is
no timeline to be pending on.

`remaining_seconds()` (`long_task.py:316-333`) returns `None`, not the whole
duration, when `kind` is not a media kind, when `total_seconds` is unknown, or
when `processed_seconds is None and not completed_seconds`. This is the
subtitles-only case: `video_subtitles.json` has `duration_seconds = 3600` and a
finished transcript but no per-second progress, so `remaining_seconds()` is
`None` — reporting 3600 s remaining would claim a finished material still has an
hour of ASR to do. The document read from the fixture is
`{"processed_seconds": null, "total_seconds": 3600.0, "is_finished": true,
"resume_kind": "expand"}`.

## 4. The ten media fields and where each is read

`with_delivery` (`long_task.py:362-524`) is the only writer. Fields, receipt key,
and read anchor:

| field | receipt key(s) | notes |
|---|---|---|
| `kind` | presence of `coverage.modalities.video` / `audio`; `_page_numbers` for PDF; else `text` | `long_task.py:465-469` |
| `total_seconds` | `video.details.duration_seconds`, falling back to text details | rounded to 3 dp; `long_task.py:426-430` |
| `processed_seconds` | max of the candidate list (section 5) | clamped to `duration + 1.0`; `long_task.py:434-443` |
| `completed_seconds` | `coverage.sampled_ranges` with `modality == "audio"` | merged ranges; `_audio_sampled_ranges` `long_task.py:184-206` |
| `transcription_status` | `video.details.transcription_status`, else text details, else carried forward | `long_task.py:470-485` |
| `transcript_source` | `video.details.transcript_source`, else carried forward | same region |
| `continuation_start_seconds` | `transcript_continuation_start_seconds` | carried forward when absent; `long_task.py:488-489` |
| `unprocessed_seconds` | `transcript_unprocessed_seconds` | carried forward when absent; `long_task.py:491-492` |
| `downloaded_bytes` | `video.details.media_downloaded_bytes`, else `usage.local.downloaded_bytes` | accumulated per media call; `long_task.py:456-462` |
| `media_calls` | increment when a `video` or `audio` modality is present | `long_task.py:515` |

Two dialects feed this: the direct-video/ASR path writes plain
`processed_until_seconds` plus `usage.local.{asr_model, asr_duration_ms,
downloaded_bytes, media_download_ms}`; the browser page-video path writes the
`transcript_*` family and an empty `usage.local`. The fold reads both, and the
fixtures cover both (`video_asr_partial.json`, `page_video_asr_partial.json`).

## 5. Processed-cutoff candidate order

The cutoff is the **maximum** over these candidates
(`long_task.py:433-440`), not a first-match:

1. `video.details.processed_until_seconds` — direct-video/ASR dialect;
2. `video.details.transcript_processed_until_seconds` — page-video dialect, the
   honest transcription cutoff;
3. `video.details.transcript_delivered_until_seconds` — how much transcript
   reached the caller;
4. the `end_seconds` of every audio `sampled_range`.

Why the delivered key alone is wrong: a budget-truncated response delivers less
text than it transcribed. `transcript_continuation_start_seconds` is the end of
the last *delivered* ASR segment (`reader.py:2157-2159`), i.e. a text-delivery
cutoff, while `transcript_processed_until_seconds` is the transcription cursor.
If the fold read the delivered key only, `processed_seconds` would be too small,
`remaining_seconds()` too large, and the resume range would start too early and
re-transcribe audio that was already processed. The comment at
`long_task.py:410-421` states this as the bug that "let a 120 s transcript of a
3600 s video count as finished"; the max over all candidates is the fix.

Note the identity gap in section 9: the resume request uses
`continuation_start_seconds` when present, which is the *delivered* cutoff, so
the range that comes back after a budget-truncated round starts at the delivered
position, not the processed position. That is a deliberate safety choice (no
skipped text) at the cost of some duplicate transcription; the size of that
duplicate is not measured yet (section 10).

## 6. Fold semantics

`with_delivery(receipt, offset=0, ...)` folds one receipt into the checkpoint and
never decreases what is known:

- text delivery: `delivered_ranges` is the union with `(offset, offset + delivered_chars)`;
  `total_chars` and `next_offset` update from the receipt; `page_count` /
  `pages_seen` only grow.
- media: `completed_seconds` merges the new audio ranges; `processed_seconds` is
  the max of old and new (clamped); `downloaded_bytes` **adds** the per-call
  counter; `media_calls` increments once per media-bearing receipt.
- `COMPLETED` → `PARTIAL` downgrade (`long_task.py:517-524`): if the receipt said
  `result: "complete"` (material state `COMPLETED`) but
  `media_pending()` is still true afterwards, the material is downgraded to
  `PARTIAL`. `complete` describes the call, not the timeline, so an unfinished
  transcription must not be able to claim completion. Other states are untouched.

Duplicate accounting of refolded receipts (observed, not fixed): folding the same
`video_asr_partial.json` twice raises `downloaded_bytes` from 1000 to 2000
(`media_calls` 1 → 2), because the accumulator has no per-call identity. This is
a lower bound on the duplicate-download metric, not a correctness problem for
resume, and it is listed as a gap in section 10.

## 7. Resume routing: read versus expand

`resume_request` (`long_task.py:535-609`) fixes the branch order before building
anything:

1. `observed_revision` given and different from the stored
   `source_revision` → `TaskBlocked("source_revision_changed")`
   (`long_task.py:546-557`). A changed source is not resumed on stale progress.
2. `is_finished()` → `TaskBlocked("already_finished")` (`long_task.py:558-559`).
3. media kind and `media_pending()` → **read** request (`long_task.py:560-604`):
   `protocol_version: "envoy.read/1"`, `request_id`, `source.url`, `goal`,
   `budget.{max_response_chars, max_total_response_chars}`,
   `retention.raw_media = "persistent"`, `retention.derived_ttl_seconds = 86400`,
   and `scope = {"kind": "range", "start_seconds": start, "end_seconds": total}`
   only when `total_seconds` is known and greater than `start`. `start` is
   `continuation_start_seconds`, falling back to `processed_seconds`, rounded to
   3 dp. The request carries no `material_id` and no `source_revision`, because a
   read request has no such keys.
4. no material yet → plain read of `source.url` (with `goal` when given).
5. otherwise → **expand**: `protocol_version`, `request_id`, `material_id`,
   `offset`, `max_chars`, `max_total_response_chars`. No source, no revision, no
   goal.
6. non-media material with no `resume_offset()` → `TaskBlocked("no_resume_offset")`.

`tasks.py:192-198` calls this with `goal=task.goal`, so a resumed media read
keeps the task's stated goal. `media_progress()` is attached to the `next`
envelope as `media`, and `remaining_seconds` as a sibling field, so the caller
can report progress without re-deriving it.

The important consequence: for a media task, "resume" means "ask the reader for
the untranscribed remainder of this URL", not "reuse the old material". The
reader has to rediscover the source, the effective download, and the ASR cursor
in a single call.

## 8. What the receipt-shape fixtures prove

Four committed receipts in `tests/fixtures/long_task/`, each
`_provenance.kind = "captured_shape"`, `_provenance.signal = "synthetic"`:
the receipt shape is produced by the real reader code path, the ASR/subtitle
result inside it is a monkeypatched fake. `page_video_asr_partial.json` is the
only one carrying the real page-path keys
(`transcript_processed_until_seconds`, `transcript_delivered_until_seconds`,
`transcript_unprocessed_seconds`). `tests/cli/test_capture_long_task_fixtures.py`
regenerates all four into a temporary directory and diffs the body against the
committed JSON, so a reader-shape change fails CI instead of ageing the fixtures
silently; provenance is checked by schema only (`commit`, `generated_at` are
capture-time facts).

Folded documents against the current code (all four verified in this working
tree):

| fixture | state | kind | processed/total | media_pending | is_finished | resume_kind | resume_request |
|---|---|---|---|---|---|---|---|
| `video_asr_partial.json` | partial | video | 120/3600 | True | False | read | range read, `scope 120.0-3600.0` |
| `page_video_asr_partial.json` | partial | video | 120/3600 | True | False | read | range read, `scope 120.0-3600.0` |
| `video_asr_complete.json` | partial | video | 600/600 | False | True | expand | `TaskBlocked("already_finished")` |
| `video_subtitles.json` | partial | video | None/3600 | False | True | expand | `TaskBlocked("already_finished")` |

Before the media branch landed, `page_video_asr_partial.json` (no text modality,
no `total_chars`) hit `TaskBlocked("no_resume_offset")`. The branch now routes it
from `continuation_start_seconds`, which is why the second row reads `read`.

## 9. Gap: ASR checkpoint identity includes the requested range

The media-layer ASR cache is keyed by `_asr_identity` (`media.py:1491-1515`).
The digest input is
`asr-checkpoint/v2|{identity_source}|model={model_name}|decode={ASR_DECODE_CONFIG}|range={start:.3f}-{end:.3f}`
(`media.py:1513`), where `identity_source` is `revision:<sha>` when a trusted
source revision exists and `content:<sha>` otherwise (`media.py:1504-1511`).
`ASR_DECODE_CONFIG = "sr=16000,task=transcribe,temperature=0,fp16=False,condition_on_previous_text=False"`
(`media.py:1471-1473`). The cache file is
`asr_checkpoint_dir()/f"{identity}.json"` (`media.py:1732`), honouring
`ENVOY_ASR_CHECKPOINT_DIR` or `~/.envoy/asr`.

Because the range is in the identity, the cache is only reusable for the exact
same requested range:

- same bytes, same range, different temp path → same identity → same checkpoint;
  proven by `tests/cli/test_media_asr_resume.py::test_identical_bytes_in_two_temp_dirs_hit_the_same_checkpoint`
  (`:290`);
- changed range → different identity → different file → `carried = []` and a
  fresh transcription starting at the new `start` (`media.py:1738-1770`);
- changed source revision → `revision:` vs `content:` identity change → old
  progress must be reported blocked, covered by
  `tests/cli/test_media_asr_resume.py::test_a_changed_source_revision_rejects_old_progress`
  (`:319`).

So "resume at 120 s of a 3600 s video" writes a new checkpoint whose first 60 s
window aligns to 120 s; it does **not** reuse the segments stored under the
0-3600 s identity. The consequence for the product claim is precise: changing
one time range and reusing the other segments is **not implemented**. A
continuation trades that reuse for correctness (no stale range assumption), and
the duplicate-transcription cost is bounded by one ASR window (60 s,
`media.py:1262`, `chunk_duration = min(60, end - cursor)` at `media.py:1415`)
plus any processed-but-undelivered audio; it is not measured end to end.

Two v2s live in different layers and must not be conflated: `asr-checkpoint/v2`
(`media.py:1513`) keys the ASR segment cache; the checkpoint protocol v2
(`long_task.py:36`) keys the task material state. The first is range-scoped; the
second is not.

## 10. Known gaps

- Range-scoped ASR identity (section 9): no reuse across a changed range, so the
  "reuse the already-transcribed segments" idea is unproven and unimplemented.
- Refold duplicate accounting (section 6): `downloaded_bytes` and `media_calls`
  accumulate per receipt with no call identity, so replaying a receipt inflates
  the duplicate-download metric (1000 → 2000 on the fixture).
- No per-task budget accounting: budget lives in each request, not in the
  checkpoint, so "budget used / remaining" cannot be read from the task.
- No page-level evidence index and no segment/frame visual index; the checkpoint
  knows text offsets, pages seen, and audio ranges only.
- No phase-level checkpoints and no per-stage rate measurement: success rates per
  stage (probe, download, ASR, extraction) are not jointly measured anywhere; the
  only existing aggregate is link-coverage per-link verdicts
  (`docs/experiments/long-task-metrics-2026-11-01.md`).
- A media range read preserves already-delivered text only in the sense that the
  caller keeps the old response; the request carries no text `offset`, so the
  reader is not told which characters the caller already has.
- `retention.derived_ttl_seconds = 86400` is hardcoded in the media request
  (`long_task.py`, media branch); it is not configurable per task.
- Whether the reader honours a mid-stream `scope.start_seconds` on a real site is
  not proven: the page-video fixture's `unresolved` text suggests the value, and
  the checkpoint layer builds the scope, but no test drives a real range read
  from a non-zero start.

## 11. Evidence status

| claim | level | evidence |
|---|---|---|
| media receipts fold to a pending, unfinished checkpoint | unit_checked | `tests/cli/test_task_checkpoint_cli.py::test_a_partial_video_receipt_resumes_with_a_range_read` (`:276`), `::test_a_media_receipt_with_no_text_modality_still_folds` (`:390`) |
| finished media is blocked, not resumed | unit_checked | `tests/cli/test_task_checkpoint_cli.py::test_a_finished_video_is_blocked_by_the_core_not_the_cli` (`:314`); `tests/cli/test_task_dispatch_flow.py::test_a_finished_video_is_not_resumed_again` (`:121`) |
| text material still resumes with an expand | unit_checked | `tests/cli/test_task_checkpoint_cli.py::test_a_text_material_still_resumes_with_an_expand` (`:338`) |
| `get` exposes the media axis | unit_checked | `tests/cli/test_task_checkpoint_cli.py::test_get_shows_the_media_axis_and_nulls_it_for_text` (`:360`) |
| the four receipt shapes are reproducible from the reader | unit_checked | `tests/cli/test_capture_long_task_fixtures.py` (4 cases, run 2026-11-01) |
| a real 60-minute video resumes after an interruption | not_run | no whisper locally, no network; see acceptance doc |
| a changed range reuses prior segments | not_run and unimplemented | section 9 |
