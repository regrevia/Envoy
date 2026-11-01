# Long-task and delivery metrics, 2026-11-01

Status: definition document plus a quote of the numbers that already exist in
committed artifacts. Working tree `419969f`, 2026-11-01 Asia/Shanghai.

Evidence levels: the link-coverage and A/B numbers below are `verified` against
the committed JSON artifacts named next to them. The per-stage values that no run
has produced are marked `unknown`, not zero. The checkpoint-v2 long-task values
are `unit_checked` where a test asserts them and `not_run` where they need real
media. No number in this document is an estimate.

## 1. Retiring the 57-link total coverage rate as the primary metric

The single corpus rate mixes reachability with extraction quality. The last
committed run, `benchmarks/link-coverage/repeat-2026-10-26.json`, reports
`summary.total = 57`, `summary.usable = 28`, `summary.usable_rate = 0.491`, with
`by_verdict = {readable: 10, partial: 18, empty: 3, blocked: 4, failed: 2,
unreachable: 20}` (the same run is quoted at
`docs/product/link-coverage-benchmark.md:69`; the corpus definition is at
`scripts/acceptance/link_coverage.py:68` and `docs/product/link-coverage-benchmark.md:10`).

With 20 of 57 links unreachable from this host, `usable_rate` is bounded by the
network, so a change in the reader moves it by a few points at most and a change
in reachability moves it more. The same document already reports the
reachable-only view for the earlier runs: 26/57 = 45.6% and 31/57 = 54.4% for the
full corpus, 26/38 = 68.4% and 31/38 = 81.6% for the reachable corpus
(`docs/product/link-coverage-benchmark.md:52-53`). Three different denominators
for one "rate" is the reason to stop using it as the headline.

Derived from the 2026-10-26 artifact (arithmetic on the quoted summary, not a new
run): reachable = 57 - 20 = 37, usable / reachable = 28/37 = 75.7%. The earlier
runs used a reachable denominator of 38, so the two numbers are not comparable
without restating both counts.

## 2. Replacement: a per-stage funnel

Every stage keeps its own denominator and its own pass definition. A run reports
all rows; no row is collapsed into another.

| stage | pass definition | denominator | 2026-10-26 value | source | status |
|---|---|---|---|---|---|
| corpus | link list fixed and versioned | 57 links, 14 families | 57 | `repeat-2026-10-26.json` `summary.total` | verified |
| reachability | transport can fetch the URL at all | all corpus links | 37/57 reachable (20 unreachable) | `summary.by_verdict.unreachable` | verified |
| text extraction | response carries usable text (readable or partial) | reachable links | 28/37 = 75.7% | derived from `usable`, `by_verdict` | verified (derived) |
| usable content | verdict `readable` only | reachable links | 10/37 = 27.0% | `summary.by_verdict.readable` | verified (derived) |
| partial extraction | verdict `partial` (needed continuation or lost material) | reachable links | 18/37 = 48.6% | `summary.by_verdict.partial` | verified (derived) |
| budget completion | read returned within budget without truncation | responses served | unknown | no artifact records budgets per link | unknown |
| checkpoint fold | receipt folds to the correct `processed_seconds` / `pages_seen` | receipts applied | 5 media cases + text cases asserted | `tests/cli/test_task_checkpoint_cli.py:296-308`, `:355`, `:380-384` | unit_checked |
| resume continuity | the next request continues instead of restarting | resumable materials | 3 media cases asserted (range read, never 0) | `tests/cli/test_task_checkpoint_cli.py:306-308`, `:314`, `:338` | unit_checked |

The `partial` verdict is the interesting one for long tasks because it means
material was read but not fully delivered; it is also the bucket the checkpoint
work targets. Reporting 18/37 `partial` separately is the first improvement over
18 inside a 57-link total.

## 3. Long-task metrics (checkpoint v2)

These are the metrics a real long-media run must report. None of them exists as a
measured value yet; the table states where the definition comes from instead.

| metric | definition | current value | evidence |
|---|---|---|---|
| resume calls per material | `next` calls until finished, excluding the first read | unknown | needs real media; unit tests stop after one resume |
| re-transcribed seconds | seconds decoded twice across resumes, `sum(end - start)` of overlapping audio windows | unknown | needs whisper; the ASR cache tests prove identity reuse only for identical bytes and range (`tests/cli/test_media_asr_resume.py:290`), and a changed range gets a fresh identity (`:319`) |
| delivered text preserved across a media resume | delivered chars before and after a range read | 0 preserved: the media branch of `resume_request` sends no `offset` and no `material_id` (`long_task.py:560-604`) | inspected |
| fold accuracy | folded `processed_seconds` equals the receipt's honest cutoff | 120.0 of 3600.0 for the partial fixture, 600.0 of 600.0 for the complete one | `tests/cli/test_task_checkpoint_cli.py:296-308`; fixtures `video_asr_partial.json`, `video_asr_complete.json` |
| false-finished rate | finished claims for materials with media pending | 0 in unit tests; the v1 rule that produced 1 is what v2 replaces | `long_task.py:354-360`, section 2 of the architecture doc |
| fold idempotence | applying the same receipt twice changes nothing | fails by construction: `media_calls` increments (`long_task.py:515`) and `downloaded_bytes` accumulates (`:456-462`); 1000 -> 2000 in a local two-fold check | inspected in code; the two-fold run was a local script, not committed |
| interruption recovery | resumed after `SIGKILL` without a parse error and without restarting at 0 | not_run | acceptance doc scenario 2 |
| time to first evidence, long video | wall seconds from read start to first usable text | unknown | needs real media |
| wall time per delivered hour of media | seconds elapsed / media hour | unknown | needs real media |

The fold idempotence row is the one number in this document that is not read
from an artifact: the doubling follows from the two code sites, and the 1000 ->
2000 pair was produced by a local fold script in `/tmp` that is not part of the
repository. Treat the pair as an illustration of the code path, and the code
sites as the evidence.

## 4. Two lines, kept apart

Line A is the agent-facing Skill/MCP line: one turn = one agent session solving a
task, and the artifacts are transcripts, tool calls and tokens. Line B is the
website/backend delivery line: one unit = one requested delivery, and the
artifact is a validated delivery package. Their pass definitions, denominators
and failure modes differ, so they are reported separately and never averaged.

### Line A: Skill/MCP, A/B artifact

`docs/acceptance/artifacts/envoy-ab-v1/summary.json` (`run_id`
`envoy-ab-20261025-ff0ae997d591`, `turn_count` 120, two arms of 60 turns):

| metric | baseline | envoy | source |
|---|---|---|---|
| turns | 60 | 60 | `arms.baseline.turns`, `arms.envoy.turns` |
| turns blocked at the wall-clock cap | 41 | 0 | `arms.*.blocked` |
| turns completed within the cap (derived as turns - blocked) | 19 | 60 | arithmetic on the two rows above |
| first evidence, median ms | 88006.0 | 48365.0 | `arms.*.first_evidence_median_ms` |
| facts hit / facts total | 47/516 (9.1%) | 197/516 (38.2%) | `arms.*.facts_hit_total`, `facts_total` |
| tool calls, total | 261 | 291 | `arms.*.tool_calls_total` |
| requests | 1771 | 4071 | `arms.*.requests` |
| request failures | 937 | 1156 | `arms.*.failures` |
| paired input tokens (19 paired turns) | 2904111 | 17422152 | `paired_input_tokens`, `paired_completed = 19`, `paired_input_ratio = 6.0` |

Two caveats that must travel with this table. `outcomes_scope` in the same file
says completed/blocked only mean the process exited inside the wall-clock cap and
do not mean the answer was correct or the task finished. `quality_verified` is
`{"turns": 0, ...}`: no turn was human-verified, so quality is not measured, and
that zero is not a quality failure either. `facts_hit` is therefore the only
content-related signal in the artifact, and it is an automated count.

### Line B: website/backend delivery, unrun

The delivery-line script is `scripts/run_large_video_delivery_e2e.py`. Its exit
codes are the closest thing to a pass definition that exists: 2 missing video, 3
missing media/python dependency, 4 video too short, 5 media probe failure, 0 means
`validate_delivery_package` accepted the exported package. No run was performed,
so every line-B value is `unknown`; the codes were read from source, not observed.
Reporting line B next to line A would require converting validated packages into
turns, which nothing measures.

## 5. What would falsify the checkpoint claim

The supported claim is narrow: some materials can be continued instead of being
read from the start, and the fold keeps an unfinished transcript from being
marked finished. It is falsified by any of the following, none of which has been
measured: a resume that starts at 0 for a material whose checkpoint advanced; a
`next` that answers `already_finished` while `media_pending()` is true; a
`processed_seconds` that exceeds the receipt's own cutoff; a fold that changes a
completed material into a blocked one. Unit tests cover the second and third for
synthetic receipts; the first and fourth need a real run.
