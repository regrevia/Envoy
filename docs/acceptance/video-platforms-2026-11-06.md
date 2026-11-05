# Video platform readability and network fallback — 2026-11-06

Date: 2026-11-06 Asia/Shanghai
Runtime: repo checkout `main` at release `0.1.45` plus the uncommitted video/media
patches described in this directory (deferred video fallback, proxy retry on
every media fetch, `needs_user_action` for missing capabilities).
Machine: macOS, headless Chromium present, **no Whisper installed** (`[asr]`
extra absent), system proxy `http://127.0.0.1:7897` reachable.

This page is the answer to one question: for each video platform, what does a
reader actually get — timed transcript, or just the page — and what happens when
the direct network path is blocked. Nothing below is inferred from unit tests;
every row is a real `envoy read` call recorded under `artifacts/video-platforms-2026-11-06/raw/`.

## Verdict rules used here

- **Accepted transcript** — response contains `video`/`audio` modality with timed
  segments (`[mm:ss]` in the text). Page metadata alone does not count.
- **Page-only** — page text returned, no timed transcript; `errors[]` carries
  `missing_capability`. Usable material, not a video read.
- **Login wall** — after overlay dismissal the page still has no content;
  `auth_required` with a `needs_user_action` sign-in instruction. This is the
  only case where the user is asked for a QR/visible sign-in.
- A dismissible overlay never counts as a wall.

## Real-site results

All five calls used `scope {start_seconds: 0, end_seconds: 30}`,
`max_work_seconds: 120`, `max_download_bytes: 40 MB`, and the default direct-first
network plan (proxy allowed only as a fallback).

| Site | Sample | Result | Wall time | Content actually returned | Verdict |
| --- | --- | --- | --- | --- | --- |
| YouTube | `watch?v=iG9CE55wbtY` | `ready` | 15.9 s | `text` + `video`, 2 timed blocks, 120 chars, `[00:27.103] Good morning. How are you?` / `[00:29.702] (Audience) Good.`, `transcript_source=manual_subtitles` | **Transcript accepted** for the requested 0–30 s range |
| Bilibili | `video/BV1zj411K7zL` | `ready` | 1.3 s cached (first media read in this session was the slow one) | `text` only, 287 chars of page metadata (title, UP, stats, description); `missing_capability: No transcript is available for this video` | **Page-only** on this machine; the same sample produced 9 ASR segments for 0–30 s on 2026-10-21 where Whisper was installed |
| Vimeo | `vimeo.com/76979871` | `ready` | 18.4 s | `text` only, 766 chars of page description; `missing_capability` | **Page-only**; direct path to Vimeo times out, page arrived through the automatic proxy fallback |
| Dailymotion | `dailymotion.com/video/x8pnwbb` | `blocked` (exit 2) | 29.1 s | no blocks; `missing_capability: Public subtitle extraction failed; Local ASR is disabled …` | **Not readable** here: no public subtitle track, and the ASR arm is unavailable because this machine has no Whisper |
| Instagram | `instagram.com/p/Cz0h1Z1rG4K/` | `blocked` (exit 2) | 43.4 s | no blocks; `auth_required` + `needs_user_action` sign-in instruction | **Real login wall** — correct escalation, no bypass attempted |

Reading rules that follow from the table:

- YouTube with a manual subtitle track is the only platform here that gives a
  transcript without local ASR; it needs a bounded range, otherwise the bot wall
  returns `blocked/empty_content`.
- Bilibili and Dailymotion expose no public subtitle for these samples, so the
  quality of the read is exactly the quality of the local ASR install. On a
  machine without Whisper the honest answer is "page text only", and the error
  now says how to fix it: `pip install 'envoy-cli[asr]'` plus
  `envoy asr install --model base`.
- Vimeo is readable as a page over a working network path; the page carries the
  video description, not the speech.
- Instagram stays gated; the response asks the user to sign in instead of
  pretending it read the post.

## Network: direct first, trusted proxy as the automatic fallback

`packages/envoy-cli/src/envoy_cli/netpolicy.py` derives a *plan*, never a silent
proxy default:

- `ENVOY_BROWSER_PROXY` unset → `[None, detected]` when a system proxy is
  detected, else `[None]`.
- `ENVOY_BROWSER_PROXY=off|none|direct|false|0` → `[None]` (pin direct).
- `ENVOY_BROWSER_PROXY=http://host:port` → `[that proxy]` (pin proxy).

Every media/HTML fetch uses that plan: `reader.py` `_fetch_html`, `_fetch_source`,
`_read_pdf_source`, `_download_image_for_ocr`, `browser.py`, `collections.py`,
`media.py`. A direct connect failure is retried through the detected proxy in the
same call; a 4xx/5xx from the origin is not retried through the proxy.

TLS-level evidence for the same five hosts (`artifacts/video-platforms-2026-11-06/raw/tls-path-probe-2026-11-06.log`,
12 s connect timeout, `trust_env=False`, no certificate relaxation):

| Host | Direct | Through `127.0.0.1:7897` |
| --- | --- | --- |
| youtube.com | `ConnectTimeout` | 200, 1,481,123 B |
| bilibili.com | 200, 337,025 B | 200, 330,116 B |
| vimeo.com | `ConnectTimeout` | 200, 13,413 B |
| dailymotion.com | `ConnectTimeout` | 200, 197,347 B |
| instagram.com | `ConnectTimeout` | 200, 504,505 B |

Causality check at the Envoy level, same Vimeo URL, same machine:

- default plan → `ready` in 18.4 s (page text through the fallback);
- `ENVOY_BROWSER_PROXY=off` → `blocked` in 31.7 s (direct-only cannot reach the
  host). The difference is the fallback, not a code path.

Certificate verification is never disabled on either branch; the proxy is a
transport override only. A pinned proxy that is itself unreachable ends as
`NETWORK_ERROR`, not as a silent direct retry.

## What is not covered

- **ASR was not executed today.** `openai-whisper` is not installed in this
  checkout's venv, so the ASR arm of Bilibili/Dailymotion could only be observed
  as `missing_capability`. Timed Bilibili segments quoted above are from the
  2026-10-21 run recorded in `platform-coverage-matrix-2026-10-21.md`.
- **Instagram content was not read.** The sign-in wall is reported, not bypassed.
- **Dailymotion media download was not attempted** because subtitle extraction
  failed before a usable public stream was found.
- YouTube was read with a subtitle track; auto-generated captions and the
  `Sign in to confirm you're not a bot` variant remain a separate case and are
  still `blocked` without a range.

## Reproduce

```sh
cd <repo>
export ENVOY_ASR_MODEL_CACHE=/tmp/envoy-asr-cache   # this machine's ~/.cache/whisper
                                                    # is a symlink to a blocked external disk
env envoy read --request-json /tmp/bili-req.json    # request shape below
```

```json
{"request_id":"acc-bili","source":{"url":"https://www.bilibili.com/video/BV1zj411K7zL/"},
 "budget":{"max_download_bytes":40000000,"max_work_seconds":120},
 "scope":{"start_seconds":0,"end_seconds":30}}
```

The `/tmp/bili-req.json` shape is what the acceptance harness submits; the read
takes ~19 s here and writes the full JSON response.
