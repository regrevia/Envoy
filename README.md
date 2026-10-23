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

Envoy 通过 Agent Skill + MCP 接入支持 MCP 的 AI。浏览器默认在后台运行；遇到登录或验证时才请你接管。安装 Skill、CLI runtime 和 MCP 连接是三个步骤，按[安装指南](integrations/skills/envoy/INSTALL.md)完成即可。

## 能力

- 提取公开网页、PDF 文本、图片、字幕和用户指定的本地视频
- 按需续读、搜索和复用材料，保留来源与时间定位
- 可选本地 Whisper、FFmpeg 和 OCR；缺少模态时明确标记材料不完整
- 支持 macOS、Windows、Linux；代码与文档采用 Apache-2.0

登录、验证码、地区限制和 DRM 不会被绕过。抖音主页集合、小红书图文等能力仍按[平台验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)逐项验证。

## 评测状态

我们正在进行同链接、同模型的 Envoy / 直接读取对比，记录完成时间、实际 token、事实覆盖、引用质量和人工介入情况。当前公开 pilot 为 **6 个来源、12 个任务**，缺少成对基线和宿主实际 usage，尚不能得出“更快”或“节省 token”的结论。查看[评测方法](benchmarks/README.md)和[pilot 数据](benchmarks/pilots/README.md)。

## 文档

- [安装、更新与 MCP 配置](integrations/skills/envoy/INSTALL.md)
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

Envoy connects to MCP-capable agents through an Agent Skill and MCP server. The browser runs headlessly by default; you are asked to take over only when login or verification is needed. Installing the Skill, CLI runtime, and MCP connection are separate steps. Follow the [installation guide](integrations/skills/envoy/INSTALL.md).

### Capabilities

- Extract public web pages, PDF text, images, captions, and user-selected local videos
- Continue, search, and reuse material with source and timestamp locators
- Optional local Whisper, FFmpeg, and OCR; missing modalities are reported explicitly
- macOS, Windows, and Linux; original code and documentation are Apache-2.0

Envoy does not bypass login, CAPTCHA, regional restrictions, or DRM. Douyin creator collections and Xiaohongshu image posts remain subject to the [platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md).

### Evaluation

We are comparing Envoy with direct reading on the same links and model, measuring completion time, actual host tokens, fact coverage, citation quality, and human intervention. The current public pilot has **6 sources and 12 tasks**, without paired baselines or actual host usage. It does not establish speed or token savings. See the [evaluation method](benchmarks/README.md) and [pilot data](benchmarks/pilots/README.md).

### Documentation

- [Installation, updates, and MCP setup](integrations/skills/envoy/INSTALL.md)
- [Product and engineering status](docs/architecture/agent-link-reader-status.md)
- [Platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [Changelog](CHANGELOG.md)
- [Security and privacy](NOTICE)
