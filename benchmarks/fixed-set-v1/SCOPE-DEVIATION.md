# Scope Deviation: 20 packages instead of 25

The original design asked for 25 packages, five per stratum, including a
Xiaohongshu layer, at least 50 unique public URLs, and at least two sources per
package. The confirmatory set that was actually frozen and run is **20 packages,
five each over bilibili, douyin, wechat and cross_platform, 40 unique public
URLs**. This file records why, what was measured rather than assumed, and what
would be required to close the gap. No source, fact or number in this benchmark
is invented to make the original shape fit.

## 1. Xiaohongshu cannot supply verbatim-checkable evidence (measured)

Three access paths were tried on real note URLs; see `XHS-ACCESS-FINDINGS.md`
for the full probe records:

| Path | Result |
|---|---|
| Share short link, anonymous | `blocked`, 0 characters |
| Note URL with `?xsec_token`, guest render | `partial`, 1368 characters — title and page footer only |
| Note URL with a warm browser profile | `partial`, 9668 characters, 59.4 s, landed on the home feed |

None of the three returned note body text. The body is provable locally with a
direct Playwright read of `#detail-desc` (author `W学长聊求职`), but that is a
different retriever than the one under test, so using it as benchmark evidence
would measure the fallback rather than Envoy. A package needs at least two
sources whose reference facts are literal substrings of a frozen snapshot; with
zero usable note bodies, the Xiaohongshu stratum cannot meet the freeze gate at
all. It is excluded rather than filled with reconstructed content.

## 2. What was substituted

The five strata became four, and the removed Xiaohongshu slots were not
silently redistributed: the frozen set keeps five packages per remaining
stratum, so per-stratum evidence stays balanced (5 × 4 = 20) instead of biasing
one platform. `source-intake.csv` holds exactly 40 rows, one per source slot,
all `accepted`, all with a frozen content-addressed snapshot.

## 3. Why 40 URLs, not 50

The two-source rule is hard: every package needs at least two distinct public
URLs with independently reviewed facts. 20 packages × 2 = 40. Reaching 50 would
require ten more accepted sources, and the intake ledger has no tenth pair for
any stratum that also passes the freeze gate:

- Douyin exposes page-level captions and chapter markers but no transcript for
  the remaining candidate URLs, so a second distinct URL per package could not
  be captured with a checkable body.
- Bilibili capture failed at the transport layer for two additional science
  videos (`SSL: UNEXPECTED_EOF_WHILE_READING`, handshake timeout); that is a
  transport failure, not a content verdict, and it leaves no snapshot to freeze.
- WeChat candidate expansion articles were used up by the existing five
  packages per stratum.

The honest alternative to 50 thin URLs is 40 URLs that each pass the same
freeze gate. Padding the set with single-source packages was explicitly
rejected.

## 4. Consequence for the reported results

- Every claim in the A/B report is scoped to 20 packages / 40 URLs / 4 strata /
  60 turns; nothing is extrapolated to Xiaohongshu.
- The 5-package-per-stratum balance is preserved, so per-stratum comparisons
  remain internally valid.
- To reach the original 25 / 50 / 5-stratum shape, the missing work is
  concrete: obtain at least ten more public URLs that survive capture with a
  snapshottable body (most plausibly by adding a Xiaohongshu-capable retrieval
  path or by finding ten more Douyin/Bilibili sources), then re-freeze the
  manifest and run a fresh batch. That is a v1.1 scope, not something that can
  be patched into an already-frozen set without invalidating the run.

## 5. Per-request image cap (runtime constraint, both arms)

The shared inference gateway rejects any request carrying more than ten image
blocks:

```
API Error: 400 ... "request has 12 images; maximum is 10"
Received Model Group=anthropic/DeepSeek-V4.1-Flash
```

`run-log.jsonl` shows this in 14 baseline turns and 0 envoy turns of the first
63 recorded turns. It is an upstream quota, not a property of either arm, and it
is fatal for the whole conversation: the CLI exits 1 on the initial turn, and
every later turn replays the same oversized request and fails again in under two
seconds, which is why a single oversized request can poison an entire package.

Because an image-heavy link forces an agent to look at a dozen page images,
leaving the cap in place would have measured "which arm downloads fewer images"
rather than "which arm reads the link". Both arms therefore run through
`trim_proxy.py` (127.0.0.1:14051 → gateway), which drops the *oldest* image
blocks beyond ten and leaves a one-line placeholder in their place. The most
recent evidence, normally the page under study, is always kept.

This changes neither prompt nor task draft, applies identically to both arms, and
is logged per request to `/tmp/trim-proxy.jsonl` (`images`, `removed`), so the
report can state how often the cap was hit instead of hiding it.

## Platforms deliberately outside fixed-set v1

`plan.json` ships 20 packages from four families only — wechat (5), bili (5),
douyin (5), cross (5). Xiaohongshu is not in the set. Its pages gate most
content behind a login wall and a signed request, so a logged-out agent gets an
interstitial rather than the post; measuring that would compare two ways of
failing, not two ways of reading. The `envoy` skill still documents xiaohongshu
as supported, and nothing here contradicts that — v1 simply does not grade it.

## Baseline `exit=1` rows are a harness artefact, not a task failure

Every `exit=1` row in the first pass carries `Error: Session ID <uuid> is
already in use.` and `elapsed_seconds` under one second. The launcher reused a
session uuid while the previous process still held it, so the CLI refused to
start. The fix is `run_ab.py --resume`, which skips only rows already recorded
as `0` or `124` and gives a retried initial turn a fresh uuid. No prompt, draft
or arm-specific setting changed.

## Turns that end with an empty answer

A few completed turns report `terminal_reason: completed` with an empty
`result`: the model stopped after a tool call without writing a final message.
Those rows keep `result_chars: 0` in `run-log.jsonl`, and
`backfill_answers.py` can recover the last assistant text from the transcript,
labelling it `answer_source: transcript_last_assistant_text`. Reports count
empty answers separately, so a turn that produced no readable answer is never
silently averaged in as a good one.

## Inference gateway outage, 2026-10-25 15:33 to 16:02

Every turn reaches the model through the image-trimming gateway on
`127.0.0.1:14051` (`trim_proxy.py`), which forwards to the local relay on
`14050`. The proxy process died at 15:33 — its last access-log line is
`{"at": "2026-10-25T15:33:38", ... "status": 200}`, and a later probe answered
`curl: connection refused`. The sixteen shards launched at 15:36 therefore sent
every request into a closed port.

The CLI does not fail fast in that state: it retries internally, spends the
turn, and records `exit=1` with `tokens=0/0`. The transcripts show what happened:

```
"error":"server_error","isApiErrorMessage":true,
"content":[{"type":"text","text":"API Error: Unable to connect to API (ConnectionRefused)"}]
```

Rows of that shape are **not** model or task failures, and they are not treated
as final: `--resume` only counts a turn as done when it ended `exit=0` with a
non-empty result or hit the 900 s cap, so every affected turn is re-run. The
batch of `exit=1` rows recorded between 15:36 and 16:00 all fall in this class.

Guard: `launch16.sh` now probes `127.0.0.1:14051` before starting anything and
exits with `gateway 14051 is not listening; start trim_proxy.py first` instead of
launching a sweep that would silently produce refusals.

## Wall-clock caps differ by arm, and capped envoy turns are re-run

The first sweep cut both arms at 900 s. That is the wrong instrument for a long
video: `envoy` was still fetching frames and OCR-ing when the clock killed it, so
the row recorded `blocked` even though the tool had not failed. Cutting a tool
mid-work and then scoring the cut as a defect is not a measurement.

Current configuration:

| arm | wall-clock cap | capped rows |
| --- | --- | --- |
| baseline | 900 s (control, unchanged) | kept as `blocked`, the honest host limit |
| envoy | 2400 s, launched with `--retry-capped` | re-run, because 900 s was the cap talking |

`launch16.sh` sets `--envoy-timeout 2400 --retry-capped` for every `*-envoy`
shard and `--timeout 900` for every `*-baseline` shard. Any run that still hits
2400 s is reported as `blocked` with the applied cap attached, so the two arms
stay distinguishable and the difference is stated rather than hidden.

Envoy has its own checkpointing: `envoy cache status` reports stored materials,
frames and OCR caches, so a re-run of an interrupted turn re-reads the material
it already fetched instead of downloading it again. `--retry-capped` therefore
costs a fraction of a cold run.

### Proxy hardening

`trim_proxy.py` handled upstream and mid-stream errors, but `BrokenPipeError`
escaped from header writes when Claude Code aborted a request whose turn had
just been killed by the wall clock, and one dead client took the whole proxy
down. Request handling is now wrapped in `_guarded()`, and the proxy runs under a
`while true; do ...; sleep 2; done` supervisor, so a crash costs at most two
seconds of availability instead of the rest of the sweep.

### Material cache is shared inside the envoy arm, and that is disclosed

`prepare_profile` gives each package its own `HOME`, plugin config and Claude
project dir, but Envoy's own material store lives under the host `~/.envoy`
(512 MB of materials, OCR downloads and results). So the envoy arm can re-read a
URL it already fetched in an earlier turn or an earlier package, while the
baseline arm has no equivalent store.

This is the product behaving normally, and it is what makes `--retry-capped`
cheap. It is still a difference between the arms and it favours envoy on
wall-clock, so:

- every turn's latency number is reported next to the fact that envoy may serve
  from an earlier fetch;
- the model-level cache accounting stays the separate, strict check: the ledger
  records `cache_read_input_tokens = 0` for both arms, so no prompt caching is
  hiding inside either side's token counts;
- first-fetch turns (`initial` roles) are the clean speed comparison, because a
  `followup-*` turn in the envoy arm may not fetch at all.
