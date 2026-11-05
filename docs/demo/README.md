# 60 秒演示（`envoy-demo-60s.gif`）

一条真实链接的完整链路：粘贴 YouTube 链接 → 请求 0–30 秒 → 返回带 `[mm:ss]` 的字幕块 → 没读到的范围显式列为待覆盖。

| 文件 | 说明 |
| --- | --- |
| `envoy-demo-60s.gif` | 演示 GIF，1280×720，27.750 s，1,147,318 B（1.09 MiB） |
| `envoy-demo-60s.mp4` | 同一帧序列的 H.264 版本（无声、20 fps），1,325,173 B |
| `captured-read.json` | 本次运行真实的 CLI 标准输出，GIF 里出现的每个数字都来自这里 |
| `capture-meta.txt` | 那次运行的元数据：命令、退出码、耗时、stdout/stderr 字节数与关键字段 |
| `render_frames.py` | 把 `captured-read.json` 渲染成终端回放帧的脚本（生成本 GIF 用的就是它） |

## 环境

| 项 | 值 |
| --- | --- |
| 系统 | macOS 27.3 / arm64 |
| Python | 3.11.15（仓库内 `packages/envoy-cli/.venv`） |
| Envoy | `envoy-cli 0.1.45 (core 0.1.45)` |
| ffmpeg | 9.0.2（`/opt/homebrew/bin/ffmpeg`，含 `ffprobe`） |
| Pillow | 13.0.0（venv 内，仅用于渲染帧） |
| 字体 | Menlo（ASCII）+ Hiragino Sans GB（中文），系统自带 |
| 录制日 | 2026-11-06 |

本机没有 `asciinema` / `agg`，因此没有用终端录像回放：改为抓取 CLI 的真实标准输出，再用 `render_frames.py` 逐帧渲染终端回放（见下文「诚实性说明」）。

## 复现命令

### 1. 跑真实读取，抓标准输出

在仓库根目录：

```sh
cat > /tmp/demo-request.json <<'JSON'
{"request_id":"demo-1","source":{"url":"https://www.youtube.com/watch?v=iG9CE55wbtY"},"budget":{"max_download_bytes":40000000,"max_work_seconds":600},"scope":{"start_seconds":0,"end_seconds":30}}
JSON

packages/envoy-cli/.venv/bin/envoy read --request-json - \
  < /tmp/demo-request.json > /tmp/envoy-demo-read.json
```

本机实测 2.2 s，`result: "ready"`，`material_id: mat-0f0f4328405a2e69fe5a7ef9`。
直连 YouTube 不通时 Envoy 会自动走系统代理后重试，耗时取决于代理。

### 2. 渲染帧

```sh
DEMO_INPUT=/tmp/envoy-demo-read.json \
DEMO_FRAMES=/tmp/demo-frames \
DEMO_FPS=20 \
packages/envoy-cli/.venv/bin/python docs/demo/render_frames.py
```

输出 555 张 1280×720 PNG（27.75 s × 20 fps）。

### 3. 编码 mp4 与 GIF

```sh
ffmpeg -y -framerate 20 -i /tmp/demo-frames/%05d.png \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p -movflags +faststart \
  docs/demo/envoy-demo-60s.mp4

ffmpeg -y -framerate 20 -i /tmp/demo-frames/%05d.png \
  -vf "fps=12,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  docs/demo/envoy-demo-60s.gif
```

### 4. 核对

```sh
wc -c docs/demo/envoy-demo-60s.gif docs/demo/envoy-demo-60s.mp4
ffprobe -v error -show_entries format=duration -of default=nw=1 docs/demo/envoy-demo-60s.gif
ffprobe -v error -show_entries stream=width,height,nb_frames,avg_frame_rate -of default=nw=1 docs/demo/envoy-demo-60s.gif
```

实测：GIF `duration=27.750000`、`1280×720`、`nb_frames=333`、`avg_frame_rate=25/2`；体积 1,147,318 B，满足 ≤60 s、宽 1200–1400、<12 MB。

## 画面里出现的数字，来源一览

| GIF 中的内容 | 来源字段（`captured-read.json`） |
| --- | --- |
| 20:03 视频全长 | `coverage.modalities.video.details.duration_seconds = 1203` |
| 请求范围 00:00–00:30 | `coverage.modalities.video.details.requested_range` |
| `[00:27.103] Good morning. How are you?` 等两条 | `blocks[kind=transcript].content.text` |
| 片段定位 27.103–31.105 | 同一 block 的 `locator.start_seconds` / `end_seconds` |
| `extraction: complete` / `partial` | `coverage.modalities.text` / `.video` |
| 画面未抽帧 | `coverage.unresolved[0]` |
| 未覆盖范围 | `coverage.unresolved[1]`（`Transcript coverage is limited to the requested range 00:00.000-00:30.000; other audio ranges were not read`） |
| 完成耗时 2.2 s | 本次运行的 `time` 实测 |

## 诚实性说明

- GIF 不是示意图：`captured-read.json` 是 2026-11-06 真实跑 `envoy read` 抓到的完整标准输出，`render_frames.py` 只负责排版、逐行揭示和高亮；画面里的 URL、`material_id`、时间戳、coverage 文案都直接取自这份输出，没有手写改写。
- 时间轴是演示节奏（打字、逐行揭示、停顿约 27.75 s），不代表真实耗时；真实读取耗时为 2.2 s，画面里单独标注。
- 为了让内容在一屏内读完，JSON 以两段节选呈现（`blocks` 与 `coverage` 的关键字段），省略处标了 `…`；完整原文见同目录 `captured-read.json`。
- 未出现 token、邮箱、API key；命令里只出现仓库相对路径，不含本机用户名。
- 未覆盖部分按 Envoy 自己的说法展示：`coverage.unresolved` 原文 + 由 `requested_range`、`duration_seconds` 推算的 00:30–20:03；没有虚构“Envoy 说未覆盖了 X”的字段。
