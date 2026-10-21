# Platform Coverage Matrix

Date: 2026-10-21 Asia/Shanghai

Envoy's video path is extractor-first, then isolated Chromium. Chromium stays
headless while it dismisses a clearly identified login dialog and reads the
underlying public page. The same dismissal check runs after scroll/lazy-load
steps and inside a bounded set of page frames. A remaining login wall or
security challenge follows the normal user-action path.

This matrix separates code-path coverage from real-site acceptance. A passing
extractor metadata probe does not prove captions, media download, ASR, or frames
are available. Browser fixture tests do not count as live-platform acceptance.

| Platform or source | Caption/metadata path | Browser fallback | Current evidence | Status and boundary |
| --- | --- | --- | --- | --- |
| YouTube | yt-dlp public subtitles, then bounded local ASR | Headless page and dismissible overlay handling | Earlier real public subtitle acceptance on 2026-10-20; latest sample displays “Sign in to confirm you're not a bot” | Public page is readable, but transcript/media remain challenge-gated; no cookie import or challenge bypass |
| Bilibili | yt-dlp public subtitles, then bounded local ASR | Headless page and dismissible overlay handling | Real no-caption sample on 2026-10-21 returned 9 English ASR segments for 00:00-00:30 | Accepted for bounded transcript range; remaining duration is intentionally uncovered |
| Douyin | yt-dlp when public extraction is available; otherwise browser-captured media and local evidence pipeline | Short-link redirects, lazy-load rechecks, centered login-card dismissal, media-response capture | Final installed preview read dismissed the real QR/login card headlessly, preserved 21 chapter blocks, downloaded the primary 100.733-second stream, produced 51 timed Chinese ASR segments, and delivered two frames | Accepted for this sample; `needs_host` for frame inspection and partial for sparse visual coverage |
| Instagram | yt-dlp when the public item is available | Headless page, dismissible login dialog, then user-action escalation if still gated | 2026-10-21 probe hit TLS EOF | Unverified; private/login-gated items remain unsupported |
| Vimeo | yt-dlp where public extraction is available | Headless page and normal user-action boundary | 2026-10-20 and 2026-10-21 probes failed on TLS EOF | Not accepted; do not disable certificate checks |
| Dailymotion | yt-dlp where public extraction is available | Headless page and normal user-action boundary | 2026-10-20 hostname verification failure; 2026-10-21 TLS EOF | Not accepted; do not bypass TLS verification |
| Direct MP4/WebM/OGV | Bounded download, local ASR, optional frame extraction | Browser media capture for embedded players | MDN MP4 accepted on 2026-10-20 and re-read through current checkout source on 2026-10-21; Wikimedia OGV accepted on 2026-10-20 | Accepted for those public samples |
| HLS/DASH player media | yt-dlp or captured public manifest when safely usable | Capture manifest/media responses after dismissing overlays | No complete live acceptance recorded | Partial/unverified; signed/session-only URLs stay local and may still fail without authorized session access |
| Public articles and WeChat articles | Static HTML first | Headless rendering, dismissible login dialogs, then user-action escalation | Existing browser regression coverage; live WeChat challenge remains unaccepted | Partial; normal verification must not be bypassed |
| Public PDF | Existing bounded PDF reader | Browser fallback only when the URL serves a readable page rather than a PDF response | Existing PDF test coverage | Supported by document path, independent of video browser behavior |

## Video Popup Behavior

Many video pages show a login prompt over otherwise public playback. Envoy now
tries to close a small, visible control only when its containing overlay has
login-related text or identity markers. It repeats this after scrolling and
lazy loading, and checks child frames as well as the top-level page. It does
not click login, authorization, CAPTCHA, or security-verification controls.

After dismissal, page text and video/media requests are captured again. If
content remains gated, Envoy may show its isolated browser for the user to
complete a normal action. It never imports personal browser cookies or
automates verification. An overlay being closed is not proof that a video was
transcribed or visually inspected; coverage remains partial until video-level
evidence is actually returned.

## Next Acceptance Order

1. Repeat real YouTube and Bilibili subtitle/ASR acceptance after browser changes.
2. Run Douyin with a public sample that exhibits the dismissible popup; record
   whether closing it exposes timed captions, a locally usable media stream, or
   page-only chapter metadata.
3. Probe Instagram, Vimeo, and Dailymotion separately, distinguishing extractor
   metadata from browser-rendered content and video-level evidence.
4. Add at least one repeatable fixture and one real public sample per platform
   before calling that platform accepted.
5. Keep WeChat articles and direct PDF/CDN links as separate source classes;
   do not let video-specific fallback changes weaken their access controls.

## 2026-10-21 Extractor Probe

The no-cookie probe used yt-dlp 2026.8.19, a 20-second per-URL timeout, and
the existing samples listed in `platform-video-probes-2026-10-20.md`.
YouTube timed out; Bilibili, Instagram, Vimeo, and Dailymotion returned TLS
EOF/network errors; Douyin requested fresh cookies. These results do not
establish that the sites are generally unavailable: this probe did not exercise
Chromium's dismissible-popup fallback, and transport errors are not extractor
compatibility results. The Douyin response is an explicit access boundary for
the extractor path; Envoy may still read content exposed publicly by its
headless browser after a dismissible overlay is closed.

## 2026-10-21 Real Envoy Read Acceptance

| Test | Runtime | Result |
| --- | --- | --- |
| `https://example.org/` through connected MCP | Installed `envoy-cli 0.1.0`; Chromium ready; Whisper absent | `ready`; confirms the MCP can reach a simple public control page |
| Douyin video through connected MCP | Same installed runtime | `blocked / auth_required`; yt-dlp requested fresh cookies and browser fallback ended with `TimeoutError` |
| YouTube sample through connected MCP | Same installed runtime | `blocked / missing_capability`; no public subtitles and the installed runtime has no local ASR |
| Bilibili sample through connected MCP | Same installed runtime | `blocked / missing_capability`; no public subtitles and the installed runtime has no local ASR |
| Douyin, YouTube, and Bilibili in fresh headless Chromium using current checkout source | Current source, temporary empty browser profiles, no cookies | Douyin and Bilibili navigation exceeded 12 seconds before `domcontentloaded`; YouTube returned `net::ERR_CONNECTION_CLOSED`. No page overlay could be inspected because usable page content did not load |
| MDN flower MP4 through connected MCP | Same installed runtime | `partial`; media worker exceeded its remaining 23-second work budget; no frames returned |
| MDN flower MP4 through current checkout `envoy read`, requesting two host-inspectable frames | Current checkout source, same machine, 30-second work budget, 5 MB download cap, FFmpeg available, Whisper absent | `needs_host`; downloaded 1,128,375 bytes and delivered two timestamped frames at 1.264s and 3.791s. Frame pixels were visually checked: a closed bud and the opened flower. ASR was unavailable and explicitly reported as a coverage gap |

The connected MCP process uses the installed CLI under
`/Users/apple/.local/share/uv/tools/envoy-cli`, not the current checkout. The
current source succeeds on the known direct-MP4 frame path while the installed
MCP process times out on the same URL; refresh/reinstall that MCP runtime
before treating its video result as representative of the current source.

At the initial probe, platform pages failed before becoming interactive. The
later preview acceptance below supersedes the Douyin status for this sample.
YouTube remains challenge-gated, and Bilibili's full-video attempt exceeded
the initial 12 MB media budget; a bounded 30-second ASR range succeeded.

## 2026-10-21 Installed Local Preview

The repository was built as
`0.1.0+preview.g814f460c3e2a.20261021040910` and installed in the isolated
`/Users/apple/.local/share/envoy-preview` virtual environment. Its doctor and
MCP doctor both identify `channel: local-preview`, revision `814f460c3e2a`,
and a dirty source tree. The existing Release command and environment remain
separate. The user's global Envoy Skill was synchronized from this checkout,
and Codex configuration now has a separate `envoy-preview` MCP entry.

| Installed preview test | Result |
| --- | --- |
| Chromium | `ready`; installed via `envoy browser setup` |
| `https://example.org/` through preview CLI | `ready`; public title and body returned |
| `https://developer.mozilla.org/shared-assets/videos/flower.mp4` through preview CLI | `partial`; the current media request failed with TLS EOF before frames could be extracted |
| Douyin sample through preview CLI, before proxy and overlay fixes | `blocked / auth_required`; extractor requested fresh cookies and browser navigation exceeded its budget |
| Whisper ASR at this checkpoint | Initially absent; subsequently installed through the local proxy and prepared from the verified existing `base.pt` cache |

The source-checkout MDN MP4 acceptance earlier in this document remains valid
for that run. Its earlier installed-preview TLS EOF was specific to the network
attempt and is not the latest state. The proxy-enabled preview successfully
read the Douyin page and primary media as recorded below.

## 2026-10-21 Proxy-Enabled Preview Acceptance

The system HTTP/HTTPS proxy at `127.0.0.1:7897` was verified against PyPI and
PyTorch's wheel source. Envoy now reads the system proxy for Chromium and yt-dlp;
`ENVOY_BROWSER_PROXY` can explicitly override it. URLs with proxy credentials
are deliberately not passed to Chromium command-line arguments. Whisper
`20250625`, Torch, Numba, and dependencies installed in the dedicated preview
environment. `envoy asr setup --model base` verified and reused the existing
SHA-256-checked model at `~/.cache/whisper/base.pt`; no second model download
was needed.

| Test | Result |
| --- | --- |
| Bilibili `BV1zj411K7zL`, first 30 seconds | No public subtitles; bounded local ASR downloaded 6,626,352 bytes and returned 9 English timestamped segments from 00:00 to 00:30 using Whisper `base`; extraction `ready` for the requested transcript range, with other times explicitly uncovered |
| Same Bilibili video, default 12 MB budget | Download was rejected when aggregate media exceeded 12 MB; increasing the explicit test budget and constraining the requested time range succeeded |
| Douyin `7698386448961683813` | yt-dlp still truthfully reports `Fresh cookies are required`; isolated headless Chromium used the system proxy, closed the centered login card's X without opening foreground, and captured page chapters plus the primary video stream |
| Douyin video evidence | Downloaded 11,218,566 bytes; duration 100.733 seconds; Whisper `base` returned 51 timestamped Chinese ASR segments through 100.64 seconds; two frames delivered at 25.183 and 75.55 seconds; both frame images were visually checked |
| YouTube `iG9CE55wbtY` | Chromium can load the public page, but YouTube renders “Sign in to confirm you're not a bot”; yt-dlp also returns that challenge. Envoy reports/escalates the access boundary; it does not import personal cookies or bypass the verification |
| JSON protocol output during local ASR | Whisper's “Detected language” progress output was found on stdout and redirected to stderr; a regression test verifies stdout remains reserved for protocol JSON |

The Douyin result is `needs_host` when frame capability is declared, and
`partial` for complete-video coverage: the model receives page chapter blocks,
timestamped ASR, and a sparse frame sample as separate evidence. Envoy does not
make job-location or company-match conclusions. A successful ASR pass does not
mean the whole video was visually inspected.

## 2026-10-21 Resumability And Final Preview

The final local preview is
`0.1.0+preview.g814f460c3e2a.20261021064912`, with proxy, Chromium, Whisper, and
the verified `base` model ready. The source worktree remains dirty; this is a
local preview, not a repository Release.

| Check | Evidence |
| --- | --- |
| Real installed-preview Douyin read | `needs_host`; 23 page chapter blocks, 51 ASR transcript blocks, 2 frame blocks; 11,218,566 downloaded bytes; media duration 100.733 seconds; ASR reached 100.64 seconds |
| Timestamped ASR content | First segment begins “大家好 今天是10月19日周一”; final segment ends at 100.64 seconds. Text remains ASR evidence, not a verified human transcript |
| Returned frames | 25.18 and 75.55 seconds; checked images show a recruitment-information page and a “专业要求” card |
| Coverage consistency | Final JSON has no obsolete “video page only / transcript not extracted” gaps; sparse frame coverage is still explicitly partial; only unrelated page-image budget gap remains |
| ASR continuation material | Stored material has `source_kind: video`, `transcript_source: asr`, and 51 video spans; `envoy_search` reuses it and reports four matching passages already present without another media download |
| Interrupted model download | Regression simulates a connection break after 9 bytes, resumes with `Range: bytes=9-`, validates the registered SHA-256, and atomically installs the completed file |
| Protocol stdout | End-to-end CLI stdout parses as a single JSON response; Whisper language/progress output stays on stderr |
