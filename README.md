<a id="top"></a>
<p align="center">
  <img src="frontend/public/brand/envoy-bird.png" alt="Envoy" width="104">
</p>

<h1 align="center">把链接分享给你的 AI</h1>

<p align="center">
  Envoy 提取网页、视频和本地媒体中的可追溯材料；由你的 AI 理解、分析并完成交付。
</p>

<p align="center">
  <a href="integrations/skills/envoy/INSTALL.md">安装 Envoy</a> ·
  <a href="https://github.com/regrevia/Envoy/releases">下载发行版</a> ·
  <a href="https://github.com/regrevia/Envoy/issues/new/choose">反馈问题</a> ·
  <a href="#english">English</a>
</p>

Envoy 通过 Agent Skill + MCP 接入支持 MCP 的 AI。浏览器默认在后台运行；遇到登录或验证时才请你接管。安装 Skill、CLI runtime 和 MCP 连接是三个步骤，按[安装指南](integrations/skills/envoy/INSTALL.md)完成即可。Claude Code、Codex 和 DeepSeek Harness 都有对应插件：完成 MCP 挂载，并在提问带链接时先走 Envoy，而不是让内置抓取重复拉整页（Codex 0.156 起还需执行一次 `envoy hook install --host codex` 写入用户级 hook）。Envoy 不替代联网搜索，只负责用户给出的链接、视频/音频和需要完整来源证据的读取。

> **典型场景**：把一条 B 站视频整理成带 `[mm:ss]` 引用的 Wiki。见[完整示例](#bilibili-wiki)。

## 能力

- 提取公开网页、PDF 文本、图片、字幕和用户指定的本地视频
- 按需续读、搜索和复用材料，保留来源与时间定位
- 可选本地 Whisper、FFmpeg 和 OCR；缺少模态时明确标记材料不完整
- 支持 macOS、Windows、Linux；代码与文档采用 Apache-2.0

登录、验证码、地区限制和 DRM 不会被绕过。抖音主页集合、小红书图文等能力仍按[平台验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)逐项验证。

<a id="bilibili-wiki"></a>

## 示例：把一条 B 站视频做成可引用的知识库

直接让模型“看视频”，通常只能拿到标题和简介；Envoy 先把视频变成带时间定位的材料，再让模型组织成 Wiki 或知识库。

对 AI 说：

> 把这条视频整理成知识库：`https://www.bilibili.com/video/BV...`
>
> 先读前 10 分钟，按主题分节；每条结论后标注 `[mm:ss]`；没覆盖到的部分单列“待补”，不要猜。

Envoy 负责：

- 解析标题、作者、时长和章节；
- 优先读取公开字幕；没有字幕时，对指定时间段运行本地 Whisper ASR；
- 返回带 `video_time` 定位的字幕/转写片段和材料 ID；
- 需要看 PPT、图表或演示画面时，按宿主能力请求抽帧，并接收视觉观察结果。

AI 负责：

- 用转写材料生成 Markdown Wiki（可直接放进 Obsidian、Notion 等知识库）：概述、分节要点、时间线、术语表、引用与待补；
- 用 `envoy_search` 在材料里定位关键词，用 `envoy_expand` 补齐未读时间段；
- 每条事实都带时间戳，读者可以跳回原视频核对。

产出可以长这样（示意）：

```markdown
## 2. 为什么选择固定轮

- 固定轮把连续动作拆成可复用的阶段，便于逐段调试。[04:18]
- 作者对比了三种方案，并给出取舍条件。[07:02]
- 这一段没有展示实际测试数据；待补。[09:31]
```

边界：Envoy 只对实际读取并返回的时间段负责。视频没有字幕、登录/地区限制、下载预算不足或本地 ASR 不可用时，会返回明确缺口，不会把未覆盖内容写成结论。

## 评测状态

我们正在进行同链接、同模型的 Envoy / 直接读取对比，记录完成时间、实际 token、事实覆盖、引用质量和人工介入情况。当前公开 pilot 为 **6 个来源、12 个任务**，缺少成对基线和宿主实际 usage，尚不能得出“更快”或“节省 token”的结论。查看[评测方法](benchmarks/README.md)和[pilot 数据](benchmarks/pilots/README.md)。

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
  Envoy extracts traceable material from web pages, videos, and local media.
  Your AI analyzes it and creates the deliverable.
</p>

Envoy connects to MCP-capable agents through an Agent Skill and MCP server. The browser runs headlessly by default; you are asked to take over only when login or verification is needed. Installing the Skill, CLI runtime, and MCP connection are separate steps. Follow the [installation guide](integrations/skills/envoy/INSTALL.md). Claude Code, Codex, and DeepSeek Harness each have a plugin that mounts MCP and routes user-supplied links through Envoy before native fetching, avoiding duplicate full-page reads (Codex 0.156+ additionally needs one `envoy hook install --host codex` to write its user-level hook). Envoy does not replace web search; it handles the user's links, video/audio, and reads that need full-source evidence.

> **Typical use case**: turn a Bilibili video into a wiki with `[mm:ss]` citations. See the [full example](#bilibili-video-wiki).

### Capabilities

- Extract public web pages, PDF text, images, captions, and user-selected local videos
- Continue, search, and reuse material with source and timestamp locators
- Optional local Whisper, FFmpeg, and OCR; missing modalities are reported explicitly
- macOS, Windows, and Linux; original code and documentation are Apache-2.0

Envoy does not bypass login, CAPTCHA, regional restrictions, or DRM. Douyin creator collections and Xiaohongshu image posts remain subject to the [platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md).

<a id="bilibili-video-wiki"></a>

### Example: turn a Bilibili video into a citable knowledge base

If you simply ask a model to "watch the video", it usually gets only the title and description. Envoy first turns the video into material with time locators; the model then organizes it into a wiki or knowledge base.

Ask your AI:

> Turn this video into a knowledge base: `https://www.bilibili.com/video/BV...`
>
> Read the first 10 minutes, group sections by theme, and add `[mm:ss]` after each claim. List anything not covered under "To fill"; do not guess.

Envoy handles:

- title, author, duration, and chapters;
- public captions first, then bounded local Whisper ASR for the requested range when captions are unavailable;
- timestamped transcript segments with `video_time` locators and a material ID;
- frame requests for slides, charts, or demos when the host supports images, plus the resulting visual observations.

The AI handles:

- a Markdown wiki you can drop into Obsidian, Notion, or another knowledge base: overview, section highlights, timeline, glossary, citations, and open gaps;
- `envoy_search` to locate terms in the material and `envoy_expand` to fill unread ranges;
- a timestamp on every factual claim, so readers can jump back to the source.

Illustrative output:

```markdown
## 2. Why use a fixed wheel

- A fixed wheel splits a continuous action into reusable stages for per-stage debugging. [04:18]
- The author compares three approaches and states the trade-offs. [07:02]
- This section shows no measured data; to fill. [09:31]
```

Scope: Envoy is responsible only for the ranges it actually read and returned. When captions are absent, access is restricted, the download budget is exceeded, or local ASR is unavailable, it reports the gap instead of turning uncovered content into a conclusion.

### Evaluation

We are comparing Envoy with direct reading on the same links and model, measuring completion time, actual host tokens, fact coverage, citation quality, and human intervention. The current public pilot has **6 sources and 12 tasks**, without paired baselines or actual host usage. It does not establish speed or token savings. See the [evaluation method](benchmarks/README.md) and [pilot data](benchmarks/pilots/README.md).

### Documentation

- [Installation, updates, and MCP setup](integrations/skills/envoy/INSTALL.md)
- [Claude Code plugin](integrations/claude-code/envoy/README.md)
- [Codex plugin](integrations/codex/envoy/README.md)
- [DeepSeek Harness plugin](integrations/dsh/README.md)
- [Product and engineering status](docs/architecture/agent-link-reader-status.md)
- [Platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [Changelog](CHANGELOG.md)
- [Security and privacy](NOTICE)
