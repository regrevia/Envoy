# Agent Product Evaluation Source Candidates

Date: 2026-10-23 Asia/Shanghai
Purpose: candidate inventory for the versioned 20-source/40-task host-agent evaluation.

These candidates do not yet count toward the release evaluation. The current
probe checks public extractor metadata only, while older records belong to
previous runtime versions. Every candidate must be re-read with the evaluation
runtime and complete an initial question plus a follow-up before it counts.
Metadata acceptance does not establish transcript, OCR, answer, citation, or
full-media acceptance.

| Candidate | Source class | Latest probe state | Probe evidence | Evaluation status |
| --- | --- | --- | --- | --- |
| `https://www.youtube.com/watch?v=iG9CE55wbtY` | YouTube video | `auth_required` | Extractor asks for sign-in verification | Candidate only; do not import cookies or bypass |
| `https://www.bilibili.com/video/BV1zj411K7zL/` | Bilibili video | `metadata` | Title/duration resolved; no subtitle tracks | Candidate only; needs current ASR task |
| `https://www.bilibili.com/video/BV1xx411c7mD` | Bilibili video | `metadata` | Title/duration resolved; no subtitle tracks | Candidate only; historical no-speech boundary |
| `https://vimeo.com/244405542` | Vimeo video | `network_error` | TLS EOF during webpage extraction | Candidate only; ask user whether their trusted proxy is needed if they can open it |
| `https://www.dailymotion.com/video/x75iqs7` | Dailymotion video | `network_error` | TLS EOF during metadata request | Candidate only; ask user whether their trusted proxy is needed if they can open it |
| `https://upload.wikimedia.org/wikipedia/commons/8/8e/Tear_down_this_wall.ogv` | Direct OGV video | `metadata` | Generic extractor resolves title; duration absent | Candidate only; historical bounded ASR must be repeated |
| `https://developer.mozilla.org/shared-assets/videos/flower.mp4` | Direct MP4 video | `metadata` | Generic extractor resolves title; duration absent | Candidate only; historical frame extraction must be repeated |

The probe used the current checkout's declared yt-dlp inside the isolated uv
environment, with no cookies, no browser profile, no TLS bypass, and no public
proxy. `accepted` here means metadata extraction only. A full evaluation record
must separately include answer/citation review, timed/page/image locators,
paired baseline usage from the same host model, first-evidence and total
latency, visual inputs, downloads, and material reuse.

The repository currently has fewer than 20 non-placeholder live source
candidates. Do not fill the remaining slots with synthetic fixtures, multiple
URLs to the same media object, or historical test hosts. Continue collecting
public user-approved samples and keep blocked sources visible in the report.
