# Platform Video Probes

Date: 2026-10-20 Asia/Shanghai
Runtime: `yt-dlp 2026.8.19`, Python 3.11 standalone environment
Policy: no cookies, no browser profiles, no certificate bypass, no anti-bot bypass

## Results

| Platform | Public URL | Result | Boundary |
| --- | --- | --- | --- |
| Vimeo | `https://vimeo.com/244405542` | blocked | TLS EOF during extractor page request; yt-dlp also reported no available impersonation target |
| Dailymotion | `https://www.dailymotion.com/video/x75iqs7` | blocked | GraphQL metadata request failed certificate hostname verification |
| Wikimedia direct OGV | `https://upload.wikimedia.org/wikipedia/commons/8/8e/Tear_down_this_wall.ogv` | accepted | 94,058,123-byte audio download; bounded 0-60 second ASR returned 8 English time cues |
| MDN direct MP4 | `https://developer.mozilla.org/shared-assets/videos/flower.mp4` | accepted | 1,128,375-byte download; FFprobe duration 5.055 seconds; one 3.0-second frame delivered to the host |

## Reproduction

Use the installed standalone runtime:

```bash
yt-dlp --ignore-config --no-playlist --skip-download \
  --print 'id=%(id)s\ntitle=%(title)s\nduration=%(duration)s' '<url>'
```

The Vimeo and Dailymotion failures must remain explicit `network_error` or
access-boundary results. Do not turn off TLS verification, import browser
cookies, or use impersonation/bot-evasion dependencies as an implicit fallback.
