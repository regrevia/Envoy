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
