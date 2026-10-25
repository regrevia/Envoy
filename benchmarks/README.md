# Envoy Evidence Efficiency Evaluation

Run the deterministic offline subtitle retrieval benchmark:

```bash
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src \
  python benchmarks/video_evidence_eval.py \
  --materials-dir /tmp/envoy-eval-materials
```

The fixture contains questions about a specific cue and a more open-ended
question. The report records whether the expected evidence is found, source
time locators, tool calls, returned characters, Envoy's tokenizer-independent
token estimate, full-scan text baseline, and local search latency.

This harness does **not** invoke an LLM and does not measure answer quality or
host model tokens. Those fields remain `unknown`/`null` unless the evaluator
provides actual usage captured from the host run:

```json
{
  "dodge-cue": {
    "provider": "host provider",
    "model": "host model",
    "input_tokens": 1200,
    "output_tokens": 80
  },
  "phase-change": {
    "provider": "host provider",
    "model": "host model",
    "input_tokens": 1350,
    "output_tokens": 90
  }
}
```

Pass that file using `--host-usage-file`. Do not compare actual provider
consumption against Envoy's text-only estimate as though they were the same
measurement. A release benchmark should also record host answer correctness,
source citation correctness, end-to-end elapsed time, and whether captions or
visual frames were actually delivered.

For token or cost comparisons, the baseline must be recorded per task with the
same host model, source, question, and answer-quality review. Envoy may reuse
retained material only when the baseline is given an equivalent fair cache
opportunity. Never compare Envoy's estimated tokenizer-independent count with
the provider's actual count as if they were the same metric.

## OCR Recognition Quality

Use `benchmarks/ocr_quality_eval.py` to score OCR text against independently
transcribed references. Each JSONL line contains `sample_id`, `source_id`,
`backend`, the source image's SHA-256, `reference_text`, and `recognized_text`.
The report contains sample-level and backend-level character error rate (CER)
and whitespace-token word error rate (WER), but never emits either text field.
References containing CJK characters have WER set to `null`; use CER for
those samples. Empty OCR output is scored as an error, not omitted.

```bash
python3 benchmarks/ocr_quality_eval.py --records /path/to/ocr-review.jsonl
```

Transcribe the source independently of the OCR output, freeze the reference
before comparing backends, and keep the source hash and backend/version with
the run record. Synthetic images are useful for pipeline tests, not real-source
accuracy claims. CER/WER measure transcription only; they do not establish
source attribution, region ordering, locator correctness, visual handoff, or
host-agent task quality.

## Real Agent Product Evaluation

Use `benchmarks/agent_product_eval.py` for the versioned end-to-end
evaluation. It consumes JSONL records containing real public sources and
host-agent task outcomes. Each source must have at least two tasks so the
second task measures material reuse and follow-up behavior. The strict gate
requires at least 20 sources and 40 tasks, one provider/model identity, source
locators, answer and citation review, first-evidence and total latency, Envoy
calls, visual inputs, repeated downloads, and actual host usage.

For the planned self-hosted Claude Code CLI comparison, follow the
[multi-link paired experiment protocol](../docs/superpowers/specs/2026-10-23-envoy-agent-benchmark-design.md).
It defines the four priority Chinese platforms, baseline tool inventory,
a declared per-package `first_arm` order, isolated CLI profiles, authoritative
inference-server token accounting, and the cache-confounded reporting rule.

Generate an auditable per-package order before launching the host agent:

```bash
python3 benchmarks/paired_experiment_plan.py \
  --packages /path/to/packages.jsonl \
  --seed 20261023 \
  --cache-isolation-status unknown \
  --output /path/to/plan.json
```

Each package must contain at least two source IDs. The generated `arms` list
is the actual order to run, and the two profile paths are intentionally
different. The planner does not launch Claude Code or alter credentials; the
server-side runner must create the profiles, tag model requests with the
package/arm/session IDs, and record whether inference-server cache reuse was
disabled or isolated. Pass `verified_disabled` or `verified_isolated` only
after the server-side operator has verified it; otherwise use `unknown` during
planning and later classify the executed batch as `cache-confounded`.

The generated plan also freezes a deterministic `run_id`,
`package_manifest_sha256`, `session_ids`, and three `turn_correlation_ids` per
arm (`initial`, `followup-1`, `followup-2`). A turn correlation ID is a logical
task-turn key, not an inference-server request ID: one turn may trigger several
model requests. The controller must attach the turn key to every server request
and retain every server-issued request ID in its usage ledger. Keep that ledger
and the plan beside the result JSONL. `confirmatory_minimum_met` is only a
planning signal: it is true when the plan has at least 20 packages, 40 scripted
turns, and 20 unique source IDs; it does not prove that sources were reachable
or that either arm completed.

The first approved fixed set uses exactly 20 packages (four in each of the
Bilibili, Douyin, Xiaohongshu, WeChat, and cross-platform strata), three
identical scripted turns per package, and at least 40 unique public URLs. Freeze
the package manifest, exact turn text, source revisions, reference facts, and
rubric before generating the plan. Candidate URLs are not accepted
sources until both arms' required access condition and source evidence have
been revalidated. Do not replace an inaccessible URL after seeing an arm's
result; record the failure in the assigned pair.

For this experiment, the accepted model identity is the gateway-reported name
`DeepSeek-V4.1-Flash`; checkpoint, tokenizer, and quantization are not claimed.
Use B group built from the same frozen Envoy `main` commit as recorded in the
run manifest, not the older `v0.1.6` Release. Keep that source-build batch
separate from any later Release evaluation.

The server calibration completed on 2026-10-24 is cache-isolation evidence
only, not benchmark usage. To use `per_request_salt` for a formal
plan, every formal `request_usage` row must include a SHA-256 of its per-request
salt (never the raw salt), all hashes must be unique, and
`cache_read_input_tokens` must be zero. The ledger validator enforces those
conditions and reports its independent `cache_isolation_evidence.verified`
result. If a request has no salt evidence, reuses a salt, or reports cache
reads, classify the batch as `cache_confounded`; do not claim cold-cache
isolation.

Validate the redacted inference-server usage ledger before converting it into
the evaluator's per-turn JSONL:

```bash
python3 benchmarks/usage_ledger.py \
  --plan /path/to/plan.json \
  --ledger /path/to/server-usage-ledger.jsonl \
  --server-export-complete \
  --output /path/to/usage-ledger-report.json
```

See `usage-ledger-records.example.jsonl` for the strict record shape. The
ledger has two record types: one `turn_summary` for every planned
package/arm/turn (including outcome, elapsed interval, intervention and visual
counts), and one `request_usage` record for every actual server request. A turn
may have multiple request records or zero requests when it fails before model
invocation. The validator checks server-issued request ID uniqueness, frozen
session/turn mapping, complete turn coverage, exact request-ID reconciliation,
cache-token category sums, and allowed fields; arbitrary payload fields are
rejected to avoid prompt/response/credential leakage. Its output must be valid
before the operator aggregates request rows into token and cache breakdown
fields in the main evaluator records. `--server-export-complete` must only be
passed after verifying the authoritative log export is complete for the whole
run window.

The report's `paired_usage_rows` can be joined to the preregistered task
records by `(package_id, turn_role)`. Copy actual usage, elapsed/outcome,
intervention, visual, repeated-download, and cache-breakdown values from those
rows; keep answer facts and citations from the blinded review record. Copy
`baseline_server_reported_cost` and `server_reported_cost` only when the ledger
reports a shared currency for the pair. The ledger report itself is an audit
artifact and is not a replacement for the evaluator JSONL.

The first JSONL record must identify the Envoy commit, host agent/OS, optional
ASR/OCR status, the provider/model used for both arms, and
`cache_isolation_status`: `verified_disabled`, `verified_isolated`,
`cache_confounded`, or `unknown`. Only the first two states permit a
token-savings claim.
Each source records when it was revalidated and the SHA-256 source revision
when material was available (null is allowed for an observed blocked source).
Private/loopback URLs, signed URLs, duplicate source URLs, and tracking-only URL
variants do not count as distinct public sources.
Each task includes a versioned reference-fact set, arm-specific claimed and
supported fact IDs, plus every citation's reviewed locator/support judgment.
The report emits macro fact recall/precision and citation-support rates;
manual answer/citation booleans remain separate adjudication fields.

If the inference server exposes cache accounting, each task can record
`usage_breakdown_status: "reported"` plus `uncached_input_tokens`,
`cache_read_input_tokens`, and `cache_creation_input_tokens`. These disjoint
counts must sum to `input_tokens`; the baseline uses corresponding
`baseline_` fields. Missing breakdowns remain `N/A` and are never reconstructed
from total input tokens. The concise report includes the cache split and total
input exposure when the split is complete.

```bash
python3 benchmarks/agent_product_eval.py \
  --records /path/to/real-agent-eval.jsonl
```

Add `--markdown` to render a concise paired comparison table for sharing.
Efficiency change rates are shown only after the strict evaluation gate passes;
quality percentages and count deltas remain visible when reviewed data is
available. Incomplete usage is rendered as `N/A`, never inferred from Envoy's
own token estimate. The JSON and Markdown reports also include a deterministic
source-clustered paired bootstrap 95% interval; a token-saving claim is
withheld when that interval crosses zero. JSON includes the same interval
stratified by `source_class`, so platform-specific regressions are visible
even when the overall average looks favorable.

The command exits nonzero for incomplete or placeholder records. Use
`--allow-incomplete` only while preparing a draft; draft output must not be
used as a release or token-savings claim. See
`agent-product-eval-records.example.jsonl` for the record shape. The evaluator
does not call an answer model and does not infer correctness.
Historical links awaiting repeat validation are tracked in
`docs/acceptance/agent-product-eval-candidates-2026-10-23.md`; blocked sources
remain candidates but do not count as completed evaluations.
