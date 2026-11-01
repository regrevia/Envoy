# Long-task receipt fixtures

These four JSON files pin the **shape** of receipts the reader really produces for
long audio/video material, and the checkpoint-v2 fold of that shape. They are not
hand-written: each file is a dict produced by the real reader code path
(`envoy_cli.reader`) driven against a pinned URL at git commit `419969f`.

What is real and what is not:

- real: the receipt's key structure, the modality/coverage block, the usage block,
  the text/source/lease fields, the character counts, and the JSON the checkpoint
  code folds;
- replaced: the ASR result (or the subtitle track) inside that dict. Whisper is
  not installed in the capture environment, so every capture monkeypatches
  `reader.transcribe_audio_file` (and, for the subtitle file,
  `reader.extract_public_subtitles`) with a fake returning a fixed transcript
  covering a fixed time range. Each file records the replaced call and its fake
  output under `_provenance`, and each file is labelled
  `_provenance.kind = "captured_shape"`, `_provenance.signal = "synthetic"`.

What the fixtures therefore prove: that a receipt of this shape folds into this
checkpoint state (media pending or not, finished or not, range read or expand).
What they do **not** prove: transcription quality, cue timing, subtitle quality or
coverage, duplicate avoidance on a real resume, or recovery after a real process
interruption. Those need the acceptance run in
`docs/acceptance/long-video-acceptance-2026-11-01.md`, which is `not_run`.

## Files as committed

Bytes and SHA-256 are of the files on disk at commit `419969f` (working tree,
2026-11-01). Recompute before trusting this table; it is a snapshot, not a
contract.

| file | bytes | sha256 |
|---|---|---|
| `video_asr_partial.json` | 10458 | `1c69c798525f7e05c52f8563d70a6f35cacef6dad96b447960147d8bd2f98bb0` |
| `video_asr_complete.json` | 10254 | `824111a48d35554c22cacdbf0e639fa49eef5996989fa72d8678139b0b4b8944` |
| `page_video_asr_partial.json` | 43322 | `0f6a823c6b57f071306e7402c348c084212ba369138a93b455f1f79a512cd7ab` |
| `video_subtitles.json` | 9117 | `3c35c212cdd381595a5dd043a51e5d952c04cfab8272e1c5a5adb0575ab08354` |

This table is the output of the guard test, not a hand-maintained list:
`packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_capture_long_task_fixtures.py -q -s`
prints one `FIXTURE <name>.json bytes=<n> sha256=<hex>` line per file. Paste new
lines from that output and do not edit the numbers here directly.

Per-file provenance (`kind` is `captured_shape` and `signal` is `synthetic` in all
four; only the replaced call differs):

| file | reader path | replaced call / fake | `_provenance.proof_scope` (abridged) |
|---|---|---|---|
| `video_asr_partial.json` | direct video, `text.source = "video_asr"`, `processed_until_seconds = 120`, `duration_seconds = 3600` | `reader.transcribe_audio_file` -> partial status, 60 cues covering 0-120 s, `next_seconds = 120.0` | proves the receipt folds into a media-pending checkpoint that is not finished and whose resume routing is a range read; not transcription quality, cue timing, duplicate avoidance, or recovery after a real interruption |
| `video_asr_complete.json` | direct video, `processed_until_seconds = 600` of 600 s, `result = "ready"` | `reader.transcribe_audio_file` -> complete status, transcript covering all 600 s | proves the finished shape of a video material whose transcript covers the whole duration; not that real ASR ever reaches this state |
| `page_video_asr_partial.json` | page-embedded video, `transcript_processed_until_seconds = 120.0`, `transcript_delivered_until_seconds = 120.0`, `transcript_unprocessed_seconds = 3480.0`, `duration_seconds = 3600` | `reader.transcribe_audio_file` inside page video evidence, plus the page media/duration probe | proves the fold of the only reader dialect carrying the `transcript_*` keys; not live-site extraction, transcription quality, or that the delivered-text cutoff equals the transcription cursor |
| `video_subtitles.json` | subtitle-only video, `text.source = "manual_subtitles"`, `transcription_status = null`, `processed_until_seconds = null`, `duration_seconds = 3600` | `reader.extract_public_subtitles` -> 3600 s caption track with `automatic = False` | proves a subtitle-only receipt carries no per-second transcript progress; not subtitle track selection on a live site or subtitle coverage of the whole duration |

`page_video_asr_partial.json` is the only fixture carrying the real page-path keys
`transcript_processed_until_seconds`, `transcript_delivered_until_seconds`, and
`transcript_unprocessed_seconds`. The direct-video path writes none of them, so a
fold bug in the page path cannot be caught by the other three files or by the
direct path's tests.

## How to regenerate

The capture script is a module of four pytest cases, not a standalone command, so
the same function that captures a receipt also asserts its shape:

```bash
cd /Users/apple/Desktop/2026/Envoy
ENVOY_CAPTURE_OUT=/tmp/long-task-fixtures \
  packages/envoy-cli/.venv/bin/python -m pytest tests/fixtures/long_task/capture_receipts.py -q
```

Each case prints `CAPTURED <name>.json`. `_fixture_dir()` writes to
`ENVOY_CAPTURE_OUT` when set, otherwise to this directory; committed files must
only be produced with the variable unset. `generated_at` comes from
`datetime.now(timezone.utc)`, so a capture after 16:00 Asia/Shanghai records the
previous UTC date: the committed files say `2026-10-31` while the docs dated
`2026-11-01` are local (+08:00) dates. Both are correct; do not retcon one into
the other.

Determinism: the receipt body is deterministic for a fixed reader at a fixed
commit. `tests/cli/test_capture_long_task_fixtures.py` re-runs all four captures
into a temp directory and diffs them against the committed files:

```bash
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_capture_long_task_fixtures.py -q
```

Every field except `_provenance` is compared for equality. `_provenance` is
compared by key set only, because `commit` and `generated_at` are capture-time
facts: a later commit or a different day must not fail the test, while a changed
kind, signal, replaced call, fake output or proof scope must. If the reader's
shape changes, the test fails with the differing key list instead of letting the
fixtures age silently.

## Two receipt dialects

Both are produced by the real reader and both are folded by
`MaterialCheckpoint.with_delivery` (`packages/envoy-core/src/envoy_core/long_task.py:404-524`):

| feature | direct-video / ASR path | page-embedded video path |
|---|---|---|
| transcript source | `reader.transcribe_audio_file` | browser media evidence, ASR on downloaded audio |
| processed cutoff | `video.details.processed_until_seconds` | `video.details.transcript_processed_until_seconds` |
| delivered cutoff | absent | `video.details.transcript_delivered_until_seconds` |
| unprocessed remainder | absent | `video.details.transcript_unprocessed_seconds` |
| continuation hint | absent | `video.details.transcript_continuation_start_seconds` |
| audio ranges | `coverage.sampled_ranges`, `modality: "audio"` | empty list |
| download accounting | `usage.local.downloaded_bytes` (+ `asr_model`, `asr_duration_ms`, `media_download_ms`) | `video.details.media_downloaded_bytes`, `usage.local` empty |
| transcription status | `video.details.transcription_status` (`"partial"` / `"complete"`) | absent |

Because the direct path has no `transcript_unprocessed_seconds` and the page path
has no `transcription_status`, `media_pending()` is a three-way union; code and
rationale are in `docs/architecture/long-task-checkpoint-v2.md` section 3.

## Fields checkpoint v2 reads

Top level: `material_id`, `revision`, `result`, `text.{source, total_chars,
delivered_chars}` (direct path), `coverage.modalities`, `coverage.sampled_ranges`,
`usage.local`.

`video.details`: `duration_seconds`, `transcription_status`, `transcript_source`,
`processed_until_seconds`, `transcript_processed_until_seconds`,
`transcript_delivered_until_seconds`, `transcript_unprocessed_seconds`,
`transcript_continuation_start_seconds`, `requested_start_seconds`,
`requested_range.start_seconds`, `media_downloaded_bytes`.

Read but not meaningful for progress: `speech_seconds`, `media_probe_bytes`,
`browser_media_url` (page path only; they describe the source file).

## Known anomalies

- `video_asr_complete.json` has `result: "ready"` and yet the folded material's
  `state` is `partial`: state mirrors the receipt word, `is_finished()` reads the
  evidence. Intended, documented in the architecture doc section 2.
- `video_subtitles.json` has `processed_until_seconds = null` and
  `transcription_status = null`; `remaining_seconds()` is `None`, not 3600.
- The fake ASR in `video_asr_partial.json` reports `next_seconds = 120.0` and 60
  cues covering 0-120 s. `next_seconds` belongs to the fake payload, not to a
  reader contract; the checkpoint reads the coverage/processed fields only.
- Refolding any file twice doubles `downloaded_bytes` and increments
  `media_calls`; a known accounting gap, not a fixture defect.
