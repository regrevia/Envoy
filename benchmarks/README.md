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

## Real Agent Product Evaluation

Use `benchmarks/agent_product_eval.py` for the versioned end-to-end
evaluation. It consumes JSONL records containing real public sources and
host-agent task outcomes. Each source must have at least two tasks so the
second task measures material reuse and follow-up behavior. The strict gate
requires at least 20 sources and 40 tasks, one provider/model identity, source
locators, answer and citation review, first-evidence and total latency, Envoy
calls, visual inputs, repeated downloads, and actual host usage.
The first JSONL record must identify the Envoy commit, host agent/OS, optional
ASR/OCR status, and the provider/model used for both arms.

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
