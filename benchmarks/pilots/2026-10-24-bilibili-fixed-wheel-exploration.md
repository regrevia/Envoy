# Bilibili Fixed-Wheel Direct Exploration

Date: 2026-10-24 Asia/Shanghai  
Status: exploratory platform probe; excluded from paired product statistics.

## Runtime Identity

- Envoy source commit embedded in the wheel: `c29579f9ad2614099c217c777c4c7f43a4508aea`
- Wheel SHA-256: `473719e7eaffc61398721215262efcf660b200bfefd4e759e5b6a23c462f86c4`
- Python: 3.11.15
- Whisper: `openai-whisper 20250625`, selected model `base`
- PyTorch: 2.14.1
- FFmpeg/FFprobe: 9.0.2
- Network: direct reads; proxy environment variables were removed and browser proxy was pointed at a local non-listening address to prevent accidental external proxy use.

The wheel code was imported ahead of the existing isolated Envoy tool environment
to reuse its installed Whisper/Torch stack. This was not a clean dependency-lock
installation of the wheel, so it is an exploratory runtime check rather than
release-runtime acceptance. OS proxy discovery was explicitly disabled inside
the Envoy process for the direct-confirmed reads, and Chromium was pointed at a
local non-listening proxy endpoint so it could not silently use a system proxy.
No browser cookies or account credentials were used.

## Results

| Source | Request scope | Result | ASR evidence | Coverage |
| --- | --- | --- | --- | --- |
| `https://www.bilibili.com/video/BV16bNveBEqw` | 00:00-01:00 | `ready`; 30 timestamped ASR segments; 738 transcript characters | Whisper `base`, Chinese detected | Requested first-minute transcript returned; no frame inspection |
| `https://www.bilibili.com/video/BV1ARzdBhEsU` | 00:00-00:30 | `ready`; 9 timestamped ASR segments | Whisper `base`, English detected | Requested range returned; no frame inspection |
| `https://www.bilibili.com/video/BV1xVXCYBEMo` | 00:00-00:30 | `ready`; 10 timestamped ASR segments | Whisper `base`, Chinese/English mixed audio | Requested range returned; no frame inspection |
| `https://www.bilibili.com/video/BV1xsgc63EPb` | 00:00-00:30 | `ready`; 5 timestamped ASR segments | Whisper `base`, Chinese detected | Requested range returned; no frame inspection |
| `https://www.bilibili.com/video/BV1xx411c7mD` | 00:00-00:30 | `blocked / network_error`; no audio file produced after subtitle extraction reported `no_subtitles` | No transcript | Failure retained; not counted as readable content |
| `https://www.bilibili.com/video/BV1qhhPziETH` | 00:00-00:30 | `blocked / budget_exceeded`; media download exceeded 30 MB cap | No transcript | Failure retained; not counted as readable content |

Page revision digests returned for the four direct-confirmed readable samples:

- `BV16bNveBEqw`: `a08ba788c1546c2ae48bed006a1811115efcb13a65036761c83505db6e297f1a`
- `BV1ARzdBhEsU`: `7a135c6c5cdb3d3aed230be60c5fb00dde4a680417608624b6ecfe2552edcbd4`
- `BV1xVXCYBEMo`: `f94bb18a31d3506b1dc83c1ffff9afd527a115be4e5221ebf1df9dbfc08621f1`
- `BV1xsgc63EPb`: `84507b4d72a83db7aad8e725cf9a62628e449c20be1a9a1eea2d895c9eb6581a`

Two earlier probes (`BV1zj411K7zL` and `BV192Ffe1EwJ`) were run with proxy
environment variables removed but before disabling macOS system proxy
discovery. They are excluded from the direct-acceptance count until repeated
with verified direct networking.

These digests identify the fetched page revisions; they are not hashes of the
video media or a human-verified transcript. No independent reference facts,
citation adjudication, visual inspection, or paired baseline was collected.
Whisper `base` output has not been accuracy-scored.

## Experiment Gate

This probe establishes only that the fixed-wheel code path can return bounded
Bilibili ASR material for four direct-connected samples in this local
environment. It does not count as a task package or as part of the
20-source/40-task paired experiment.

Formal A/B remains blocked on:

- Source content and reference facts being frozen with independently retained snapshots.
- A verified cache-isolation method. The current LiteLLM-to-vLLM route still
  returns nonzero upstream prefix-cache reads despite `cache: false` at LiteLLM
  and attempts to set `CLAUDE_CODE_EXTRA_BODY.cache_salt`.
- Authoritative server usage export covering each run's complete request window.

On Claude Code CLI 2.1.197, a three-request local diagnostic using a repeated
long prompt and `CLAUDE_CODE_EXTRA_BODY.cache_salt` surfaced
`modelUsage.cacheReadInputTokens` of 0 / 5,632 / 5,888 for A1 / A2 / B. This
client-visible diagnostic is not a substitute for authoritative server logs,
but it confirms that the extra-body salt did not establish arm isolation.
No formal package turns were launched.

The separate LiteLLM host did not accept the available SSH public key. No
gateway configuration was changed or restarted. A gateway operator must
temporarily apply and validate a per-package/per-arm upstream cache namespace
mapping (or disable upstream prefix caching for this experiment route) before
strict paired execution can proceed.

Do not calculate or claim token savings from this exploration.
