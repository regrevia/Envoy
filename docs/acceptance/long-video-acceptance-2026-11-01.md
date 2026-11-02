# Long video / long task acceptance, 2026-11-01

Status: **not_run**. This document fixes what an acceptance run must do, on which
inputs, and what it must print; none of the seven scenarios has been executed.
Recorded against working tree `419969f` on 2026-11-01 (Asia/Shanghai).

Evidence level: `not_run` for every scenario here. The only evidence that exists
for the checkpoint work is unit-level and shape-level, listed per scenario under
"What is checked instead". A fixture fold, a mocked transcript, or a unit test is
not acceptance for a long-media run and must not be quoted as one.

## Environment as found

- Offline. No live site is reachable, so every scenario that needs a real URL, a
  real video download, or a real PDF cannot start.
- ASR backend missing: `python -c "import whisper"` raises `ImportError` in
  `packages/envoy-cli/.venv`. `ffmpeg` and `ffprobe` are present at
  `/opt/homebrew/bin`.
- `scripts/run_large_video_delivery_e2e.py` additionally imports backend services
  (`app.services.crud_service`, `app.workers.jobs.preprocess_job`) and a
  `DATABASE_URL`; that stack has not been started in this environment.
- The four receipts under `tests/fixtures/long_task/` carry
  `_provenance.signal = "synthetic"`. Their ASR/subtitle output is fake. They
  prove a fold, never transcription or subtitle quality.

## How to drive the checkpoint surface

The CLI has exactly one task entry point; there is no `envoy task create` /
`apply` / `next` subcommand family. Each call reads one JSON payload and prints
one JSON envelope:

```bash
cd $HOME/Desktop/2026/Envoy
packages/envoy-cli/.venv/bin/envoy task-checkpoint --request-json - <<'JSON'
{"action": "create", "task_id": "acc-2026-11-01", "goal": "follow this material",
 "sources": ["https://example.com/long-video"]}
JSON
```

Actions and their required keys (dispatch in
`packages/envoy-cli/src/envoy_cli/tasks.py:251`):

| action | keys | result |
|---|---|---|
| `create` | `goal`, `sources` (list of URLs; `urls` accepted as alias), optional `task_id` | task payload with one material per source |
| `get` | `task_id` | task plus per-material media axis |
| `list` | none | `{"tasks": [...]}` |
| `apply` | `task_id`, `source` (or `url`), `receipt` object, non-negative `offset` | folded checkpoint; `blocked` carries `{code, message}` |
| `next` | `task_id`, `source` (or `url`), optional `request_id`, `max_chars` (4000), `max_total_response_chars` (48000), `observed_revision` | `result: "ready"` with `action`, `media`, `remaining_seconds`, `request`; or `result: "blocked"` |

A checkpoint persists as `task-<sha256(task_id)[:32]>.json` under
`storage.resolve_material_root()` (0600, atomic write, sibling lock), so
`create` in one process and `next` in another is the supported shape of a resume.

## Scenarios

Every scenario below is `not_run`. "Pass" means the printed criterion, not a
human impression.

### 1. Direct video, real 60-minute file, repeated resumes to the end

- Prerequisites missing: whisper; a real >= 60 min media file; network for the
  source URL (or a local file plus `file://`).
- Steps: `create` with the URL; loop: `apply` the receipt from a plain
  `envoy read` / MCP read, then `next`; stop when `result` is `blocked` with
  `already_finished` or when the task reports `completed`.
- Pass: `next` returns a range read whose `scope.start_seconds` equals the last
  processed cutoff (never 0 after the first pass); after the final `apply`,
  `get` shows `is_finished: true` and `remaining_seconds: 0.0`; no call returns
  `action: "expand"` for a pending media material.
- Also record: number of `next` calls, wall seconds, ASR seconds per call,
  re-transcribed seconds (see metrics doc section 3).
- What is checked instead: `tests/cli/test_task_checkpoint_cli.py::test_a_partial_video_receipt_resumes_with_a_range_read`
  (`:276`), `::test_a_finished_video_is_blocked_by_the_core_not_the_cli`
  (`:314`), `tests/cli/test_task_dispatch_flow.py::test_a_video_task_survives_create_apply_and_next`
  (`:73`). All synthetic receipts.

### 2. Resume after a real process interruption

- Prerequisites missing: whisper; real media; a way to kill the process during
  transcription (the unit tests stop before any transcription starts).
- Steps: start scenario 1; send `SIGINT` / `SIGKILL` while the reader is
  transcribing; verify the task file is either the previous complete version or
  absent (atomic write, no half-written JSON); restart and `next`.
- Pass: the restarted process reads a parseable task file, returns a range read
  that does not restart at 0, and the printed transcript does not repeat text
  already delivered. Failure mode to record: a JSON parse error, or a resume at
  0.
- What is checked instead: `is_finished()`/`resume_request()` ordering and the
  COMPLETED -> PARTIAL downgrade are unit-tested; process-kill durability is not.
  The ASR checkpoint cache under `~/.envoy/asr` is a separate mechanism with its
  own tests (`tests/cli/test_media_asr_resume.py:290` same bytes hit the same
  cache, `:319` changed revision is rejected); those tests do not kill a process.

### 3. Page-embedded video against a live site

- Prerequisites missing: network; a page whose video is served through browser
  media evidence.
- Steps: `envoy read` the page URL (or the MCP read tool), `apply` the receipt,
  then `next`; the receipt must carry `transcript_processed_until_seconds`,
  `transcript_delivered_until_seconds`, `transcript_unprocessed_seconds`.
- Pass: the fold's `processed_seconds` equals the processed cutoff (not the
  delivered one when they differ), and the resume is a range read starting at
  the continuation cutoff.
- What is checked instead: `tests/fixtures/long_task/page_video_asr_partial.json`
  folds to `processed_seconds = 120.0`, `unprocessed_seconds = 3480.0`,
  `media_pending` true, resume range read from `120.0` to `3600.0`. Shape only;
  the page extraction itself was replaced by a monkeypatched probe.

### 4. Large-video delivery pipeline (backend e2e script)

- Prerequisites missing: backend dependencies, a running `DATABASE_URL`, whisper
  or paddle + paddleocr, a >= 60 s local video.
- Command:
  `python scripts/run_large_video_delivery_e2e.py --video tmp/e2e-video-rag/commercial-long-video.mp4 --out-dir tmp/large-video-delivery --database-url sqlite:///tmp/large-video-delivery-e2e.db --min-duration-seconds 60`
- Exit codes are part of the contract: 2 missing video, 3 missing media/python
  dependency, 4 video too short, 5 media probe failure; 0 means the delivery
  package validated.
- Pass: exit 0, `validate_delivery_package` accepts the exported package, and the
  printed manifest covers the whole duration.
- What is checked instead: nothing. The script has not been run; its exit codes
  were read from source, not observed.

### 5. Subtitle-only video on a live site

- Prerequisites missing: network; a video with a manual subtitle track.
- Steps: read the URL, `apply`, `get`, `next`.
- Pass: `transcript_source` is `manual_subtitles`, `processed_seconds` stays null,
  `remaining_seconds` is null, `media_pending` is false, and `next` answers
  `already_finished` instead of inventing a range read.
- What is checked instead:
  `tests/cli/test_task_checkpoint_cli.py::test_a_media_receipt_with_no_text_modality_still_folds`
  (`:390`) and fixture `video_subtitles.json` (`processed_seconds: null`,
  `remaining_seconds: null`, finished). The subtitle track in the fixture is fake.

### 6. Long PDF across process restarts

- Prerequisites missing: network; a PDF large enough to need several reads.
- Steps: `create`, then alternate `envoy read` on disjoint page ranges and
  `apply` with the returned offset, in separate processes; finish with `get`.
- Pass: `page_count` and `pages_seen` only grow, `next` keeps returning an expand
  with the first unseen offset, and the final `get` reports completed without a
  re-read of already delivered pages.
- What is checked instead: text/expand resume is unit-tested
  (`::test_a_text_material_still_resumes_with_an_expand`, `:338`); page-level
  fixture coverage is absent, so this scenario has no shape-level evidence either.

### 7. Link-corpus rerun at one job

- Prerequisites missing: network.
- Command: `python scripts/acceptance/link_coverage.py --jobs 1`
- Pass: the run writes a new `benchmarks/link-coverage/*.json` whose `summary`
  has the same 57-link corpus and a per-verdict split (the last run on
  2026-10-26 gave readable 10, partial 18, empty 3, blocked 4, failed 2,
  unreachable 20; see the metrics document). Acceptance requires the per-verdict
  split to be reported, not only the 49.1% usable total.
- What is checked instead: nothing new. The 2026-10-26 artifact is the previous
  run, not this acceptance.

## What this document does not claim

No scenario above has been executed, so no statement about long-media behaviour
outside the unit-level fold is supported by this document. The supported claim
remains the narrow one: some materials can be continued from a checkpoint
instead of being read again from the start, and that is checked only at the
shape/unit level. Quality of transcription or subtitles, timing accuracy, real
interruption recovery, and live-site behaviour are all unmeasured here; each is
listed above with the exact command that would measure it.

Monkeypatched calls the fixtures rest on, for the record:
`reader.transcribe_audio_file` (three captures; direct-video partial, direct-video
complete, page-video partial) and `reader.extract_public_subtitles` (subtitle
fixture), plus the page media/duration probe in the page-video capture. All four
are captured shapes with fake transcript or subtitle output; none of them is a
delivery, so none of them is acceptance evidence.
