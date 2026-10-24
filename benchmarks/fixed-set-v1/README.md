# Fixed Evaluation Set v1

Status: preparation only. No source in this directory is accepted until its
row in `source-intake.csv` has a canonical public URL, a current Envoy probe,
an independently reviewed reference-fact set, and a frozen content revision.
Do not start the confirmatory run while any of the 40 source rows is pending.

## Frozen Design

- 20 packages, four each: Bilibili, Douyin, Xiaohongshu, WeChat Official
  Accounts, and cross-platform synthesis.
- At least two distinct public source URLs per package; at least 40 unique
  canonical URLs across the set. A URL may not appear in two packages.
- Three identical turns in both arms: initial deliverable, a focused
  evidence follow-up, and a second follow-up that requires reuse or coverage
  expansion. Exact text is frozen in the package manifest before either arm.
- Keep each stratum's source class consistent. Cross-platform packages use
  sources from at least two of the four priority platforms.
- A and B run once per package with randomized order from
  `paired_experiment_plan.py --confirmatory-fixed-20`. Each arm has an isolated
  Claude Code profile and material cache; turns remain in the same arm session.
- B is built from one pinned `main` commit and records its wheel SHA-256,
  source revision, Skill hash, MCP tool catalog, and runtime capability report.
  This first batch is not the `v0.1.6` Release evaluation.
- The accepted model identity is the gateway-reported name
  `DeepSeek-V4.1-Flash`. Do not claim checkpoint, tokenizer, or quantization
  identity. Keep the gateway policy digest fixed for the whole batch.

## Source Acceptance

For each URL, record canonical URL, source type, public access result, capture
time, material/source SHA-256 when content is available, returned coverage,
and known unresolved gaps. A 404, login wall, TLS failure, or empty extraction
remains a result and must not be swapped after either arm has seen it. If a
source cannot support a factual task, replace it only before the manifest is
frozen, document why, and re-freeze the full manifest hash.

Reference facts must be established from the public source independently of
either model output. Each fact has a stable fact ID, claim, source ID, exact
locator (timestamp, paragraph/block, or image index), and an adjudication note.
For inaccessible sources, use a separately labeled robustness package rather
than inventing reference facts.

## Candidate Revalidation Log

The following are preparation probes only and are not accepted rows:

| Candidate | Result on 2026-10-24 | Decision |
| --- | --- | --- |
| Bilibili `BV1zj411K7zL` | Media exceeded 8 MiB and 12 MiB budgets, including a 0-30s range request | Do not use unless a lower-budget public subtitle path is independently verified |
| Bilibili `BV1zj411K7zL` higher-cap retry | A 64 MiB ceiling allowed a bounded 0-45s ASR read; a fresh 12 MiB control failed again | Candidate only; 8/12 MiB were probe caps, not Envoy defaults. The runtime default is 256 MiB. Fix and record the Envoy video cap before the A/B batch |
| Bilibili `BV1xx411c7mD` | No subtitle track; yt-dlp produced no audio file for bounded ASR | Exclude from factual set; may be robustness-only |
| Bilibili `BV1Qb4SenEKY` | Chinese ASR 00:00-00:30 ready; noisy opening, chapter title is AI course intro | Candidate only; needs full transcript/reference review |
| Bilibili `BV1pr421K7zP` | Chinese ASR 00:00-00:30 ready; opening resembles above candidate | Possible duplicate series/content; not counted until source identity/task independence is reviewed |
| Bilibili `BV1w5TL6eEsW` | TLS unexpected EOF during media page access | Exclude unless it succeeds through an approved network path; no TLS bypass |
| Bilibili `BV1d142147ay` | No subtitle track; yt-dlp produced no audio file for bounded ASR | Exclude from factual set; may be robustness-only |
| Bilibili `BV1JZ421u7Xo` | Media exceeded 10 MB budget | Do not use unless a bounded public evidence route is verified |
| Bilibili `BV1DK411G7hc` | English ASR 00:00-00:45 ready; useful outline of computer fundamentals | Candidate only; needs task/reference review |
| Bilibili `BV1DK411G7hc/?p=2` | Same series, p02, English ASR 00:00-00:45 ready | Distinct video segment but same series; can be paired only within one package, not counted as separate package-level source |
| Bilibili `BV1Qb4SenEKY` + `?p=2` | Same AI-course series, p01 course intro and p02 outline; bounded Chinese ASR available | Candidate package pair; transcript accuracy and course facts still need review |
| Bilibili `BV1nJ411V7bd` + `BV1b7411N798` | Separate Qingdao University and Wangdao data-structures course intros | Candidate pair; both 45-second openings are mostly course framing, not enough for substantive comparison without reading later lesson ranges |
| Bilibili `BV1c4411d7jb` + `BV1gV411h7r7` | Computer-network course intros, one mentions a CNNIC survey and one identifies Xie Xiren's 7th edition | Candidate pair for course framing; only the first 45 seconds have been transcribed |
| Douyin `7698386448961683813` | Extractor requested fresh cookies; browser challenge exceeded 60s budget | Exclude from accepted factual set; no cookie import or challenge bypass |
| Douyin `7647491904791186714` | Public title and chapter summary expose domino logic gates/half-adder/full-adder; transcript and media not extracted | Partial candidate; questions must be limited to page evidence unless later video-level material is obtained |
| Douyin `7690052789893361826` | Public title and page text compare phone calculator and computer pi calculation; no transcript/media | Partial candidate; verify any numerical claims independently |
| Douyin `7691125608985693440` | Page title/chapter summary gives cost claims and warns compute cost is not total company cost; page marked AI-generated | Partial candidate only; do not freeze its numerical facts without independent authoritative verification |
| Douyin `7464637783957687615` | Public title/caption describes AI-coded heating-system animation; transcript/media unavailable | Partial candidate; use only explicitly returned page claims unless full evidence becomes available |
| Xiaohongshu `64c9b3ad000000001f02b29a` | Correct automatic detection resolved to the platform 404 “note temporarily unavailable” page | Not accepted; no note content or images were exposed |
| Xiaohongshu `xhslink.com/JNulHU` | Automatic redirect landed on generic `/explore` homepage and recommendations, not a unique note | Not accepted; do not treat homepage cards as note evidence |
| Xiaohongshu public share candidate | Platform returned risk-control code `300012` before exposing the note | Keep as access-boundary evidence only; don't change egress or use cookies to force a read |
| Xiaohongshu `67da2ecb000000001c0047e1` and `67c6d4f50000000029039014` | Both exact note URLs resolved to the platform 404 “note temporarily unavailable” page | Not accepted; retain as stale-link failures, not content evidence |
| WeChat `i6rO6k1Br0XgiA9DRQIaDQ` | Article body resumed to text-complete; parameter basics | Candidate for text-only questions; article images were only partially returned |
| WeChat `C46jhKFNAhiIuri9d8_MsA` | Article body resumed to text-complete; LLM parameter principles | Candidate for text-only questions; verify claims independently before gold labels |
| WeChat `R3BxCMu5CSFo_HPcVJTuOA` | Article body resumed to text-complete; Transformer overview | Candidate for text-only questions; article images remain uncovered |
| WeChat `WUyY2F6Cr9EU15FHeKM9eg` | Article body resumed to text-complete; DeepSeek MLA | Candidate for text-only questions; math/diagram details may depend on images and must be excluded unless inspected |
| WeChat `6FdfPNi9gOf_QQbwlkAdBQ` | ChatSQL overview; body resumed to text-complete | Candidate; score source claims about architecture, failure modes and suitability, not correctness of its advice |
| WeChat `IJvzjWMLMhljkMQO2oa2bg` | Enterprise AI Agent implementation article; body text returned | Candidate; image coverage partial, keep tasks text-scoped |
| WeChat `dAnNHayrE49FEl8TcLII2Q` | Function Calling, MCP and Skills comparison; body resumed to text-complete | Candidate; author describes their framework perspective, treat as attributed opinion |
| WeChat `0jB157hXY2AwIcji0jywBQ` | Agent framework selection article; body resumed to text-complete | Candidate with historical scope: article dated 2024-11-05, do not treat framework claims as current product status |

These failures are preserved in the local preparation record; none is silently
replaced after A/B execution because the experiment has not started.

## Resource Budget Policy

The Envoy runtime schema defaults `max_download_bytes` to 256 MiB. The smaller
8-12 MiB values in the probe log were explicit exploration limits, not the
installed product default. For the confirmatory B arm, pin the same
`max_download_bytes: 268435456` and `max_work_seconds` for every package; do not
adapt the cap after seeing a particular source fail. A lower cap may be chosen
before preregistration if the study specifically measures constrained-device
behavior, but that is a separate condition.

The video range currently bounds ASR/transcription, but yt-dlp downloads audio
before Whisper clips the requested range. Thus a 45-second ASR request can
consume bytes for the full source audio. Keep this limitation in source
coverage and record the exact byte cap. `usage.local.downloaded_bytes` is the
postprocessed audio-file size; the progress hook separately enforces aggregate
transfer bytes but does not currently expose the aggregate, so do not report
that local field as network bytes transferred.

TLS EOF and certificate errors are transport failures, not budget failures.
Retry once with the same approved network/proxy and TLS verification. Do not
raise the byte cap to address them, disable certificate checks, or introduce an
unapproved proxy.

## Scoring

Blind reviewers receive anonymized outputs with A/B labels removed. Score
source-supported fact recall, unsupported claim rate, citation-to-fact support,
required deliverable completion, material reuse, missing-evidence disclosure,
and access outcome. Resolve disagreements before unblinding. Report completion
and usage intention-to-treat, plus conditional successful-pair metrics.
Token or latency improvements never compensate for lower evidence quality.

## Start Gate

1. Complete and independently review all 40 source rows and package/task facts.
2. Save the immutable package manifest; record its SHA-256 and rubric version.
3. Confirm the pinned B build and the server-reported model/gateway policy.
4. Create the randomized plan with cache status `per_request_salt`; the
   completed server-ledger validator promotes it to
   `verified_per_request_salt` only if all request evidence passes.
5. Run a separate 2-package instrumentation rehearsal; discard its outputs and
   request IDs from the confirmatory batch.
6. Begin the 20-package batch only when correlation metadata and server-ledger
   export are proven end to end. Every formal server request must have a unique
   salt hash and zero cache-read tokens.

The three server calibration requests from October 24 are isolation
calibration only. They do not belong to the rehearsal or evaluation dataset.
