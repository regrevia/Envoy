# Platform Video Probes

Date: 2026-10-20 Asia/Shanghai
Runtime: `yt-dlp 2026.8.19`, Python 3.11 standalone environment
Policy: no cookies, no browser profiles, no certificate bypass, no anti-bot bypass

## Results

| Platform | Public URL | Result | Boundary |
| --- | --- | --- | --- |
| YouTube | `https://www.youtube.com/watch?v=iG9CE55wbtY` | accepted | 427 public English subtitle cues; Envoy read returned bounded transcript with `video_time` locators; retained-material search returned complete cue context |
| Bilibili | `https://www.bilibili.com/video/BV1xx411c7mD` | metadata accepted | 2055.637-second public video metadata resolved without captions; selected ASR interval produced no reliable speech and returned `no_speech` |
| Vimeo | `https://vimeo.com/244405542` | blocked | TLS EOF during extractor page request; yt-dlp also reported no available impersonation target |
| Dailymotion | `https://www.dailymotion.com/video/x75iqs7` | blocked | GraphQL metadata request failed certificate hostname verification |
| Wikimedia direct OGV | `https://upload.wikimedia.org/wikipedia/commons/8/8e/Tear_down_this_wall.ogv` | accepted | 94,058,123-byte audio download; bounded 0-60 second ASR returned 8 English time cues |
| MDN direct MP4 | `https://developer.mozilla.org/shared-assets/videos/flower.mp4` | accepted | 1,128,375-byte download; FFprobe duration 5.055 seconds; one 3.0-second frame delivered to the host |

## Reproduction

Use the installed standalone runtime:

```bash
python scripts/probe_platform_urls.py \
  --timeout-seconds 20 \
  --url 'https://vimeo.com/244405542' \
  --url 'https://www.dailymotion.com/video/x75iqs7' \
  --url 'https://upload.wikimedia.org/wikipedia/commons/8/8e/Tear_down_this_wall.ogv'
```

The command returns `envoy.platform-video-probe.v1` JSON and continues after
individual failures. The Vimeo and Dailymotion failures must remain explicit
`network_error` or access-boundary results. Do not turn off TLS verification,
import browser cookies, or use impersonation/bot-evasion dependencies as an
implicit fallback.
