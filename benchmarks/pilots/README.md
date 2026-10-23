# Host Evaluation Pilots

Files in this directory are explicitly incomplete pilot records, not release
benchmarks. Run them with `--allow-incomplete`; they must retain
`gate_status: incomplete` until the strict 20-source/40-task paired evaluation
is populated.

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
  three facts with supporting locators (observed pilot fact recall, precision,
  and citation support: 1.0). This is one source and two questions, not a
  quality estimate for Envoy generally; there is no comparison arm.
- The MCP host did not expose provider/model, actual usage, wall-clock request
  timing, host agent version, or Python version. Those fields remain `null`.
  There is no baseline arm. Accordingly paired quality, fact metrics, timings,
  usage, and token-savings fields remain null; the pilot is not comparative
  evidence and cannot support a savings claim.
