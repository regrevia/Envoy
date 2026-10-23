# Host Evaluation Pilots

Files in this directory are explicitly incomplete pilot records, not release
benchmarks. Run them with `--allow-incomplete`; they must retain
`gate_status: incomplete` until the strict 20-source/40-task paired evaluation
is populated.

`2026-10-23-public-docs-codex-release-0.1.6.jsonl` combines five MDN HTTP
status pages and the Python JSON library documentation (six distinct source
URLs, twelve questions) into one pilot run.
Its generated summary is `2026-10-23-public-docs-codex-release-0.1.6.report.json`.
This is still only 6/20 sources and 12/40 tasks and has no paired baseline or
provider usage; it is not a comparative product result. The RFC 9110 and
Python docs pages were `partial`; each task is scoped to returned, located text,
not full-document coverage.

## MDN 404 / Codex / Envoy Release 0.1.6

`2026-10-23-mdn-404-codex-release-0.1.6.jsonl` records a real call to the
currently connected Envoy MCP and one follow-up search:

- The MCP doctor identified the connected runtime as the clean `0.1.6` release
  from commit `872fea695d75`; local OCR/ASR development changes were not used.
- Initial read returned the page definition and the limitation that 404 alone
  does not say whether missing is temporary or permanent. Both reference facts
  were checked against the public MDN page and returned page locator.
- Follow-up asked for MDN's 410 statement. `envoy_search` returned `empty`
  because the initial read had already covered and returned the matching
  material. The answer therefore reuses the original returned evidence and
  locator; no second download occurred.
- Under the pilot's manually reviewed three-fact rubric, Envoy returned all
  three facts with supporting locators (observed Envoy-arm fact recall,
  precision, and citation support: 1.0). These are descriptive results for
  one source and two questions, not a comparative quality estimate.
- The MCP host did not expose provider/model, actual usage, wall-clock request
  timing, host agent version, or Python version. Those fields remain `null`.
  There is no baseline arm. Baseline metrics, paired quality, timings, usage,
  and token-savings fields remain `null`; the pilot is not comparative evidence
  and cannot support a savings claim.

The machine-generated, explicitly incomplete summary is
`2026-10-23-mdn-404-codex-release-0.1.6.report.json`. Recreate it with:

```bash
python3 benchmarks/agent_product_eval.py \
  --records benchmarks/pilots/2026-10-23-mdn-404-codex-release-0.1.6.jsonl \
  --allow-incomplete
```

## MDN 410 / Codex / Envoy Release 0.1.6

`2026-10-23-mdn-410-codex-release-0.1.6.jsonl` is a second independent MDN
source under the same connected release MCP:

- Initial read returned the 410 definition and permanence statement with page
  locators.
- Follow-up search for the client behavior returned `empty` because that
  statement was already in the first returned body. The follow-up reused the
  retained material; it is counted as a second task but not as a second
  download.
- The Envoy-side pilot rubric marked the fact and citation correct. Baseline,
  provider usage and latency are unavailable and remain `null`.

Recreate the machine report with:

```bash
python3 benchmarks/agent_product_eval.py \
  --records benchmarks/pilots/2026-10-23-mdn-410-codex-release-0.1.6.jsonl \
  --allow-incomplete
```

## MDN 429 / Codex / Envoy Release 0.1.6

`2026-10-23-mdn-429-codex-release-0.1.6.jsonl` is a third independent MDN
source:

- Initial read returned the 429 meaning and the Retry-After header purpose
  with exact page locators.
- Follow-up asked for the value in MDN's example. `envoy_search` returned
  `empty` because the exact 3600-second example had already been returned in
  the first body; no second download was recorded.
- The manually reviewed Envoy-side facts and locators are recorded. The
  connected MCP still provides no baseline, actual provider usage, or latency,
  so comparative fields remain null.

Recreate the machine report with:

```bash
python3 benchmarks/agent_product_eval.py \
  --records benchmarks/pilots/2026-10-23-mdn-429-codex-release-0.1.6.jsonl \
  --allow-incomplete
```

## Python JSON / Codex / Envoy Release 0.1.6

`2026-10-23-python-json-codex-release-0.1.6.jsonl` records a technical
documentation pilot using the same connected release MCP:

- `envoy_read` returned the Python JSON encoder/decoder examples but reported
  `partial` because the long page exceeded the response budget.
- Follow-up `envoy_search` calls reused retained material. Their snippets were
  fragmented; the recorded tasks are limited to the `loads` example present in
  the initial returned block, not claims about the whole documentation page.
- The page returned one logo image URI. It was not visually inspected and is
  not counted as a model visual input. No second source download was recorded.
- Provider usage and request duration were unavailable, so those remain null
  and the pilot cannot contribute a comparative or savings result.

Recreate the report with:

```bash
python3 benchmarks/agent_product_eval.py \
  --records benchmarks/pilots/2026-10-23-python-json-codex-release-0.1.6.jsonl \
  --allow-incomplete
```
