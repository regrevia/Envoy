# Knowledge evaluation — four branches, three arms

Phase 4 of the capability-bundle plan. This document defines what the evaluator
measures, what it refuses to claim, and why a report from it is a piece of
evidence rather than a summary of enthusiasm.

The evaluator is `knowledge_eval.py`, driven by `envoy knowledge eval --run
<record> [--json]`. It is a local, deterministic scorer. It calls no model, opens
no socket, and never writes to the material store or the unit store. Its only
output is a report about a run somebody else performed.

## 1. The question the three arms exist to answer

The product claim under test is not "Envoy reads pages". It is: *a bundle of
evidence-linked units makes a host agent measurably better than the same agent
with the same material and no bundle.* That claim has three layers, and a single
arm cannot separate them.

| Arm | What the host agent gets | What the arm isolates |
| --- | --- | --- |
| `plain` | its own fetch, no Envoy | the floor: what the agent already does |
| `material` | retained Envoy material, no units | reach and retention — is `envoy_read` worth a call? |
| `bundle` | the same material plus a Knowledge Pack and compiled `SKILL.md` | distillation — does turning material into units add anything? |

`material − plain` answers whether Envoy's *material* has value. `bundle −
material` answers whether Envoy's *knowledge* has value. The second delta is the
one this project lives or dies on, and it is only meaningful because the first
one was measured with the same tasks, the same model, the same budget and the
same frozen commit. An arm that changes two things at once measures neither.

Fairness conditions, all checked rather than assumed:

- every arm has the same `task_id` set (paired; no dropped tasks);
- every arm declares the same budget and the record's observed calls stay inside
  it;
- one model identity for all three arms;
- `cache_isolation_status` is `verified_disabled` or `verified_isolated`. A
  `cache_confounded` or `unknown` batch is reportable but never claimable, because
  a cheaper arm may simply have been warmer.

## 2. The four branches

Collapsing these into one score is how an evaluation stops being able to say
*which* layer failed. Each branch has its own evidence source and its own refusal.

### 2.1 `evidence` — mechanical, no reviewer

For every unit the run used, and every citation any arm produced: the
`material_id` and `evidence_id` must resolve against the retained material, the
locator must equal the one the material itself stores, and the recorded source
revision must equal the revision observed now.

The count is over citations and refs, not over prose. A unit whose refs resolve
is not thereby a true claim — that is branch 2 — but a unit whose refs do not
resolve is not evidence of anything.

Fails closed: any unresolved ref or citation is a blocker, and a blocker makes
every claim ineligible. This branch can never be improved by review.

### 2.2 `distillation` — human labels, two independent reviewers

Each unit carries reviewer verdicts: `supported` (the unit says no more than its
cited evidence does), `overreach` (the unit states something true of the source
but not supported by the cited passage), `unsupported` (the cited evidence does
not establish the claim at all).

`overreach` is the important middle verdict. Without it, every reviewer
compresses to a boolean and the interesting failure — a plausible summary that
wandered one sentence past its locator — disappears.

A unit's verdict counts only when it is *effective*:

- two or more independent reviewers, and
- either they agree, or a recorded adjudication resolves the disagreement.

A single-reviewer unit, or an unadjudicated split, is a blocker rather than a
zero. This is the direct consequence of the failure that produced this design: an
A/B whose two arms both scored 0 and where nobody had judged whether the answers
were right is not a measurement, it is two numbers.

Reported: `labeled` / `units`, `coverage` (effective units over all units),
`precision` (`supported` over effective units), and `overreach_rate`. Precision is
`null`, never `0`, when nothing is labeled.

### 2.3 `skill` — mechanical artifact check plus human routing

Mechanical: every member must be `promoted` at scoring time (a candidate in a
shipped pack is a publishing error, not a result), every member's refs must still
resolve, the pack closure recomputed now must equal the membership the run
declared, and each declared artifact's bytes must equal what `render_skill`
produces from the stored unit today. Same unit, same bytes, or the artifact is
stale.

Human: routing. For each task a reviewer says whether the pack *should* apply.
`routing_recall` is the share of `should_apply` tasks where the bundle arm
actually used a pack unit; `routing_precision` is the share of applied bundles
that the reviewer agreed should apply. A skill nobody's trigger matches is a
well-formed useless document, and only a human can say which.

### 2.4 `runtime` — task outcome, ledger, and honest token accounting

Per task per arm: answer correctness (reviewed, two reviewers for claimability),
reference-fact recall and precision, citation support rate, Envoy calls, elapsed
time to first evidence and total, and whether a pack unit was applied.

Tokens come only from the host's own usage record. Envoy's `estimated_tokens` is a
tokenizer-independent text estimate and is **never** compared against provider
counts as though they were the same measurement; if the host usage is missing the
token fields are `null` and the pair is excluded from the token delta, not filled
with an estimate. A report that substitutes the estimate is worse than a report
with a hole, because the hole is visible.

Paired deltas are reported with a deterministic cluster bootstrap (1000
resamples, seed derived from the record bytes) so that the same record always
produces the same interval.

## 3. What makes a claim emittable

The report separates four gate layers, in the shape
`benchmarks/agent_product_eval.py` already uses:

| layer | question |
| --- | --- |
| `protocol_valid` | is the record well formed, and are the three arms paired? |
| `data_complete` | is every task, citation, ref and label actually present? |
| `quality_reviewed` | did two independent humans judge the answers and the units? |
| `claim_eligible` | is the measured delta supported, given the three layers above |

A run can be `complete` and still not `passed`: no improvement is a result, not an
incompleteness. `gate_status` is `failed` (schema errors), `incomplete` (missing
inputs), `complete` (everything present, no supported improvement), or `passed`
(supported improvement).

`claim_blockers` is a sorted list of stable codes — `no_paired_tasks`,
`single_reviewer:<unit_id>`, `unadjudicated_disagreement:<unit_id>`,
`not_promoted:<unit_id>`, `artifact_stale:<unit_id>`,
`pack_membership_mismatch`, `cache_confounded`, `usage_missing`,
`no_success_signal` — so a reader can see *why* a claim is absent without reading
the prose.

## 4. What this does not measure

- It does not measure whether the model was capable enough to use the bundle. A
  weak model in all three arms proves nothing about the bundle.
- It does not prove the units are *good*, only that humans agreed they stay inside
  their evidence and that the artifact is mechanically intact.
- It does not establish that the task set is representative. Every report states
  its task set size and every claim is conditional on it.
- It does not compare Envoy against another tool. `plain` is the host's own fetch,
  which is the only fair floor.
- It does not evaluate routing over a corpus. That is
  `benchmarks/agent_product_eval.py --routing-only`.

## 5. Record shape

One JSON object, or JSONL where the first line is the run. Unknown fields are
rejected, so a typo in a label name fails loudly instead of silently scoring
zero.

```json
{
  "schema_version": "envoy.knowledge-eval/1",
  "run": {
    "envoy_commit": "08a40bd",
    "at": "2026-11-24T00:00:00Z",
    "host_agent": "codex",
    "model": "one-model-identity",
    "cache_isolation_status": "verified_disabled",
    "budget": {"max_calls": 12, "max_seconds": 300}
  },
  "arms": [
    {"name": "plain", "tools": ["host_fetch"]},
    {"name": "material", "tools": ["envoy_read"]},
    {"name": "bundle", "tools": ["envoy_read"], "pack": {"unit_ids": ["ku-…"], "target": "codex"}}
  ],
  "reviewers": [{"name": "r1", "independent": true}, {"name": "r2", "independent": true}],
  "units": [
    {
      "unit_id": "ku-…",
      "artifact": {"target": "codex", "path": "…/SKILL.md"},
      "reviewers": [{"reviewer": "r1", "verdict": "supported"},
                    {"reviewer": "r2", "verdict": "overreach"}],
      "adjudication": {"by": "r3", "verdict": "overreach"}
    }
  ],
  "tasks": [
    {
      "task_id": "t1",
      "reference_facts": [{"fact_id": "f1"}],
      "arms": {
        "plain": {
          "correct": [{"reviewer": "r1", "correct": true}, {"reviewer": "r2", "correct": true}],
          "claimed_facts": ["f1"], "supported_facts": ["f1"],
          "citations": [{"material_id": "mat-…", "evidence_id": "ev-…", "locator": {},
                         "reviewer": "r1", "supported": true}],
          "calls": 3, "elapsed_ms": 42000,
          "usage": {"provider": "p", "model": "m", "input_tokens": 1200, "output_tokens": 80},
          "applied_unit": null,
          "should_apply": [{"reviewer": "r1", "applies": false}]
        }
      }
    }
  ]
}
```

## 6. Where the branch code lives

`knowledge_eval.py` is pure scoring: `evaluate_run(record, root=…)` returns the
report dict and `render_report(report)` returns Markdown. Everything it knows
about units, materials and artifacts it re-derives from the store at scoring
time, because an evaluation that trusts a number the run wrote about itself is the
thing this engine exists to replace.

The entry point is `envoy knowledge eval --run <record> [--json]`, which prints
`render_report(report)` or the report dict. It is CLI-only on purpose: scoring a
run is a maintainer's act, not a capability an agent should be able to reach for
mid-task.

A record that names a field this schema does not know is refused with
`invalid_record` rather than scored with that field ignored, because a run that
quietly adds a column would otherwise look better than one that does not.
