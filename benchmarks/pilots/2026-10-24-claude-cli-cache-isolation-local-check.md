# Claude Code Cache-Isolation Local Check

Date: 2026-10-24 Asia/Shanghai  
Observed UTC: 2026-10-24T07:07:57Z  
Status: local CLI check passed; authoritative server-ledger reconciliation pending.

## Setup

- Claude Code CLI: `2.1.197`
- Operator-reported gateway: `http://192.168.14.156:14000`, configured as the
  independent LiteLLM benchmark gateway.
- Model reported by Claude Code for all three calls:
  `anthropic/DeepSeek-V4.1-Flash`
- Envoy was not invoked. The requests used `claude -p`, no tools, and
  `--no-session-persistence`, each from a fresh temporary working directory.
- A fixed long calibration prompt was reused verbatim. A distinct
  `CLAUDE_CODE_EXTRA_BODY.cache_salt` value was set for each request.
- No API credentials, request headers, or settings content are included here.

The gateway endpoint was operator-reported; this run did not independently
inspect the endpoint configuration or server database. Direct server log
access is not available from this workstation.

## Results

| Request | Salt policy | CLI `usage.cache_read_input_tokens` | Model usage cache read | Model usage cache creation | Model usage input | Model usage output |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A1 | unique salt 1 | 0 | 0 | 0 | 256,214 | 330 |
| A2 | unique salt 2 | 0 | 0 | 0 | 256,216 | 540 |
| B | unique salt 3 | 0 | 0 | 0 | 256,216 | 277 |

All calls returned successfully and reported the same model identity. The
`usage` and `modelUsage` input totals differ, so this client output is not a
reconciled authoritative usage ledger.

## Gate Decision

This is positive end-to-end client evidence that the current CLI route reports
zero cache reads for three differently salted requests. It supersedes the
earlier local calibration where `cache_salt` did not isolate cache on the
previous route.

It does **not yet** satisfy the strict experiment cache gate because the
gateway-side request IDs and upstream callback records for these exact three
requests were not exported and reconciled. Do not mark the formal run
`verified_disabled` or `verified_isolated` based only on this file. The
operator-reported server-side `0/0/0` result should be attached as a redacted
metadata-only ledger/report with exact request IDs and timestamp before
formal execution.

Formal source readiness is a separate gate: the 20-package draft remains
unfrozen and cannot be executed until readable content snapshots, source
revision hashes, reference facts, and citation locators are frozen.
