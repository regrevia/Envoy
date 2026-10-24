# Fixed Evaluation Set v1

Status: preparation only. No source in this directory is accepted until its
row in `source-intake.csv` has a canonical public URL, a current Envoy probe,
an independently reviewed reference-fact set, and a frozen content revision.
Do not start the confirmatory run while any of the 40 source rows is pending.

## Intake Snapshot (2026-10-24)

- 27 of 50 source slots contain a unique URL; 27 unique
  canonical URLs across 25 packages.
- Current intake states: 14 已接受, 9 待复核,
  4 排除-鲁棒性, 23 待补充. These states are
  deliberately separate from Envoy's per-read result such as `ready`,
  `partial`, or `blocked`.
- Populated strata: Bilibili 8, Douyin 4,
  Xiaohongshu 4, WeChat 10, cross-platform
  1. All four Xiaohongshu URLs remain failed; no note
  body or note images were obtained.
- The four WeChat parameter articles are 已接受: each body was captured through
  Envoy as a byte-stable snapshot under `materials/<sha256>.txt`, and their
  24 reference facts were re-checked as literal substrings of that snapshot.
- The six WeChat expansion articles (wechat-03/04/05) are also 已接受: 37 reference
  facts were sliced from their snapshots and re-checked as literal substrings, and the
  `wechat-04a` embedded code region is explicitly excluded from quotation.
- Four Bilibili videos are 已接受 on local-ASR transcripts captured from the
  opening 1.7-5 minutes (`BV1Qb4SenEKY` p1 and p2, `BV1nJ411V7bd`,
  `BV1b7411N798`), stored as snapshots and accepted with the ASR caveat that
  frames, slides, and on-screen code were never inspected. Their packages are
  scoped to what the speaker actually says, never to slide content.
- The five `*-05` packages are expansion slots added when the design moved
  from 20 to 25 packages. `xhs-05` is the labeled robustness package: it is
  scored on access honesty, never on reconstructed note content.
- Four Douyin URLs now have archived page-level snapshots (`douyin-01..04`:
  real titles, captions, chapter summaries and chapter markers; no transcript
  or media), but they stay `待复核` because no second distinct Douyin URL could
  be captured for each package. `envoy discover` refuses without a public
  profile URL and page text exposes none, so the two-source rule was kept
  rather than relaxed to single-source packages. `douyin-02`'s snapshot is
  explicitly a retained earlier capture of the same URL, not a fresh fetch.
- On the same day Bilibili capture failed at the network layer for two new
  science videos (`BV14Enb6LEDe`, `BV1ZLnt6tEpT`): `SSL: UNEXPECTED_EOF_WHILE_
  READING` / handshake timeout, so the Bilibili stratum stays at its four
  already-accepted videos. That is a transport failure, not a content verdict.
- There are no duplicate URLs across the populated slots. Package pairing,
  independent fact review, and task quality still need review.

### Intake State Definitions

- `待复核`: Envoy returned usable source material, but independent reference
  facts, source locators, task wording, and coverage boundaries are not frozen.
  It is not allowed into the confirmatory A/B run yet.
- `已接受`: independent facts and task package are reviewed, the source
  revision is frozen, and the row is eligible for the confirmatory run.
- `排除-鲁棒性`: the exact URL was tested but did not provide enough primary
  material, for example a 404, login challenge, empty player shell, or
  transport failure. Keep it for access-failure reporting; do not use it as a
  successful source.
- `待补充`: the slot has no source URL yet.

The old labels `candidate`, `failed`, and `unverified` are not used for the
intake decision anymore. Historical probe-log prose may use them to describe
what happened during exploration, but the CSV state is authoritative.

## Frozen Design

- 25 packages, five each: Bilibili, Douyin, Xiaohongshu, WeChat Official
  Accounts, and cross-platform synthesis. 20 of them are the original design;
  the five `*-05` slots are the modest scope expansion, and `xhs-05` is the
  robustness package for a platform that blocks anonymous reads.
- At least two distinct public source URLs per package; at least 50 unique
  canonical URLs across the set. A URL may not appear in two packages.
- Three identical turns in both arms: initial deliverable, a focused
  evidence follow-up, and a second follow-up that requires reuse or coverage
  expansion. Exact text is frozen in the package manifest before either arm.
- Keep each stratum's source class consistent. Cross-platform packages use
  sources from at least two of the four priority platforms.
- Every package declares at least one `authority_anchors` entry: an independent
  authoritative document (vendor API reference, standard, or peer-reviewed
  paper) that is not one of the package's own sources, plus a note on what it
  supports. Numeric or regulatory claims that are not quoted from a package
  source must be marked `claim_basis: external_fact` and carry a corroboration
  URL and quote; the validator rejects the weaker shape.
- A and B run once per package with randomized order from
  `paired_experiment_plan.py --confirmatory-fixed`. Each arm has an isolated
  Claude Code profile and material cache; turns remain in the same arm session.
- B is built from one pinned `main` commit and records its wheel SHA-256,
  source revision, Skill hash, MCP tool catalog, and runtime capability report.
  This first batch is not the `v0.1.6` Release evaluation.
- The accepted model identity is the gateway-reported name
  `DeepSeek-V4.1-Flash`. Do not claim checkpoint, tokenizer, or quantization
  identity. Keep the gateway policy digest fixed for the whole batch.

## Source Acceptance

For each URL, record canonical URL, source type, public access result, capture
time, material/source SHA-256 when content is available, returned coverage, and
known unresolved gaps. A 404, login wall, TLS failure, or empty extraction
remains a result and must not be swapped after either arm has seen it. If a
source cannot support a factual task, replace it only before the manifest is
frozen, document why, and re-freeze the full manifest hash.

Accepted material is stored as a content-addressed snapshot,
`materials/<sha256>.txt`, and the recorded revision hash is recomputed from that
file. A source cannot be 已接受 on a hash nobody can reproduce.

Reference facts must be established from the public source independently of
either model output. Each fact has a stable fact ID, claim, source ID, exact
locator (timestamp, paragraph/block, image index, or section), a verbatim
`quote`, and a `claim_basis` of either `source_claim` or `external_fact`. The
freeze gate rejects a fact whose quote is not a literal substring of the stored
snapshot, so paraphrase and invention cannot reach the manifest. For
inaccessible sources, use a separately labeled robustness package rather than
inventing reference facts; scored behaviour there is disclosure, not recall.

WeChat captures currently return `result: partial` with a single body paragraph
and empty image blocks: text claims are in scope, while anything that only
exists in a figure, table, or formula is out of scope and must be declared as a
coverage gap rather than guessed.

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
| WeChat `i6rO6k1Br0XgiA9DRQIaDQ` | Article body captured, 10256 chars, byte-stable snapshot | 已接受 2026-10-24; six facts quote-verified; images out of scope |
| WeChat `C46jhKFNAhiIuri9d8_MsA` | Article body captured, 7654 chars, byte-stable snapshot | 已接受 2026-10-24; six facts quote-verified; formulas degraded in capture and excluded |
| WeChat `R3BxCMu5CSFo_HPcVJTuOA` | Article body captured, 5197 chars, byte-stable snapshot | 已接受 2026-10-24; facts quote-verified; diagrams out of scope |
| WeChat `WUyY2F6Cr9EU15FHeKM9eg` | Article body captured, 5896 chars, byte-stable snapshot | 已接受 2026-10-24; math/diagram details excluded from scope |
| Bilibili `BV1Qb4SenEKY` p1 | Course-introduction speech, 738 chars of local ASR (0-100 s) | 已接受 2026-10-24; opening 12 s is garbled and excluded; slides never inspected |
| Bilibili `BV1Qb4SenEKY` p2 | Same multi-part course, part 2, 712 chars of local ASR (0-111 s) | 已接受 2026-10-24; ASR came back in Traditional Chinese; quotes kept verbatim |
| Bilibili `BV1nJ411V7bd` | Data-structures course introduction, 2431 chars of local ASR (0-5 min) | 已接受 2026-10-24; first five minutes only, slides not inspected |
| Bilibili `BV1b7411N798` | Data-structures course introduction, 2801 chars of local ASR (0-5 min) | 已接受 2026-10-24; first five minutes only, slides not inspected |
| WeChat `6FdfPNi9gOf_QQbwlkAdBQ` | ChatSQL explainer, 6822 chars, byte-stable snapshot | 已接受 2026-10-24; 12 facts quote-verified; images out of scope |
| WeChat `IJvzjWMLMhljkMQO2oa2bg` | Enterprise AI-agent practice, 3739 chars, byte-stable snapshot | 已接受 2026-10-24; facts quote-verified; diagrams not read |
| WeChat `dAnNHayrE49FEl8TcLII2Q` | Function Calling / MCP / Skills analysis, 9431 chars | 已接受 2026-10-24; 13 facts; token-split code region excluded |
| WeChat `0jB157hXY2AwIcji0jywBQ` | Agent framework selection guide, 8876 chars | 已接受 2026-10-24; facts quote-verified; screenshots not read |
| WeChat `uxU_rWvP9HWOMzg7Zg6oKA` | SuperCLUE 2024-08 Chinese LLM report, 7429 chars | 已接受 2026-10-24; 12 facts; image tables out of scope |
| WeChat `r_aAjFHTRDBGXhl3bd06XQ` | SuperBench comprehensive evaluation report, 8688 chars | 已接受 2026-10-24; facts quote-verified; charts not read |
| WeChat `6FdfPNi9gOf_QQbwlkAdBQ` | ChatSQL overview; body resumed to text-complete | Candidate; score source claims about architecture, failure modes and suitability, not correctness of its advice |
| WeChat `IJvzjWMLMhljkMQO2oa2bg` | Enterprise AI Agent implementation article; body text returned | Candidate; image coverage partial, keep tasks text-scoped |
| WeChat `dAnNHayrE49FEl8TcLII2Q` | Function Calling, MCP and Skills comparison; body resumed to text-complete | Candidate; author describes their framework perspective, treat as attributed opinion |
| WeChat `0jB157hXY2AwIcji0jywBQ` | Agent framework selection article; body resumed to text-complete | Candidate with historical scope: article dated 2024-11-05, do not treat framework claims as current product status |
| Douyin `7691207590809799974` | Model hallucination/alignment chapter summaries; page labels content AI-generated | Partial candidate; page evidence only, no transcript or frames |
| Douyin `/shipin/7260895215337359401` | Primary title/caption and creator/date returned | Metadata-scoped candidate only; related-video transcripts are not evidence for this post |
| Douyin `/shipin/7449917198811940873` | Short-video chapter-editing tutorial, dated 2023-02-17 | Partial candidate; historical UI guidance, exclude related videos |
| Bilibili `BV1WS4y1g742` | Computer-network course, 0-45s Chinese ASR | Candidate only; opening is mostly class logistics and ASR is noisy, likely too weak for a substantive task |
| WeChat `Z3355OEqzJBQvqXONxgO0g` | AIGC RAG survey article; body returned through early sections | Partial candidate; scope to returned text, not unseen continuation or images |
| WeChat `Eg33URH4HhXcP5FkGp1Tsw` | Enterprise Agent/MCP gateway governance article; body returned through early sections | Partial candidate; attribute claims and exclude unseen continuation/images |

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

## Routing Decision (On-Demand Use)

Envoy is only worth its cost when the task needs a link's content and a plain
fetch cannot deliver it. Every source slot therefore carries a routing label,
derived mechanically from the stratum and source class rather than assigned by
hand:

- `envoy_required`: video or audio stratum, image post, long article, or a
  platform page whose body needs scrolling, cookies, or login.
- `direct_sufficient`: the task quotes or restates the material, or the source
  is a single static page whose body a plain fetch already returns.
- `either`: a short static page where both paths can succeed.

Each arm records `first_tool` (`envoy_read`, `web_fetch`, `none`), total tool
calls, wall clock, and whether the answer's facts were source-supported. The
routing score is the share of tasks where the recorded choice matches the
label, reported per label so that overuse on `direct_sufficient` and abstention
on `envoy_required` stay visible. A run that sends every link through Envoy and
a run that opens none can both score below a run that routes; neither extreme
may be reported as a reading improvement.

The 2026-10-24 Xiaohongshu probes returned platform blocks from both the direct
egress and the local proxy, so those slots are `envoy_required` for routing but
are scored on access honesty rather than content recall: the correct outcome is
a disclosed `blocked`, not a reconstructed note.

## Start Gate

1. Complete and independently review all 50 source rows and package/task facts.
2. Save the immutable package manifest; record its SHA-256 and rubric version.
   `freeze_manifest.py --check` is the gate: it encodes the set-size, unique-URL,
   acceptance, revision-hash, snapshot, verbatim-quote, authority-anchor, turn,
   and reference-fact rules, so a manifest hash cannot be claimed for a set
   nobody validated. `--write` refuses while any
   blocker remains; `--rehearsal` relaxes set size for the 2-package run and
   marks that output rehearsal-only. Because only accepted packages can be
   rehearsed before the whole set is ready, `--only <package_id,...>` (refused
   without `--rehearsal`) filters the rows and drafts to that subset before
   validation, so the printed hash covers exactly the packages in the file.
   The current rehearsal artifacts are `manifest-rehearsal.json` (7 packages:
   5 WeChat + 2 Bilibili, 81 facts, sha256 `f37619719860f9f4...79ef23`) and
   `plan-rehearsal.json` (run `envoy-ab-20261024-58dcd0c7abfd`, 21 tasks,
   14 unique sources, two strata, cache isolation still `unknown` until the
   requests run). Both are marked rehearsal-only and prove the pipeline, not
   the product claim.
3. Confirm the pinned B build and the server-reported model/gateway policy.
4. Create the randomized plan with cache status `per_request_salt`; the
   completed server-ledger validator promotes it to
   `verified_per_request_salt` only if all request evidence passes.
5. Run a separate 2-package instrumentation rehearsal; discard its outputs and
   request IDs from the confirmatory batch.
6. Begin the 25-package batch only when correlation metadata and server-ledger
   export are proven end to end. Every formal server request must have a unique
   salt hash and zero cache-read tokens.

The three server calibration requests from October 24 are isolation
calibration only. They do not belong to the rehearsal or evaluation dataset.
