# Envoy Agent Benchmark Design

**Status:** Design draft for review
**Date:** 2026-10-23
**Decision owners:** Envoy maintainer and benchmark operator

## Goal

Measure whether Envoy improves a self-hosted Claude Code CLI workflow on
multi-link public-source tasks, focusing on actual model-server token usage,
elapsed time, evidence quality, missing facts, and human intervention. The
experiment must be reproducible and must not turn incomplete or failed reads
into token-savings claims.

## Scope

The primary product experiment compares two arms using the same Claude Code
CLI build and the same self-hosted DeepSeek checkpoint. The planned first
confirmatory batch uses the operator's deployed DeepSeek-V4.1-Flash-1017
model, subject to recording the checkpoint and tokenizer actually loaded by
the inference server:

- **A, native read:** Envoy Skill and MCP are absent. The host may use only
  native reading tools that are actually installed and enabled on the server.
- **B, Envoy read:** A fixed Envoy Skill commit and runtime Release are
  installed and the task uses Envoy through MCP.

The experiment does not compare Anthropic-hosted Claude models. Claude Code is
the agent runner; the self-hosted inference server is the source of truth for
token accounting. Envoy supplies located evidence; the host agent remains
responsible for selecting, interpreting, and producing the requested output.

## Experimental Units

A **task package** contains:

- One frozen user goal and required deliverable.
- Multiple public source URLs (normally 2-5) relevant to that goal.
- Three pre-authored user turns: initial request and two increasingly
  specific follow-ups.
- A reference-fact set, expected source attribution, and answer rubric,
  written before either arm is run.

The versioned package manifest uses `task_package_schema_version:
envoy.task-package/1`. Each package has at least two distinct public source
IDs and exactly three scripted turns: one initial request and two follow-ups.
The package's `package_id` and fixed `source_ids` are repeated on each task
record. The package, rather than an individual URL or turn, is the primary
randomization and bootstrap unit.

The same package is run once in each arm. Each arm gets its own clean Claude
Code session, work directory, local state, and material cache. The three turns
within an arm stay in that arm's session so both configurations may reuse their
own material. No prompts, answers, extracted material, or caches cross from A
to B or B to A.

The product corpus contains 100 task packages, 20 per stratum. The four
priority Chinese platforms are Bilibili, Douyin, Xiaohongshu, and WeChat
Official Accounts; cross-platform packages test synthesis across them:

| Stratum | Packages | Focus |
| --- | ---: | --- |
| Bilibili-centered | 20 | Multiple public video links, subtitles/ASR, follow-up reuse |
| Douyin-centered | 20 | Share/video links and public image-note links, with source-specific coverage |
| Xiaohongshu-centered | 20 | Public image notes, ordered images, visual evidence and OCR where enabled |
| WeChat Official Account-centered | 20 | Public article links, long text, continuation and follow-up reuse |
| Cross-platform synthesis | 20 | Multiple links spanning at least two priority platforms |

Each package contains multiple links; package counts must not be reported as
URL counts. Require at least two distinct public source URLs per package and
target at least 200 unique URLs overall. The run manifest records both counts
and URL deduplication; an intentionally reused URL counts once in the unique
URL total. The paired planner additionally freezes a deterministic
`run_id`, package-manifest SHA-256, per-arm session IDs, and
`turn_correlation_ids` for all three turns. A turn correlation ID is a logical
task-turn key, not an inference-server request ID, because one turn may issue
multiple model requests. Tag every authoritative server request with the turn
key and retain each server-issued request ID in the usage ledger. Keep the plan
and ledger beside the JSONL results. Planner minimum status does not establish
source availability or successful execution.
Within a package, all links and all three turns are fixed before execution.
Any needed clarification is pre-authored and delivered identically to both
arms. The operator must not improvise one-sided hints.

A separate robustness set contains at least 20 packages with expected
boundaries such as expired links, inaccessible pages, login/challenge walls,
network failures, and partial media. These results report state classification,
recoverability, retries, and human-intervention time. They are excluded from
the denominator for successful-read token savings, but remain visible in the
overall task-completion rate.

Record platform coverage by source URL and task package. Do not count several
URLs from one package as independent task successes. Stratify the main report
by the four priority platforms and cross-platform synthesis; publish an
overall figure only alongside these strata so a strong result on one platform
cannot hide failures on another.

## Standard Long-Document Calibration

QASPER is a supplemental calibration set, not part of the 100-package product
uplift score. The target is a preregistered, stratified 200-question subset
from the official test split, targeting approximately 100 unique papers.
Stratification uses the official answer categories: extractive, abstractive,
yes/no, and unanswerable. Prefer approximately two questions per paper, with
the exact per-type quota and selected question/paper IDs frozen in advance.
Freeze source URLs/artifacts, inclusion rules, and evaluator version before
execution. Never use sampled test questions or answers to tune prompts,
routing, or extraction behavior.

Run the official evaluator and report Answer F1, Evidence F1, per-answer-type
Answer F1, and missing prediction count. The official scorer supports both
full annotated evidence and a `text_evidence_only` mode that excludes
figure/table evidence. Declare the selected mode before the run and do not
compare scores across modes. QASPER evidence can be drawn from multiple
paragraphs and may include figures or tables. Cluster QASPER confidence
intervals by paper because multiple questions about the same paper are
correlated. This calibration result must not be described as a general
web/platform benchmark or merged into the real-platform improvement
percentage.

If both arms cannot access equivalent full-paper inputs from the same source
condition, record the access failure and do not silently replace one arm's
input with pre-extracted QASPER context. Any shared-context QA run is a
separate scorer/model calibration and is not Envoy product evidence. For the
end-to-end comparison, both arms receive the same public paper URL; the
QASPER full text and annotations are gold data for evaluators only, never
additional model input.

## Pairing, Randomization, and Isolation

Use a single experiment controller, but run each arm as a separate process
with an independently prepared Claude Code configuration directory and
workspace. The A profile must not load Envoy Skill, MCP configuration, or
Envoy-derived files. The B profile must load the exact pinned Skill and runtime.
Before the first task, archive the effective Claude Code settings and each
arm's discovered tool list, with secrets removed.

Randomize A/B order within each task package using a recorded seed, balancing
the total number of A-first and B-first pairs. A single controller selects
the arm by launching one of two isolated Claude Code CLI profiles; it does
not switch modes inside a conversation. Each package starts two fresh
sessions, and its scripted follow-up turns stay within their respective
session. Run pairs sequentially on the same host to avoid GPU contention,
with a fixed warm-up procedure excluded from measurements. Keep the inference
server, checkpoint, tokenizer, quantization, sampling settings, concurrency,
and resource allocation fixed throughout a batch. Pass the planner's
package/arm/session/turn-correlation IDs through the server's correlation
metadata for every model request, and retain the server-issued request IDs
separately; do not synthesize server request IDs during execution.

The operator may first run a baseline-only instrumentation pilot to inventory
the server's native reading tools and validate usage capture. Keep that pilot
out of the confirmatory results. For the confirmatory batch, use paired,
randomized A/B runs under the same controller and frozen task manifest. Do
not run the entire baseline corpus first and the entire Envoy corpus later:
time, source changes, model-server changes, and operational drift would be
confounded with the treatment.

The primary cold-cache comparison disables cross-request server prefix/KV
cache reuse when supported, and starts each session with empty Claude Code
conversation and local cache state. It must not clear or reset another
operator's shared service state. If server caching cannot be disabled, isolate
each arm in separate cache namespaces or equivalent replicas, and record
cache-read/cache-write usage. If isolation cannot be verified, classify the
batch as cache-confounded rather than claiming a cold-cache result.

Envoy's material cache and each arm's conversation context remain available
within the three-turn package to measure intended follow-up reuse. Clear
package-scoped material and local state between packages. A separate
production-cache condition may measure normal warm-cache behavior; report it
as a distinct condition and never mix it with cold-cache results. No shared
browser profile, personal cookies, private account, or unapproved proxy may
be used. Normal user authentication or verification is handled by the user
and counted as intervention.

### Operator Run Sequence

1. Run a baseline-only instrumentation pilot on 10 packages. Inventory the
   native tools available to Claude Code on the benchmark server, verify
   request-to-run correlation and token reconciliation, and test that the
   Envoy profile is absent from arm A. Exclude this pilot from all confirmatory
   statistics.
2. Freeze the task packages, reference facts, scoring rubric, runtime
   identities, cache policy, and randomization seed before the confirmatory
   batch. Prepare two launch profiles: A has no Envoy Skill, MCP, or
   Envoy-derived material; B pins the selected Envoy Skill, Release, and MCP
   configuration.
3. Use one controller as the switch. For each package, it randomly chooses
   whether to run A or B first, launches a new isolated CLI process/profile
   for each arm, and sends the exact same three scripted turns to each.
   Complete that package's pair before moving to the next package. Do not
   switch arms within a session and do not run the entire A corpus before B.
4. Reset package-local session, workspace, and material state between
   packages. Preserve follow-up reuse within each arm's package. Apply the
   preregistered server-cache policy; if neither cache disablement nor
   verifiable namespace/replica isolation is available, label the batch
   cache-confounded and report cache counters instead of calling it
   cold-cache.
5. Reconcile every model request with authoritative inference-server usage
   before scoring. Missing request/token records stay missing; do not fill
   them from Claude Code estimates or Envoy's estimated token counts.

This design intentionally uses randomized paired runs rather than a single
global mode toggle between two corpus-sized phases. The controller-level
switch makes the assignment auditable while separate sessions and profiles
prevent prompts, tool access, retained materials, and local caches from
crossing between arms.

## Model and Runtime Identity

The batch manifest records:

- Claude Code CLI version and executable/package digest.
- Host OS, architecture, Python/runtime versions, and installed native
  reading tools.
- Inference server implementation/version, API protocol, server configuration
  digest, tokenizer identity, quantization, and checkpoint revision/hash.
  For the planned DeepSeek-V4.1-Flash-1017 run, record the exact served model
  identifier and loaded checkpoint/revision; do not infer model identity from
  a Claude Code model alias or a requested configuration value.
- Envoy Release version, source commit, wheel SHA-256, Skill commit, and MCP
  tool inventory for B.
- Cache policy, session-isolation procedure, network/proxy policy, concurrency,
  warm-up, task-order seed, and rubric version.

For an official DeepSeek API run, record the exact API model identifier and
provider-returned model field. For self-hosted weights, record the actual
checkpoint revision/hash and serving stack rather than treating an API alias
such as `deepseek-flash` as proof of the loaded local model version. A change
to any of these identities starts a new batch; do not merge results across
identities.

## Instrumentation

The inference server's per-request usage/log record is authoritative for
model usage. Claude Code client telemetry alone is not sufficient for a
self-hosted backend unless its counters are demonstrably sourced from that
backend. Place a local, non-mutating metrics adapter in front of the inference
server only if native server logs cannot be correlated to task/session IDs.
The adapter must preserve request and response bodies byte-for-byte; it may
add only the transport metadata required for correlation.

Every model request is tagged with `run_id`, `package_id`, `arm`,
`session_id`, and `request_id`. Capture:

- Input tokens, cache-read tokens, cache-creation tokens, and output tokens as
  separate fields.
- Total input-token exposure, effective/billable input tokens when defined by
  the serving backend, and output tokens. Never substitute one for another.
- Server-reported cost separately from estimated GPU/runtime cost.
- Request start/end, first-token time, full-package wall time, retries, and
  HTTP/tool errors.
- Tool calls, actual Envoy calls, repeated downloads, returned visual
  resources, and visual inputs actually inspected by the host model.
- Human intervention type, count, and active operator minutes.

Do not store API keys, authorization headers, cookies, full prompts, or full
answers in the metrics collector. Save only redacted metadata and task
identifiers. Review the host and inference server logs before export. Because
Claude Code is connected to a self-hosted model rather than relying on
Anthropic-hosted usage telemetry, the controller must reconcile each run's
request IDs against authoritative inference-server token records. If native
logs lack correlation fields, a local adapter may add run/package/arm/session
metadata without reading, retaining, or changing prompt and response bodies.
If usage cannot be reconciled, mark those pairs missing; do not substitute
Claude Code's estimates or Envoy's token estimate.

The run record must declare `cache_isolation_status` as
`verified_disabled`, `verified_isolated`, `cache_confounded`, or `unknown`.
Strict claims require one of the first two states. When the inference server
exposes cache accounting, each task records
`usage_breakdown_status` and, when reported,
`uncached_input_tokens`, `cache_read_input_tokens`, and
`cache_creation_input_tokens` for both arms. The three components must sum to
the corresponding `input_tokens`; missing components remain unknown. The
evaluator reports input exposure and its cache split only when both arms are
complete, and withholds token-savings claims when cache isolation is unverified.

For each scripted turn, the runner also records `envoy_task_outcome` and
`baseline_task_outcome` (`completed`, `partial`, `blocked`, or `failed`),
human intervention count/minutes, and provider cost status. A package is
complete only when all three turns in that arm are `completed`; blocked and
partial packages remain in the intention-to-treat denominator. Cost is
reported only when the inference server provides it for every paired turn;
otherwise the public table shows `N/A`.

## Quality Review

Before execution, freeze each package's reference facts, expected source
attribution, acceptable partial outcomes, and deliverable rubric. Preserve
facts that cannot be established from the source as unknown rather than
guessing.

Two reviewers independently score anonymized A/B outputs, with labels hidden:

- Package deliverable meets the requested structure and scope.
- Reference fact recall and precision.
- Claim-level support by the cited source and locator.
- Citation locator resolves to the cited source material.
- Unsupported claim count/rate.
- Missing required source or media evidence.
- Whether the final answer honestly reports partial/blocked coverage.

Resolve disagreements using a documented adjudication rule and retain both
original ratings. Report inter-rater agreement. Do not let the model under
test grade its own answers.

Definitions:

- `fact_recall = supported_reference_facts / all_reference_facts`
- `omission_rate = 1 - fact_recall`
- `unsupported_claim_rate = unsupported_claims / all_reviewed_claims`
- `citation_traceability = citations resolving to the stated source and
  locator / citations reviewed`
- `omission_reduction = (A_omission_rate - B_omission_rate) / A_omission_rate`

When the A omission rate is zero, omission reduction is not applicable. Report
both absolute percentage-point differences and relative changes. Do not
describe a higher citation count alone as better evidence quality.

## Analysis and Reporting

The task package is the resampling unit because its three turns and several
links are correlated. For QASPER, use the paper as the resampling cluster.
Report paired A/B mean and median differences, p50/p90 latency, and 95%
paired-bootstrap confidence intervals, stratified by platform and overall.
Report the number of valid pairs and missing metrics for every measure.
Failed/blocked packages stay in the intention-to-treat completion and usage
totals. Also report conditional token/quality metrics for packages where both
arms succeeded, clearly labeled as conditional; never present those alone.

The one-page public summary must include:

| Metric | Native baseline | Envoy | Change |
| --- | ---: | ---: | ---: |
| Successful package completion | | | percentage-point change |
| Supported-fact recall / omission rate | | | absolute and relative change |
| Unsupported-claim rate | | | absolute change |
| Citation traceability | | | absolute change |
| Input-token exposure (cache split shown) | | | relative change |
| Output tokens and measured cost | | | relative change |
| End-to-end p50 / p90 time | | | relative change |
| Human-intervention rate and minutes | | | absolute change |

Keep the public result concise, but show quality next to efficiency so a
headline such as "70% fewer input tokens" cannot stand alone. For example,
report input-token reduction, supported-fact recall, omission-rate reduction,
unsupported-claim rate, and completion rate together. Define reduction
relative to A as `(A - B) / A`; show absolute percentage-point changes for
quality rates. Mark a metric "N/A" when its baseline denominator is zero.
Include the paired sample count and confidence interval in a note or linked
technical appendix.

Publish two token views: (1) intention-to-treat input/output usage and measured
cost per assigned package, including failed attempts and retries; (2)
conditional successful-pair usage. Only publish a token-savings percentage if
paired usage is complete, source and model quality are non-inferior under the
preregistered rule, and the confidence interval supports savings. Report
failed and partial outcomes alongside successful-read metrics. Do not use
Envoy's tokenizer-independent estimate as actual model usage.

## Release Gates

1. **Instrumentation pilot:** 10 packages. Verify arm isolation, randomization,
   task/request correlation, usage reconciliation, redaction, cache handling,
   and reproducible output generation. Pilot data is never combined with the
   confirmatory batch.
2. **Product batch:** 100 multi-link packages / 300 scripted turns plus the
   separate robustness set. Freeze manifests and rubrics before unblinding.
3. **QASPER calibration:** frozen 200-question subset, official evaluator,
   separately reported.
4. **Claims review:** publish only metrics backed by complete paired records
   and blinded adjudication. The existing minimum 20-source/40-task evaluator
   gate remains a floor, not a substitute for this protocol.
5. **Release policy:** no new formal release or public speed/token-saving claim
   until all applicable gates pass. Keep release version, development runs,
   and exploratory pilots in separate batches.

## Implementation Boundary

The implementation should add a benchmark controller and result adapter
without changing Envoy's evidence protocol or putting user credentials into
Envoy. Keep the existing v1 evaluator backward-compatible; introduce a
versioned task-package schema for multi-link tasks and per-request telemetry.
The runner must fail closed when it cannot prove arm isolation or correlate
authoritative usage.

## References

- QASPER dataset paper: Dasigi et al., NAACL 2021,
  <https://aclanthology.org/2021.naacl-main.365/>.
- QASPER dataset card and evidence schema:
  <https://huggingface.co/datasets/allenai/qasper>.
- Official QASPER evaluator and Answer F1 / Evidence F1 implementation:
  <https://github.com/allenai/qasper-led-baseline/blob/main/scripts/evaluator.py>.
- DeepSeek API change log (API model alias and 2026-10-17 update):
  <https://api-docs.deepseek.com/updates/>.
- DeepSeek V4.1 Flash 1017 model card:
  <https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017>.
