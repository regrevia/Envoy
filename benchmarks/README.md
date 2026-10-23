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
randomized per-package A/B order, isolated CLI profiles, authoritative
inference-server token accounting, and the cache-confounded reporting rule.

Generate an auditable per-package order before launching the host agent:

```bash
python3 benchmarks/paired_experiment_plan.py \
  --packages /path/to/packages.jsonl \
  --seed 20261023 \
  --output /path/to/plan.json
```

Each package must contain at least two source IDs. The generated `arms` list
is the actual order to run, and the two profile paths are intentionally
different. The planner does not launch Claude Code or alter credentials; the
server-side runner must create the profiles, tag model requests with the
package/arm/session IDs, and record whether inference-server cache reuse was
disabled or isolated. If that cannot be verified, report the batch as
cache-confounded.

The first JSONL record must identify the Envoy commit, host agent/OS, optional
ASR/OCR status, and the provider/model used for both arms.
Each source records when it was revalidated and the SHA-256 source revision
when material was available (null is allowed for an observed blocked source).
Private/loopback URLs, signed URLs, duplicate source URLs, and tracking-only URL
variants do not count as distinct public sources.
Each task includes a versioned reference-fact set, arm-specific claimed and
supported fact IDs, plus every citation's reviewed locator/support judgment.
The report emits macro fact recall/precision and citation-support rates;
manual answer/citation booleans remain separate adjudication fields.

```bash
python3 benchmarks/agent_product_eval.py \
  --records /path/to/real-agent-eval.jsonl
```

The command exits nonzero for incomplete or placeholder records. Use
`--allow-incomplete` only while preparing a draft; draft output must not be
used as a release or token-savings claim. See
`agent-product-eval-records.example.jsonl` for the record shape. The evaluator
does not call an answer model and does not infer correctness.
Historical links awaiting repeat validation are tracked in
`docs/acceptance/agent-product-eval-candidates-2026-10-23.md`; blocked sources
remain candidates but do not count as completed evaluations.
