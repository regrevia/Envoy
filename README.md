<a id="top"></a>
<p align="center">
  <img src="frontend/public/brand/envoy-bird.png" alt="Envoy" width="104">
</p>

<h1 align="center">把链接分享给你的 AI</h1>

<p align="center">
  一个链接，成为你想要的任何东西。
</p>

<p align="center">
  <a href="#安装">安装 Envoy</a> ·
  <a href="https://github.com/regrevia/Envoy/releases">下载发行版</a> ·
  <a href="https://github.com/regrevia/Envoy/issues/new/choose">反馈问题</a> ·
  <a href="#english">English</a>
</p>

给 AI 一个链接，Envoy 把它变成带来源定位的材料，AI 再用它写你要的东西：Wiki、摘要、报告、脚本。网页、公众号、PDF、小红书图文、B 站视频、本地视频都行。

## 安装

三个宿主各一行。安装脚本只要求 `curl`：uv 和 Python 3.11 它自己装，装好会打印 `envoy install: runtime … protocol … browser …`。

```sh
# Claude Code
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code

# Codex
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex

# DeepSeek Harness
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh
```

不确定宿主是哪个：把最后一段换成 `auto`，脚本会识别已装的 Claude Code / Codex / DSH，都没有就写 MCP 条目（`mcp`、`skill` 也可以单独装；`all` 等同于 `auto`）。只想先看它要做什么，命令前加 `ENVOY_DRY_RUN=1`。从镜像或本地目录安装时，把下载根换成 `ENVOY_RELEASES_BASE`、清单地址换成 `ENVOY_MANIFEST_URL`（目录按 `v<版本>/` 放产物与 `SHA256SUMS`），默认值不变；整仓换到 fork 时设 `ENVOY_REPO`（默认 `regrevia/Envoy`）。Windows PowerShell 用 `irm https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.ps1 | iex`（默认 auto，指定宿主时下载后 `powershell -ExecutionPolicy Bypass -File install.ps1 dsh`）。

装完两条自检：

```sh
envoy doctor --json     # runtime.install.status 应为 pinned：固定唯一 runtime，旧 PATH 上的 envoy 不会抢占
envoy browser setup     # 浏览器安装脚本已装好；这行用于补装或换版本，装完会自动清掉被替换的旧版本
envoy browser prune     # 回收旧版本浏览器下载（升级几次后缓存会翻倍），--dry-run 只报不删
```

升级就是重跑同一行；卸载、企业镜像、各宿主插件目录见 [INSTALL.md](integrations/skills/envoy/INSTALL.md)。

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

同一条链接、同一个模型，只差装不装 Envoy；20 个任务包 × 3 轮提问（每臂 60 轮，共 120 轮），题目与来源冻结在 `benchmarks/fixed-set-v1`。下面是**探索性观察**，还不足以当作结论。

**一眼能看懂的差别。** 同一条链接、同一个模型，只差装不装 Envoy：

| 发生了什么 | 没装 Envoy | 装了 Envoy |
| --- | --- | --- |
| 给出「读完了」的结论 | 13 / 60 轮 | 55 / 60 轮 |
| 跑到超时也没结论 | 41 / 60 轮 | 0 轮 |
| 答案里至少说中一条材料事实 | 14 / 60 轮 | 49 / 60 轮 |
| 材料要点被说中的比例 | 约 9%（47/516） | 约 38%（197/516） |
| 第一次拿到证据要等多久（中位） | 88 秒 | 48 秒 |
| 回答长度（中位） | 3,125 字 | 8,142 字 |
| 空回答（另一组 60 对） | 31 处 | 0 处 |

这张表说明的是「能不能读到东西」，不是「快多少、省多少」：两臂时间上限不同（900 秒 vs 2400 秒），人工核对也没做（`verified` 两臂都是 0）。

**怎么判的。** 每轮先按答案文本归类：`timed_out`（撞墙钟上限）、`no_output`、`infra_failed`、`needs_action`、`cannot_see`（要登录/扫码/授权，或说读不到）、`suspect_complete`（进程正常结束、答案不短、没说读不到——**只是疑似完成，不验证事实是否正确、引用是否落位**）。`verified` 只统计人工评审把 `answer_correct` 判为 true 的轮次；本轮 **verified = 0**，评审表未填。facts 命中是 60% 词覆盖匹配，引用命中要求 URL 规范化到文章级（同一公众号的不同文章不再互相计分），两者都不是正确性判定。

**本轮可复算的观察。** baseline 60 轮：`suspect_complete` 13、`timed_out` 41、`cannot_see` 5、`needs_action` 1。envoy 60 轮：`suspect_complete` 55、`timed_out` 0、`cannot_see` 4、`needs_action` 1。两个 `verified` 都是 0。facts 词覆盖也偏向 envoy（claim 覆盖 9.1% vs 38.2%，至少命中一条 facts 的轮次 14/60 vs 49/60），但同样受下面这条限制影响，只能算线索。

**不能这么读。** 两臂时间上限不同：baseline 900 秒（`/tmp/AB3/run-meta.json` 的 `cap_seconds`；60 轮里 41 轮撞上限），envoy 2400 秒（`/tmp/AB6` 各轮 `turn_timeout_seconds`；0 轮撞上限）。完成率差距被上限差异放大，不能读成工具能力差；`suspect_complete` 也不能读成“答案正确”。要变成结论还缺：同预算比较（两臂同上限重跑）、可复核的真实任务判定（不是关键词分类）、能与来源快照对上的产物，以及两臂完整的 60/60 轮账本。旧版首页给出的完成率与样本数字来自更早的部分运行，本轮无法复现，已撤回。

见[评测方法](benchmarks/README.md)、[口径与偏差](benchmarks/fixed-set-v1/AB-PROTOCOL.md)与[复核记录](docs/acceptance/envoy-read-failure-2026-10-25.md)。

## 链接兼容性

123 条真实链接（新闻、公众号、B站、播客、PDF、论坛、代码文档……）跑了一遍，看三件事：

| 看什么 | 结果 |
| --- | --- |
| 100 条链接里，能拿到可用材料 | **80 条（80.5%）** |
| 能连上的 100 条里，能读出正文 | **93 条（92.5%）** |
| 打不开的链接要干等多久 | **26 秒**（原来 77 秒，少了 67%） |

「可用」= 能当素材：整篇可引用，或读到一部分并写明缺了什么；读不出来会说清原因（要登录、被人机校验拦住、网络不通），不猜。

剩下没拿到的 24 条里，16 条根本连不上（全是境外站点，卡在本机代理出口），8 条卡在登录墙或人机校验——这两种装不装 Envoy 都读不到，Envoy 会直说，让 Agent 走别的路。

**装不装差多少。** 同一条链接、同一个模型，每臂 60 轮（探索性对照：两臂时间上限不同，`verified` 都是 0，读法见[评测](#评测)）：

| 看什么 | 不装 Envoy | 装了 Envoy |
| --- | --- | --- |
| 读完并给出结论 | 22%（13/60 轮） | **92%（55/60 轮）** |
| 跑到超时还没结论 | 68%（41/60 轮） | **0 轮** |
| 答案里至少说中一条原文事实 | 23%（14/60 轮） | **82%（49/60 轮）** |
| 空回答 | 31 处 | **0 处** |

复算：`scripts/acceptance/link_coverage.py`（报告与口径见[兼容性实验](docs/experiments/link-compatibility.md)）。

## 本地 Web 界面（Docker）

**插件与 skills 不依赖 Docker。** Claude Code / Codex / DSH 三个装法都是本机跑：`pip install envoy-cli` 或 `uv tool install`，再加一个 skills 目录或插件包，不需要容器、不需要这个 compose。Docker 只服务仓库里那套旧的 Web 界面（FastAPI + Next.js + MySQL），用来自本机点着看任务和产物；不用这个界面就完全不用管它。

需要 Docker Desktop，仓库根目录：

```bash
cp .env.example .env
docker compose up -d --build
```

前端 `http://127.0.0.1:8200`，后端健康检查 `http://127.0.0.1:8140/api/v1/health` 返回 `{"status":"ok"}`。`docker compose ps` 里 `mysql`、`redis`、`backend`、`frontend` 都 `healthy` 即可用；MySQL 首次启动会自动执行 `sql/` 下的建表脚本，上传与导出都写在 `envoy_runtime` 卷里，默认不需要对象存储。首次构建会拉 MySQL 8.4、Redis，并在后端镜像里装 FFmpeg、Tesseract、Chromium，耗时几分钟。`docker compose down` 保留数据卷；只有 `down -v` 会删库和本地文件。端口、密码、模型配置都在 `.env`，构建加速与排错见 [Docker 一键启动](docs/quickstart-docker.md)。

## 文档

- [安装、更新与 MCP 配置](integrations/skills/envoy/INSTALL.md)
- [Claude Code 插件](integrations/claude-code/envoy/README.md)
- [Codex 插件](integrations/codex/envoy/README.md)
- [DeepSeek Harness 插件](integrations/dsh/README.md)
- [产品与技术状态](docs/architecture/agent-link-reader-status.md)
- [平台真实验收矩阵](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [链接覆盖基线与复测](docs/product/link-coverage-benchmark.md)
- [登录扫码交接](docs/product/qr-signin-handoff.md)
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

### Install

One line per host. The installer only needs `curl`: it installs uv and Python 3.11 itself and prints `envoy install: runtime … protocol … browser …` when done.

```sh
# Claude Code
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code

# Codex
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex

# DeepSeek Harness
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh
```

Not sure which host you have: use `auto` instead — it detects an installed Claude Code / Codex / DSH and falls back to an MCP entry (`mcp` and `skill` also install on their own; `all` is the same as `auto`). Add `ENVOY_DRY_RUN=1` to see what it would do first. To install from a mirror or a local directory, point `ENVOY_RELEASES_BASE` at the download root and `ENVOY_MANIFEST_URL` at the manifest (layout: `v<version>/` with the artifacts and `SHA256SUMS`); the defaults are unchanged; to point at a fork set `ENVOY_REPO` (default `regrevia/Envoy`). On Windows PowerShell: `irm https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.ps1 | iex` (auto by default; for one host download it and run `powershell -ExecutionPolicy Bypass -File install.ps1 dsh`).

Two checks after installing:

```sh
envoy doctor --json     # runtime.install.status should be pinned: one runtime, an older envoy on PATH cannot win
envoy browser setup     # the installer already provisions it; rerun to repair or change versions, stale copies are pruned
envoy browser prune     # reclaim browser downloads left behind by upgrades; --dry-run only reports
```

Upgrading means running the same line again. Uninstall, mirrors, and each host's plugin directory: [INSTALL.md](integrations/skills/envoy/INSTALL.md).

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

Same link, same model, the only difference is whether Envoy is installed; 20 task packages × 3 questions per arm (60 turns each, 120 total) on a frozen package (`benchmarks/fixed-set-v1`). What follows is an **exploratory observation**, not yet a conclusion.

**The short version.** Same link, same model, the only difference is Envoy:

| What happens | Without Envoy | With Envoy |
| --- | --- | --- |
| Reaches a "finished reading" answer | 13 / 60 turns | 55 / 60 turns |
| Times out with no answer | 41 / 60 turns | 0 turns |
| Answer states at least one fact from the source | 14 / 60 turns | 49 / 60 turns |
| Share of source claims that get stated | ~9% (47/516) | ~38% (197/516) |
| Time to the first piece of evidence (median) | 88 s | 48 s |
| Answer length (median) | 3,125 chars | 8,142 chars |
| Empty answers (a separate 60-pair run) | 31 | 0 |

This table says whether the agent could read the source at all — not how fast or how cheap it is. The arms ran under different caps (900 s vs 2400 s), and no human correctness review exists yet (`verified` is 0 for both).

**How turns are scored.** Each turn is classified from its answer text: `timed_out` (hit the wall-clock cap), `no_output`, `infra_failed`, `needs_action`, `cannot_see` (asks for login/scan/authorization, or says it cannot read), `suspect_complete` (process exited normally, answer is not short, no refusal — **suspected only, with no check of factual correctness or citation placement**). `verified` counts only turns a human review marked `answer_correct = true`; **verified = 0** here because the review file is still empty. Fact hits are a 60% token-overlap match, and citation hits require an article-level canonical URL (two posts from the same WeChat account no longer credit each other); neither is a correctness verdict.

**What can be recomputed now.** baseline, 60 turns: `suspect_complete` 13, `timed_out` 41, `cannot_see` 5, `needs_action` 1. envoy, 60 turns: `suspect_complete` 55, `timed_out` 0, `cannot_see` 4, `needs_action` 1. `verified` is 0 for both. Fact-word coverage also leans envoy (claim coverage 9.1% vs 38.2%; turns with at least one fact 14/60 vs 49/60), but it is subject to the limit below, so treat it as a signal only.

**What it cannot be read as.** The two arms ran under different caps: baseline 900 s (`cap_seconds` in `/tmp/AB3/run-meta.json`; 41 of 60 turns hit it) and envoy 2400 s (`turn_timeout_seconds` in `/tmp/AB6`; 0 of 60 hit it). The completion gap is inflated by that difference and is not a capability comparison; `suspect_complete` is not "correct". Turning this into a conclusion needs a same-budget run (both arms at one cap), a real task-level verdict that can be reviewed (not keyword rules), artifacts that can be checked against the source snapshots, and a full 60/60-turn ledger per arm. The earlier front-page completion rates and sample counts came from an earlier partial run, cannot be reproduced from the archived data here, and have been withdrawn.

See the [evaluation method](benchmarks/README.md), the [protocol and deviations](benchmarks/fixed-set-v1/AB-PROTOCOL.md), and the [review record](docs/acceptance/envoy-read-failure-2026-10-25.md).

### Link coverage

123 real links (news, WeChat posts, Bilibili, podcasts, PDFs, forums, code docs) — three questions:

| Question | Answer |
| --- | --- |
| Out of 100 links, how many give usable material | **80 (80.5%)** |
| Out of the 100 that answered at all, how many yielded body text | **93 (92.5%)** |
| How long an unreachable link makes you wait | **26 s** (was 77 s, 67% less) |

"Usable" means quotable in full, or partially read with the gap named; a failure states why (login, bot check, network) instead of guessing.

Of the 24 that were not usable, 16 hosts were unreachable — all overseas, blocked at the local proxy exit — and 8 sat behind a login or bot-check wall. Neither case gets better with Envoy installed; Envoy says so and the agent takes another route.

**With vs without.** Same link, same model, 60 turns per arm (exploratory: the arms ran under different caps and `verified` is 0 for both — see [Evaluation](#evaluation)):

| What happens | Without Envoy | With Envoy |
| --- | --- | --- |
| Finishes reading and answers | 22% (13/60 turns) | **92% (55/60 turns)** |
| Times out without an answer | 68% (41/60 turns) | **0 turns** |
| Answer states at least one fact from the source | 23% (14/60 turns) | **82% (49/60 turns)** |
| Empty answers | 31 | **0** |

Rerun with `scripts/acceptance/link_coverage.py`; reports and definitions are in the [link-compatibility experiment](docs/experiments/link-compatibility.md).

### Local web interface (Docker)

**The plugin and skills do not need Docker.** All three installs (Claude Code, Codex, DSH) run locally: `pip install envoy-cli` or `uv tool install`, plus a skills directory or plugin package — no container, no compose. Docker only serves the legacy web interface in this repo (FastAPI + Next.js + MySQL) for browsing tasks and artifacts on your own machine; skip it entirely if you do not want that UI.

With Docker Desktop running, from the repository root:

```bash
cp .env.example .env
docker compose up -d --build
```

The interface is at `http://127.0.0.1:8200`; `http://127.0.0.1:8140/api/v1/health` answers `{"status":"ok"}`. The stack is ready when `mysql`, `redis`, `backend` and `frontend` all report `healthy`; MySQL bootstraps the schema from `sql/` on first start, and uploads plus exports stay on the `envoy_runtime` volume, so no object storage is required. The first build pulls MySQL 8.4 and Redis and installs FFmpeg, Tesseract and Chromium into the backend image, so it takes a few minutes. `docker compose down` keeps the volumes; only `down -v` drops the database and local files. Ports, passwords and model settings live in `.env`; build acceleration and troubleshooting are in [Docker quickstart](docs/quickstart-docker.md).

### Documentation

- [Installation, updates, and MCP setup](integrations/skills/envoy/INSTALL.md)
- [Claude Code plugin](integrations/claude-code/envoy/README.md)
- [Codex plugin](integrations/codex/envoy/README.md)
- [DeepSeek Harness plugin](integrations/dsh/README.md)
- [Product and engineering status](docs/architecture/agent-link-reader-status.md)
- [Platform acceptance matrix](docs/acceptance/platform-coverage-matrix-2026-10-21.md)
- [Link coverage baseline and re-run](docs/product/link-coverage-benchmark.md)
- [Sign-in QR handoff](docs/product/qr-signin-handoff.md)
- [Changelog](CHANGELOG.md)
- [Security and privacy](NOTICE)
