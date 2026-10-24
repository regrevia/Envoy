<a id="top"></a>
<p align="center">
  <img src="frontend/public/brand/envoy-bird.png" alt="Envoy" width="104">
</p>

<h1 align="center">把链接分享给你的 AI</h1>

<p align="center">
  一个链接，成为你想要的任何东西。
</p>

<p align="center">
  <a href="integrations/skills/envoy/INSTALL.md">安装 Envoy</a> ·
  <a href="https://github.com/regrevia/Envoy/releases">下载发行版</a> ·
  <a href="https://github.com/regrevia/Envoy/issues/new/choose">反馈问题</a> ·
  <a href="#english">English</a>
</p>

给 AI 一个链接，Envoy 把它变成带来源定位的材料，AI 再用它写你要的东西：Wiki、摘要、报告、脚本。网页、公众号、PDF、小红书图文、B 站视频、本地视频都行。

## 能做什么

**关键帧** — 视频里的 PPT、图表、演示画面按时间点抽帧交给 AI 看，不用来回拖进度条。

**视频理解** — 优先读公开字幕；没有字幕时对指定时间段跑本地 Whisper，返回带 `[mm:ss]` 的转写片段。

**图文** — 网页、公众号文章、PDF、图片帖，变成干净正文、图片和来源。

**知识库 / Wiki** — 长视频整段读完，按主题分节，每条结论带时间戳，直接放进 Obsidian、Notion。

## 怎么用

对 AI 说：

> 把这条视频整理成知识库：`https://www.bilibili.com/video/BV...`
>
> 先读前 10 分钟，按主题分节；每条结论后标注 `[mm:ss]`；没覆盖到的部分单列“待补”，不要猜。

材料到手就已经分好段、带定位，所以产出可以直接引用：

```markdown
## 2. 为什么选择固定轮

- 固定轮把连续动作拆成可复用的阶段，便于逐段调试。[04:18]
- 作者对比了三种方案，并给出取舍条件。[07:02]
- 这一段没有展示实际测试数据；待补。[09:31]
```

## 工作原理

1. **取材料** — 打开链接，拿到标题、正文、字幕或转写、PDF 文本、图片。
2. **带定位** — 切成片段并附 `video_time` 或来源坐标，给一个 material id。
3. **按需续读** — `envoy_search` 定位关键词，`envoy_expand` 补齐没读的部分，不重拉整页。
4. **缺口照实报** — 没字幕、要登录、模态缺失、下载预算不够，标记为未覆盖，不写成结论。

浏览器在后台运行，遇到登录或验证才请你接管。接入方式是 Agent Skill + MCP，Claude Code、Codex、DeepSeek Harness 各有插件（Codex 0.156 起需执行一次 `envoy hook install --host codex`）。

## 边界

不绕过登录、验证码、地区限制和 DRM；不替代联网搜索。Envoy 只对实际读取并返回的时间段负责。抖音主页集合、小红书图文等能力按[平台验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)逐项验证。

## 评测

正在做同链接、同模型的 Envoy / 直接读取对比，记录完成时间、实际 token、事实覆盖、引用质量和人工介入。当前 pilot 为 **6 个来源、12 个任务**，还不足以得出“更快”或“更省 token”的结论。见[评测方法](benchmarks/README.md)与 [pilot 数据](benchmarks/pilots/README.md)。

## 文档

- [安装、更新与 MCP 配置](integrations/skills/envoy/INSTALL.md)
- [Claude Code 插件](integrations/claude-code/envoy/README.md)
- [Codex 插件](integrations/codex/envoy/README.md)
- [DeepSeek Harness 插件](integrations/dsh/README.md)
- [产品与技术状态](docs/architecture/agent-link-reader-status.md)
- [平台真实验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [版本记录](CHANGELOG.md)
- [安全与隐私说明](NOTICE)

<a id="english"></a>

## English

<p align="center"><a href="#top">中文</a></p>

<h3 align="center">Share a link with your AI</h3>

<p align="center">
  One link in. Anything you want out.
</p>

Give your AI a link and Envoy turns it into material with source locators; the AI then writes whatever you asked for: a wiki, a summary, a report, a script. Web pages, WeChat posts, PDFs, Xiaohongshu image posts, Bilibili videos, local video files.

### What it does

**Keyframes** — pull slides, charts, and demo frames at chosen timestamps and hand them to the AI, no scrubbing.

**Video** — captions first; when there are none, run local Whisper over the requested range and return transcript segments with `[mm:ss]`.

**Text and images** — web pages, articles, PDFs, image posts become clean text, images, and their source.

**Knowledge base / wiki** — read a long video end to end, group it by theme, put a timestamp on every claim, and drop the result into Obsidian or Notion.

### How to use it

Ask your AI:

> Turn this video into a knowledge base: `https://www.bilibili.com/video/BV...`
>
> Read the first 10 minutes, group sections by theme, and add `[mm:ss]` after each claim. List anything not covered under "To fill"; do not guess.

The material arrives already segmented and located, so the output can cite directly:

```markdown
## 2. Why use a fixed wheel

- A fixed wheel splits a continuous action into reusable stages for per-stage debugging. [04:18]
- The author compares three approaches and states the trade-offs. [07:02]
- This section shows no measured data; to fill. [09:31]
```

### How it works

1. **Fetch** — open the link and take the title, body, captions or transcript, PDF text, and images.
2. **Locate** — split it into segments carrying `video_time` or source coordinates, and return a material id.
3. **Continue on demand** — `envoy_search` finds a term, `envoy_expand` fills the unread range; no second full fetch.
4. **Report gaps** — no captions, login required, missing modality, exhausted download budget: marked as uncovered rather than written up as a conclusion.

The browser runs headlessly and asks you to take over only for login or verification. Access is an Agent Skill plus MCP server; Claude Code, Codex, and DeepSeek Harness each have a plugin (Codex 0.156+ needs one `envoy hook install --host codex`).

### Scope

No bypassing login, CAPTCHA, regional restrictions, or DRM, and no replacement for web search. Envoy is responsible only for the ranges it actually read and returned. Douyin creator collections and Xiaohongshu image posts follow the [platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md).

### Evaluation

Running a same-link, same-model comparison against direct reading: completion time, actual tokens, fact coverage, citation quality, and human intervention. The current pilot has **6 sources and 12 tasks** — not enough to claim speed or token savings yet. See the [evaluation method](benchmarks/README.md) and [pilot data](benchmarks/pilots/README.md).

### Documentation

- [Installation, updates, and MCP setup](integrations/skills/envoy/INSTALL.md)
- [Claude Code plugin](integrations/claude-code/envoy/README.md)
- [Codex plugin](integrations/codex/envoy/README.md)
- [DeepSeek Harness plugin](integrations/dsh/README.md)
- [Product and engineering status](docs/architecture/agent-link-reader-status.md)
- [Platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [Changelog](CHANGELOG.md)
- [Security and privacy](NOTICE)
