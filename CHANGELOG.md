# 更新日志 / Changelog

## 未发布 / Unreleased

### 中文

- MCP 正文预算：单行超长 JSON 不再只输出头部——正文按 240 字符硬折行，逐块给出
  字符数与 `output truncated: showing N of M characters`，续读提示按材料是否还在
  决定给 `envoy_expand` 还是 `envoy_read refresh=true`。
- 材料过期不再误导：缓存里仍有的 payload 照常返回给被拦的源，但正文不再提示
  `envoy_expand` 一个已被回收的 `material_id`（正是它把调用方引到
  `expired_resource`），改为明确的 `refresh=true` 续读路径。
- 启动轻量化：`httpx` / `pypdf` / Playwright / feed 解析改为惰性导入，
  `envoy --version` 走独立快路径，`doctor` 报浏览器磁盘占用；默认安装依然不拉
  `torch` / `whisper`（`[media]` 之外没有入口），mac 上 `import envoy_cli.cli`
  从 127.8 ms 降到 106.8 ms、`--version` 从 130.1 ms 降到 109.1 ms。
- Windows 适配合并：dry-run 不再因「计划内 runtime」报退出码 1、用户 PATH 保持
  `REG_EXPAND_SZ`、安装脚本 UTF-8 无 BOM 并兼顾 OEM 代码页、uv 分支在临时目录里
  按 `SHA256SUMS` 校验并保留 wheel 文件名。

### English

- MCP body budget: a single-line oversized JSON block is now hard-wrapped at 240
  characters and reported as `output truncated: showing N of M characters`
  instead of arriving as a header with no body; the continuation hint chooses
  `envoy_expand` or `envoy_read refresh=true` by whether the material survives.
- Expired material no longer misleads: a cached payload is still served when the
  source is blocked, but its text stops offering `envoy_expand` for a reclaimed
  `material_id` (the path that led to `expired_resource`) and names the
  `refresh=true` re-read instead.
- Startup lightweighting: lazy `httpx` / `pypdf` / Playwright / feed imports, a
  dedicated `envoy --version` fast path, browser disk usage in `doctor`; a
  default install still pulls no `torch` / `whisper` (only the `[media]` extra
  does). On macOS `import envoy_cli.cli` went 127.8 → 106.8 ms and `--version`
  130.1 → 109.1 ms.
- Windows adaptation merged: dry-run no longer exits 1 on a planned runtime, the
  user PATH keeps `REG_EXPAND_SZ`, the installer writes UTF-8 without BOM around
  the OEM code page, and the uv branch verifies `SHA256SUMS` in a temp directory
  while preserving the wheel filename.

## 0.1.24 - 2026-10-28

### 中文

- 一键安装：`install.sh`（macOS / Linux）与新增的 `install.ps1`（Windows）都能在
  一条命令里装好 runtime、装好浏览器、认好宿主（Claude Code / Codex / DSH），
  再把 MCP 条目接到对应配置。没检测到宿主 CLI 时改为写 MCP 条目，不再假装装上了
  Skill。
- 固定单一 runtime：安装时把所用的 `envoy` 绝对路径写进 `~/.envoy/runtime-path`，
  之后一律用它。PATH 里更早出现的旧 `envoy` 不会再悄悄盖掉新装的那份
  （离线用例 `scripts/check_installer_offline.sh` 覆盖）。
- 一键安装的仓库源可覆盖：`ENVOY_REPO=<owner>/Envoy` 同时改下载地址、Claude Code
  插件市场源与 Codex 市场源；镜像或 fork 装出来的就是那份代码，不会悄悄装成上游
  （环回干跑 `scripts/check_installer_e2e_local.sh` 覆盖两条一键分支）。
- 浏览器只在安装期准备一次：读网页时缺浏览器直接快速失败并给出
  `envoy browser setup`，不再在宿主的 300 秒工具时限里下 900 秒的 Chromium。
- Windows：新增 `process_control.py`，探活用 `OpenProcess` / `GetExitCodeProcess`，
  停进程用 `taskkill /PID /T /F`，不再调用 Windows 上不存在的 `os.killpg` /
  `signal.SIGTERM`；取消 auth 会话也走同一条路径。
- 本地 PDF：本地 PDF 不再走视频请求，按页解析并按页提交断点；下载与逐页解析
  共用一个剩余时限，预算不够时明确给出 paused / resumable 而不是超时。
- 长视频 / 播客：ASR 断点按音频内容摘要命名，换一个临时目录仍能接着跑；`collect`
  的子请求拿到剩余预算，不会各自从头计时。
- 发行包：`SHA256SUMS` 与 `release-manifest.json` 现在覆盖 DSH bundle
  （`envoy-dsh-<version>.tgz`）与两个安装脚本；正式发行工作流新增 npm 发布步骤
  （未配置 `NPM_TOKEN` 时跳过并说明）。

### English

- One-command install: `install.sh` (macOS / Linux) and the new `install.ps1`
  (Windows) provision the runtime, the browser, and the detected host
  (Claude Code / Codex / DSH), then wire the MCP entry. With no host CLI
  detected they write the MCP entry instead of pretending a Skill was installed.
- One pinned runtime: install records the absolute `envoy` path in
  `~/.envoy/runtime-path` and every later command uses it, so an older `envoy`
  earlier in `PATH` cannot silently win (`scripts/check_installer_offline.sh`).
- The one-click installer takes its repository from `ENVOY_REPO=<owner>/Envoy`,
  which now redirects the download base, the Claude Code marketplace source and
  the Codex marketplace source together, so a mirror or fork installs that copy
  instead of silently installing upstream (`scripts/check_installer_e2e_local.sh`).
- The browser is provisioned once at install time: a read that finds no browser
  fails fast with `envoy browser setup` instead of downloading Chromium for up
  to 900 s inside a 300 s host tool deadline.
- Windows: new `process_control.py` probes with `OpenProcess` /
  `GetExitCodeProcess` and stops with `taskkill /PID /T /F` instead of the
  Unix-only `os.killpg` / `signal.SIGTERM`; auth-session cancel uses the same
  path.
- Local PDF: a local PDF is parsed as a document (never as video), committed one
  page at a time, and download plus per-page parsing share one remaining
  deadline, so an exhausted budget reports paused / resumable instead of a
  timeout.
- Long video / podcast: ASR checkpoints are named by the audio content digest,
  so a re-read from a different temp directory resumes instead of starting
  over; `collect` passes its remaining budget to child reads.
- Release assets: `SHA256SUMS` and `release-manifest.json` now cover the DSH
  bundle (`envoy-dsh-<version>.tgz`) and both installers, and the release
  workflow publishes the npm bundle (skipping with a note when `NPM_TOKEN` is
  absent).

## Unreleased

### 文档

- README（中/英）新增「安装」段：Claude Code / Codex / DSH 各一行 `install.sh` 命令、`auto` 识别、`ENVOY_DRY_RUN` 预演、Windows `install.ps1`，以及装后 `envoy doctor --json` 与 `envoy browser setup` 两条自检。

### 中文

- 安装后自检：`envoy doctor` 新增 `runtime` 一行，显示当前运行的 `envoy` 是否就是
  安装时固定在 `~/.envoy/runtime-path` 的那份，并对未固定 / 固定文件已消失 /
  PATH 里另有旧 `envoy`（`shadowed`）各给一条修复动作；`doctor --json` 同步给出
  `runtime.install`。
- 安装收尾：`install.sh` 与 `install.ps1` 装完打印浏览器状态和安装状态，非 `pinned`
  时点名 PATH 实际指向哪里；Windows 安装用法并入 `INSTALL.md`。
- 读取判定稳定：同一个链接是「不是公网地址」还是「域名解析不出来」，现在由
  一次解析给出结论并在本次读取内复用，不再解析两次——两次之间 DNS 抖一下，
  报错码就可能从 `source_unavailable` 翻成 `network_error`（或反过来）。

- 安装源可覆盖：`install.sh` 与 `install.ps1` 的四处下载都走 `ENVOY_RELEASES_BASE`（清单地址另有
  `ENVOY_MANIFEST_URL`），镜像/本地目录按 `v<版本>/` 放产物与 `SHA256SUMS` 即可，默认仍是公开路径。
  `scripts/check_installer_e2e_local.sh` 在 `127.0.0.1` 上真跑一遍「解析版本 → 下载 → 校验摘要 → 解包」，
  再确认 `ENVOY_DRY_RUN=1` 只打印计划不落盘、篡改摘要会中止安装。
- 装前校验管到实验计划：`verify_delivery.py` 多查一项 `EXPERIMENT-PLAN.json` —— schema、条目 id
  唯一、状态合法，`passed`/`failed` 必须带 `evidence` 与 `observed_at`，`not_run` 不许夹带证据；
  预览包之前根本没带这份计划，已补上。
- 预演不落盘：`ENVOY_DRY_RUN=1` 原来会在 `ensure_runtime` 里把选中的 `envoy` 真的 pin 到
  `~/.envoy/runtime-path`；预演现在连这一步都只打印 `  + pin …`，`mcp` 分支也不再写模板文件。
- 安装可换仓库：`install.sh` 与 `install.ps1` 的清单地址、发布根、插件市场地址都由 `ENVOY_REPO` 推出
  （默认 `regrevia/Envoy`），fork 或内网镜像仓库不必再改脚本；README 中/英同步说明。
- 快速失败：连接级失败（拒绝连接、连接超时、代理不可用）不再在同一个出口上
  连试三次——每次都要重新付一次连接超时。出口仍按 `proxy_plan()` 逐个尝试。
  实测不可达页面（reuters.com）单行从 `77.4 s` 降到 `25.9 s`，结论与错误码不变。
- 适配：`podcasts.apple.com`、`open.spotify.com`、`docs.anthropic.com`、
  `infoq.cn`、`engadget.com`、`php.net`、`theverge.com` 提升为 supported
  （均以实测出正文为前提）。非 http(s) scheme（`mailto:` / `ftp:` / `file:` 等）
  与 `x.com` / `twitter.com`、微信首页改判 unsupported：宿主要求「别调用工具、
  走原生能力」，不再白付一次读。
- 语料：第 19 轮补 3 行、第 20 轮补 5 行、第 21 轮补 5 行，声明 138 行；最近一轮
  实跑 123 行、可用 `99 = 80.5%`（同组成 118 行子集 `94 = 79.7%`，可达口径
  `99/107 = 92.5%`，两轮稳定口径 `92/118 = 78.0%`）。
- 度量修正：`classify()` 原先用 200 字硬下限判可用，整页交付但只有 170 字的
  `example.com` 被误记为 empty；现在「交付 ≥40 字且整页不超过交付量 2 倍」按短页
  计可用，`total_chars >= 1000` 的截断分支保留。
- Skill：长视频 / 播客要在同一次调用里显式抬高 `budget.max_work_seconds`
  （短集 300、长录音到 900），而不是超时后重读一遍。
- 实验记录：`record_experiment.py` 把一条实测结果连同证据行和观测数字写进
  `EXPERIMENT-PLAN.json`；没有证据的「通过 / 失败」写不进去，跑过的条目也不会
  被悄悄改回 `not_run`，计划级状态跟着条目重算。不带 `--write` 只干跑。
- 安装包校验：`install.sh` 下载后核对 `SHA256SUMS`，取不到清单或机器上没有
  `shasum`/`sha256sum` 时各打一行说明；清单拿到了却没有该文件条目就直接失败，
  不再像以前那样静默跳过；`install.ps1` 侧也改成同样的两条分支，两平台对
  「不可达」与「缺条目」给一样的反应。
- 安装源可覆盖：`ENVOY_RELEASES_BASE` 与 `ENVOY_MANIFEST_URL` 让镜像或本地目录
  按同样布局（`v<版本>/` + 清单）提供安装包，默认值仍是公开路径，`install.sh` 与 `install.ps1` 的
  四处下载全部走同一个根（Windows 侧同名变量）。`scripts/check_installer_e2e_local.sh` 在 127.0.0.1 上
  起一个镜像，完整跑「解析版本 → 下载 → 校验摘要 → 解包」，并把摘要改错验证它会
  真的停下：不进公网、不装运行时 wheel。

### English

- Post-install self-check: `envoy doctor` gains a `runtime` line that says whether
  the running `envoy` is the one pinned at `~/.envoy/runtime-path`, with one
  repair action for unpinned, stale-pin, and shadowed-by-PATH installs;
  `doctor --json` exposes the same data under `runtime.install`.
- Installer epilogue: `install.sh` and `install.ps1` now print the browser state
  and the install state, naming where `PATH` actually points when it is not the
  pinned runtime; the Windows usage lives in `INSTALL.md`.
- Stable URL verdict: whether a link is a non-public address or a host name that
  does not resolve is now decided by one lookup and reused for the whole read
  attempt. Two lookups moments apart could disagree and flip the reported code
  between `source_unavailable` and `network_error`.

- Fast fail: connect-level failures (refused, connect timeout, unusable proxy) are
  no longer retried three times on the same exit; each retry paid a fresh connect
  timeout. Planned exits are still tried in order. Measured on an unreachable page
  (reuters.com): one row drops from `77.4 s` to `25.9 s` with the same verdict.
- Support: `podcasts.apple.com`, `open.spotify.com`, `docs.anthropic.com`,
  `infoq.cn`, `engadget.com`, `php.net` and `theverge.com` become supported
  (each one measured delivering text first). Non-HTTP(S) schemes (`mailto:`,
  `ftp:`, `file:`, ...), `x.com` / `twitter.com` and the WeChat home page now
  report unsupported, so the host skips the tool call and uses its native
  reader instead of paying for a read that cannot succeed.
- Corpus: 3 rows added in round 19, 5 in round 20 and 5 in round 21; 138 declared.
  The latest round measured 123 rows, usable `99 = 80.5%` (same-composition
  118-row subset `94 = 79.7%`, reachable denominator `99/107 = 92.5%`, and a
  two-round stable measure of `92/118 = 78.0%`).
- Measurement fix: `classify()` used a flat 200-character floor, so
  `example.com` (whole page delivered, 170 characters) was recorded as empty;
  a page delivered in full with at least 40 characters now counts as usable,
  while the `total_chars >= 1000` truncation branch stays.
- Skill: a long video or podcast should raise `budget.max_work_seconds` in the
  same call (300 for a short episode, up to 900 for a long recording) instead of
  re-reading after the budget timeout.

- Experiment record: `record_experiment.py` writes one measured result, its
  evidence line and the observed numbers into `EXPERIMENT-PLAN.json`. A
  pass or fail without evidence is refused, an entry that already ran cannot
  be quietly turned back into `not_run`, and the plan-level status is
  recomputed from the entries. Without `--write` it only prints the change.

- Installer checksum: `install.sh` verifies `SHA256SUMS` after downloading and now
  says so when the list is unreachable or no `shasum`/`sha256sum` exists; a
  reachable list that lacks the file's entry fails instead of being skipped, so
  POSIX and Windows now react the same way to an unreachable list and to a
  list missing the entry.
- The release root is overridable: `ENVOY_RELEASES_BASE` and
  `ENVOY_MANIFEST_URL` let a mirror or a local directory serve the same
  layout, defaulting to the public paths. `scripts/check_installer_e2e_local.sh`
  serves the release over 127.0.0.1 and runs version resolution, download,
  checksum verification and unpacking end to end, plus a tampered digest case.

## 0.1.21 - 2026-10-27

- 适配：`theguardian.com`、`youtube.com`、`platformer.news`、`feeds.arstechnica.com`、
  `feeds.simplecast.com`、`dev.to`、`discourse.mozilla.org`、`forum.arduino.cc`
  提升为 supported（均以实测出正文为前提）。
- 适配：语料新增 6 行 feed / forum / audio，第 15 轮 107 行口径可用 83（78.3%），
  可达口径 90.2%，同组成子集 77.5%。
- 文档：`docs/experiments/link-compatibility.md` 补第 14、15 轮（含三口径与抖动披露）。

## 0.1.20 - 2026-10-27

### 中文

- 验收语料质量修正：把 23 字的 dummy PDF 换成真实 `rfc9110.pdf`（7389 字），
  历史里那条永远 `empty` 的假阴性消失。
- `news` 族补 `oschina.net`（5637 字）与 `pingwest` 文章页（2542 字），news 族可用
  行从 3 升到 6。
- 语料 102 行 / 分母 101：可用 `79/101 = 78.2%`；剔 13 条本机出口不可达后
  可达口径 `79/88 = 89.8%`；同组成 98 行子集 `76/98 = 77.6%`。

### English

- Corpus quality fix: the 23-character dummy PDF is replaced with the real
  `rfc9110.pdf` (7389 chars), removing a permanent false `empty` from the history.
- The `news` family gains `oschina.net` (5637 chars) and a `pingwest` article
  (2542 chars): usable news rows go from 3 to 6.
- Corpus is 102 rows over a denominator of 101: usable `79/101 = 78.2%`; excluding
  the 13 locally unreachable rows, `79/88 = 89.8%`; the same-scope 98-row subset is
  `76/98 = 77.6%`.

## 0.1.19 - 2026-10-27

### 中文

- 新增 3 个可读站点：`gitlab.com`（README 7961 字）、`douban.com` 小组话题（1253 字，
  `static_http`）、`ximalaya.com` 专辑页（504 字，需浏览器）。
- 验收语料 97 → 100 行，预检剔 1 条死链，分母 99：可用 `76/99 = 76.8%`；剔掉 13 条本机
  出口不可达的行后，可达口径 `76/86 = 88.4%`；同口径 96 行子集 `75/96 = 78.1%`。
- `social` 与 `audio` 两个家族第一次出现可用行（此前各自 0 可用）。
- 关闭弹窗逻辑补上欧洲同意横幅：`.cmp-container`、`#onetrust-banner-sdk`、
  `.qc-cmp2-container`、`[aria-label*="consent" i]`。

### English

- Three new readable hosts: `gitlab.com` (README, 7961 chars), `douban.com` group topics
  (1253 chars via `static_http`) and `ximalaya.com` album pages (504 chars via the browser).
- Acceptance corpus 97 → 100 rows with one dead link removed (denominator 99): usable
  `76/99 = 76.8%`; with the 13 locally unreachable rows excluded, `76/86 = 88.4%`; the
  same-scope 96-row subset is `75/96 = 78.1%`.
- The `social` and `audio` families now have usable rows for the first time.
- The overlay dismissal script also closes European consent banners: `.cmp-container`,
  `#onetrust-banner-sdk`, `.qc-cmp2-container` and `[aria-label*="consent" i]`.

## 0.1.18 - 2026-10-27

### 中文

- 新增 5 个可读站点：`solidot.org`、`guancha.cn`、`thepaper.cn`、`developer.mozilla.org`、
  `docs.python.org`（实测 7942 / 7938 / 785 / 7540 / 3708 字，全部 `static_http`，`< 2.1s`）。
- 验收语料 93 → 97 行，预检剔 1 条死链，分母 96：可用 `75/96 = 78.1%`（可读 20 + 部分 55），
  零超时；同口径 95 行子集 `74/95 = 77.9%`。
- YouTube 字幕通道两次运行稳定返回 `video_subtitles`（`manual_subtitles`，2.2 万字原文、
  单次返回 1.1 万字，可继续 `envoy_expand`），此前 `challenge_required` 是本机出口问题。
- 修复 CI 静态检查：`mcp.py` 里重复的 `urlparse` 导入触发 ruff `F811`，0.1.16/0.1.17 两次
  `main` 的静态检查因此失败，现已修掉并恢复绿灯。

### English

- Five new readable hosts: `solidot.org`, `guancha.cn`, `thepaper.cn`, `developer.mozilla.org`
  and `docs.python.org` (7942 / 7938 / 785 / 7540 / 3708 chars respectively, all `static_http`,
  under 2.1s).
- Acceptance corpus 93 → 97 rows with one dead link removed (denominator 96): usable
  `75/96 = 78.1%` (20 readable + 55 partial), zero timeouts; the same-scope 95-row subset is
  `74/95 = 77.9%`.
- YouTube subtitles came back consistently across two runs as `video_subtitles`
  (`manual_subtitles`, 22k chars available, 11k returned with continuation), so the earlier
  `challenge_required` was a local egress problem rather than a capability gap.
- Fixed the CI static check: a duplicated `urlparse` import in `mcp.py` tripped ruff `F811` and
  failed the static-check job on `main` for both 0.1.16 and 0.1.17; the import is gone and CI is green.

## 0.1.17 - 2026-10-27

### 中文

- 新增可读站点 `ted.com`（演讲页，`static_http`，实测 502 字 / 1.5s）；`ruanyifeng.com` 的 Atom 源与 `ithome.com` 的 RSS 经 `public_api:feed` 通道读取成功，语料补 3 行后全部可用。
- 修复错误归类：浏览器导航失败（`net::ERR_CONNECTION_CLOSED`、目标被关闭这类传输层错误）以前会被报成 `auth_required`，让模型对一个根本没出登录框的站点去跑 `envoy_auth`；现在统一报 `network_error`。
- 验收语料 90 → 93 行，预检剔 1 条死链，分母 92：可用 `69/92 = 75.0%`（可读 20 + 部分 49），零超时，p50 `4.46s`；与上一轮同口径的 89 行子集 `66/92` 之外无任何一条行发生判定翻转。

### English

- `ted.com` talk pages are now readable (`static_http`, 502 chars in 1.5s), and the `ruanyifeng.com` Atom feed plus the `ithome.com` RSS both read through `public_api:feed`; three new corpus rows, all usable.
- Fixed a misclassification: a browser navigation failure (`net::ERR_CONNECTION_CLOSED`, closed target and similar transport errors) used to be reported as `auth_required`, which sent the model to `envoy_auth` for a site that never showed a sign-in wall. It now reports `network_error`.
- The acceptance corpus grew from 90 to 93 rows with one dead link removed, so the denominator is 92: usable `69/92 = 75.0%` (20 readable + 49 partial), zero timeouts, p50 `4.46s`, and not a single verdict flip among the 89 rows shared with the previous round.

## 0.1.16 - 2026-10-27

### 中文

- 新增 17 个可读站点，全部有实测正文（`static_http` 通道）：技术/开发媒体 `dev.to`、`zenn.dev`、`qiita.com`、`lwn.net`、`brendangregg.com`、`techcrunch.com`；国内媒体 `ithome.com`、`ifanr.com`、`pingwest.com`、`qbitai.com`；财经媒体 `jiemian.com`、`cls.cn`、`yicai.com`、`caixin.com`、`wallstreetcn.com`；个人博客 `ruanyifeng.com` 与图库 `commons.wikimedia.org`。
- JSON 状态桩不再冒充正文：接口只回一个 `{"status": ...}` 的空壳（快手分享链 63 字）以前被当成 `ready` + `extraction=complete` 交付，现在按 `empty_content` 拒绝，宿主不会再收到「读完了但什么都没有」。
- 平台页只剩几百字外壳时标 `details.thin_platform_page=true`，让宿主知道拿到的是壳而不是全文；同时修正一条把该语义写错的测试。
- DNS 解析失败不再被 URL 守卫吞成「不支持」，统一返回 `network_error` 与 `reader=url_guard`，不再把出口问题伪装成能力问题。
- 视频/播客链接在 MCP 侧拿到更长的默认时间预算（300s，调用方显式给的 `budget` 仍然优先），长视频字幕/ASR 不再在 180s 处被判超时；普通网页保持原默认，不吃这条预算。
- 验收语料扩到 83 行并重跑：同分母同组成的旧 72 行子集可用率 `65.3% → 68.1%`（+2 行），全语料 `59/82 = 72.0%`，随后补 7 条新行后全语料 `66/89 = 74.2%`；零超时，p50 延迟 `7.29s → 4.43s`。
- 新增回归测试：真实 JSON API 响应仍算正文（`api.github.com` 不回退），JSON 状态桩判定 `empty_content`。

### English

- Seventeen new readable hosts, each verified with real extracted text over the `static_http` path: developer media `dev.to`, `zenn.dev`, `qiita.com`, `lwn.net`, `brendangregg.com`, `techcrunch.com`; Chinese outlets `ithome.com`, `ifanr.com`, `pingwest.com`, `qbitai.com`; finance media `jiemian.com`, `cls.cn`, `yicai.com`, `caixin.com`, `wallstreetcn.com`; the `ruanyifeng.com` blog and `commons.wikimedia.org`.
- A JSON status stub no longer passes as content: an endpoint returning only `{"status": ...}` (a 63-character Kuaishou share response) used to be delivered as `ready` with `extraction=complete`; it is now rejected as `empty_content` instead of handing the host an empty successful read.
- Platform pages reduced to a few hundred characters now set `details.thin_platform_page=true`, so the host knows it received a shell rather than the article, and a test that encoded the old semantics was corrected.
- DNS resolution failures are no longer swallowed by the URL guard as "unsupported"; they return `network_error` with `reader=url_guard`, so an egress problem is not reported as a capability problem.
- Video and podcast URLs now get a longer default work budget on the MCP side (300s, and an explicit caller `budget` still wins), so long-video subtitles/ASR stop timing out at 180s; ordinary web pages keep the old default and never inherit the media allowance.
- The acceptance corpus grew to 83 rows: the identical 72-row subset moved `65.3% → 68.1%` usable (+2 rows) and the full corpus reports `59/82 = 72.0%`, reaching `66/89 = 74.2%` after seven more rows, with no timeouts and p50 latency `7.29s → 4.43s`.
- New regression tests cover a real JSON API response still counting as content (`api.github.com` no longer falls back) and a JSON status stub being rejected as `empty_content`.

## 0.1.15 - 2026-10-27

### 中文

- 登录墙不再被当成正文卖：Instagram reel、`mp.weixin.qq.com` 这类只回一屏「请登录 / Post isn't available」的链接，现在统一报 `blocked`、`auth_required`、`action_required: true`，并带 `envoy_auth` 的扫码入口；不再返回 `partial` 让宿主以为读到了内容。
- 静态 HTML 里就已经是登录墙的短页不再启浏览器，直接失败交回登录动作（`mp.weixin.qq.com/` 实测 85.9s → 29s）；带「安全验证」字样的挑战页仍先让浏览器解一次人机校验，避免误杀能过的站。
- 新增两个公开 API 回退：Hacker News 走 Firebase item API（`reader=public_api:hackernews`，正文 + 评论 2481 字）、Vimeo 走 oEmbed（标题/作者/缩略图，`backend=vimeo`）。
- `pinterest.com`、`figma.com`、`api.github.com` 三个站有实测可读结果，写进支持清单；`link-verdicts.json` 同步，JS/Python 判定一致性继续由 CI 守住。
- 修掉回退正文标题被拆成一字一行的问题（`_clean_text` 收到字符串被按字符遍历）。
- 抖音短链在只剩 400 字以内空壳时多给一次浏览器重试；验收语料第 3→4 轮配对（n=56）可用率 82.1% → 80.4%，下降的 1 条正是上面两处登录墙改判，没有真实的读取回退。

### English

- Sign-in walls are no longer sold as content: links that only return "log in to continue" / "Post isn't available" (Instagram reels, `mp.weixin.qq.com`) now report `blocked` with `auth_required` and `action_required: true`, plus the `envoy_auth` handoff, instead of a misleading `partial`.
- A static short page that is already a wall no longer boots the browser (85.9s → 29s on `mp.weixin.qq.com/`); "security verification" challenges still get one browser pass so real pages are not misjudged.
- Two new public-API fallbacks: Hacker News via the Firebase item API (`reader=public_api:hackernews`, 2481 chars incl. comments) and Vimeo via oEmbed (`backend=vimeo`).
- `pinterest.com`, `figma.com`, `api.github.com` join the supported hosts on measured reads; `link-verdicts.json` is regenerated and CI keeps the JS/Python verdicts identical.
- Fixes fallback titles being split one character per line (`_clean_text` iterating a string).
- Douyin short links get one more browser retry while the static shell stays under 400 chars; acceptance rounds 3→4 paired (n=56) usable 82.1% → 80.4%, where the single lost row is one of the two sign-in-wall corrections above.

## 0.1.14 - 2026-10-26

### 中文

- 三个宿主都能一条命令装完：`curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh|claude-code|codex`。脚本先确保 PATH 上有够新的 `envoy`（已装好且够新就直接复用），再装对应插件；Codex 的用户级 hook 也在同一条命令里写完。
- 插件市场那条命令不再依赖已发布的 npm 版本：发行版同时挂出 `envoy-dsh-<版本>.tgz`，`dsh plugin add` 可直接装这个包，和 npm 发布解耦。
- 修掉 0.1.13 的安装断点：`min_runtime` 指向的 0.1.13 wheel 从未发行（GitHub 上最新正式发行还是 0.1.9），按文档装的新用户会拿到 404；本版补上发行，并把版本号纳入自动发布检查。
- 首次读网页缺 Chromium 时自动装（`playwright install chromium`），失败才提示手动 `envoy browser setup`；`ENVOY_DISABLE_AUTO_BROWSER_SETUP=1` 可关掉自动安装。
- 安装脚本补上 `mcp` 与 `skill` 两个目标（覆盖 Claude Desktop / Cursor / Windsurf 这类只有 MCP 配置、或只认 skill 目录的宿主），下载后用发行里的 `SHA256SUMS` 校验，结束时自己跑 `envoy doctor --json` 报版本，失败停在出错的那一步。

### English

- One command per host: `curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh|claude-code|codex` provisions a current `envoy` when needed, then registers the plugin; the Codex user-level hook is installed in the same run.
- The marketplace install no longer waits on npm: each release publishes `envoy-dsh-<version>.tgz`, which `dsh plugin add` accepts directly.
- Fixes the 0.1.13 install gap: the wheel its `min_runtime` pointed at was never released, so a fresh install hit a 404. This release publishes it.
- A missing Chromium is installed on first page read (with `ENVOY_DISABLE_AUTO_BROWSER_SETUP=1` as the opt-out) instead of failing the read.
- The installer also covers `mcp` and `skill` targets (Claude Desktop, Cursor, Windsurf, and hosts that only read a skill directory), verifies downloads against the release `SHA256SUMS`, and ends with an `envoy doctor --json` version check.

## 0.1.13 - 2026-10-26

### 中文

- 新增 `envoy_auth`（`core` 工具集自带）：遇到需要登录或风控验证的链接时，它在后台浏览器里打开该站登录页，把首帧截图路径（通常是二维码）返回给模型，模型把图给用户，用户扫一次码即可，不用离开对话去终端。
  浏览器在两次调用之间保持运行，同站点一个批次里的一次扫码覆盖全部暂停；`action: "status"` 报 `signed_in` 后再用 `envoy_read` 读原链接。
- 风控页会主动改走该站的登录入口（知乎、小红书、微博、抖音），截图里是真正的登录/扫码页而不是 403 文本。
- `needs_user_action` 的选项新增 `in_chat_sign_in`，排在第一条：告诉模型用 `envoy_auth` 把二维码发出来，而不是让用户去开终端。
- CLI 等价入口：`envoy auth shot --url <link> --json`，其余参数与 MCP 工具一致。
- 视频页没有字幕、本地又没装 ASR 时，不再整条 `blocked`：回退读该页自身正文（标题、简介、分P），结果标 `partial` 并写明「没有转写，下面是页面本身」。
- 链接覆盖基线入库：57 条真实链接、14 个类别，脚本 `scripts/acceptance/link_coverage.py`，可用率 45.6% → 54.4%（可达到语料 68.4% → 81.6%），原始测量 JSON 与复测命令见 `docs/product/link-coverage-benchmark.md`。

## 0.1.12 - 2026-10-26

- `envoy_read` 的 `needs_host` 交接现在列出每份原始帧的绝对路径，并说明可以直接用宿主的图片/文件工具打开原图，不需要再回头找文件。
- 视觉交接文本与结构化载荷同时带路径：文本宿主（DSH）和 JSON 宿主（Codex、Claude Code）都能拿到同一份位置。

## 0.1.11 - 2026-10-26

### 中文

- 修复「工具结果只给回执」这一类故障：`envoy_read` / `envoy_search` / `envoy_expand`
  的 `content` 文本现在自带正文、命中片段、`material_id`、`source_revision`、资源 id 与
  应调用的下一步；`structuredContent` 同样携带完整载荷。三宿主对「哪个字段到模型」的
  结论不同（DSH 只读 `content`，Codex CLI 与 Claude Code 只读 `structuredContent`），
  两者都放回执就会在对应宿主上复现该故障，详见 `docs/product/mcp-host-content-matrix.md`。
- `envoy_search` 不再把命中的窗口写进读取交付账本，命中后再 `envoy_expand` 同一区域
  不再被「overlaps an earlier response」挡住（此前会把模型逼回重读 URL）。
- `envoy_replay` 允许省略 `source_session_id`，由保留物料记录推断；跨进程/重启后接管
  不再因为模型不知道服务端会话 id 而失败。

### English

- Fix the "receipt-only tool result" class of failure: the `content` text of
  `envoy_read` / `envoy_search` / `envoy_expand` now carries the block text,
  search hits, `material_id`, `source_revision`, resource ids, and the next call,
  and `structuredContent` carries the same full payload. Hosts disagree about
  which field reaches the model (DSH reads only `content`; Codex CLI and Claude
  Code read only `structuredContent`), so a receipt in either field reproduces
  the failure on that host. See `docs/product/mcp-host-content-matrix.md`.
- `envoy_search` no longer records hit windows in the delivery ledger, so
  `envoy_expand` of the same region is not rejected with "overlaps an earlier
  response".
- `envoy_replay` accepts an omitted `source_session_id` and derives it from the
  retained material, so taking over after a process restart no longer fails.

## 0.1.10 - 2026-10-26

### 中文

- 登录暂停成为可恢复的一步：新增 `envoy auth open|verify|resume|status|clear`。
  同一站点在一批任务里只暂停一次，恢复时用 `--retry-reason auth` 只重试被挡住的
  条目；判定只来自对原链接的无头复检，打开窗口本身不算成功。
- 长读取期间心跳续租 item lease，避免长视频在读完前被 `lease-reap` 判为超时；
  `touch_lease` 只接受处于 `processing` 的条目，不消耗重试次数。
- ASR 每 60 秒把已完成分片写入检查点；中断后从最后一片续跑，完整转写直接复用。
- 任务可发现：`envoy collect --list [--json]` 与 `envoy_collection_checkpoint`
  的 `action="list"` 列出本机可续跑的任务、完成数与待登录数。
- 工具改名 `envoy_observe_visual` → `envoy_observe`，保留一个版本的调用别名。
- 新增 `scripts/acceptance/auth_pause_resume.py`：覆盖「17 条完成、3 条因同一站点
  认证暂停、只打开一次验证窗口、只续那 3 条、退出后重开仍能找到任务」。

### English

- A login pause is now a resumable, site-scoped step (`envoy auth open|verify|resume|status|clear`).
  One site pauses once per batch; resume re-reads only the blocked items via
  `--retry-reason auth`, and success is decided by a headless re-check of the
  original URL, never by the window having opened.
- Long reads renew their item lease; `touch_lease` accepts only `processing` items
  and does not consume attempts.
- ASR persists finished chunks every 60 seconds, resumes from the last chunk, and
  reuses a complete transcript.
- `envoy collect --list [--json]` and `envoy_collection_checkpoint` `action="list"`
  report resumable runs with progress.
- Renamed `envoy_observe_visual` to `envoy_observe`, with a one-release alias.
- Added `scripts/acceptance/auth_pause_resume.py` for the 17+3 pause scenario.

## 0.1.9 - 2026-10-26

### 中文

- 安装/升级链路对齐：`INSTALL.md` 指向的正式发行版、wheel 名和 Release 链接统一为
  `0.1.9`（`hook install` 只存在于 0.1.7 及以后，旧文档却让人先装 0.1.6），
  `scripts/check_versions.py` 仍然校验文档里的版本号。
- DSH 插件启动器不再「有旧 runtime 就直接用」：每次挂载先比对 `envoy --version`
  与插件要求的最低 runtime，自备 runtime 过低时原地升级（venv 与 `~/.envoy` 分离，
  ASR/OCR 配置与已下载模型保留），`ENVOY_BIN`/PATH 上的外部 runtime 过低时直接报
  升级命令而不是静默使用不兼容 CLI。
- 新增 `integrations/release-manifest.json` 与 `scripts/check_release_manifest.py`：
  Skill/插件版本、最低兼容 runtime、协议版本写在一处，安装侧读它，发布时用
  `--emit` 生成带源码提交和产物 sha256 的清单随 Release 发布；CI 校验四处不漂移。
- 自动发布只在同一 SHA 的 CI 成功后打标签（最多等 40 分钟），并且只由 tag push
  触发一次 `release.yml`；此前 tag push 与 `workflow_call` 会各发布一次。
- 状态目录不可写（宿主沙箱、只读容器、CI 只挂只读 `$HOME`）不再让整次读取失败：
  物料根按「显式配置 → `~/.envoy` → 系统临时目录」依次探测可写落点；跨进程读锁
  拿不到时退化为不串行执行，并在 `coverage.modalities[*].details.state_lock` 标注
  `skipped` 与回退原因，已经取到的证据照常返回。
- 状态目录相关失败在 CLI 与 MCP 都改为可执行的说明：新增
  `state_not_writable`（含 `ENVOY_LOCAL_DATA_DIR=<可写目录>` 的修复命令），不再只回
  `invalid_request` 或让 MCP 客户端只能看到 `[object Object]`。
- `envoy_read` 的可见文本补齐行动信息：`partial` 时直接列出 `unresolved` 与
  `needs_user_action`（要转述的问题和两条带站点范围的选项），并让真实缺口排在
  装饰性排除项之前；请求了 `include_visual` 而该来源没有交付任何帧/图时，明确写
  「未交付画面，不要描述画面与镜头节奏」，避免模型拿封面、评论区或壳层文字补想象。
- 「取画面」的开关不再藏在服务端字段里：`envoy_read` / `envoy_read_local_video`
  的 `inputSchema` 现在声明 `host_capabilities`（`video_input:
  none|native|frames`、`image_input: none|local_file|uri`）与 `retention`
  （`raw_media: ephemeral|session|persistent`、`derived_ttl_seconds`）。请求了
  `include_visual` 却没声明帧能力时，可见文本与
  `coverage.modalities.video.details.visual_requirement` 直接给出
  `host_capabilities {video_input: frames, image_input: local_file}` 加
  `retention {raw_media: persistent}` 的原文，并写明「按这些字段重读，不要改开
  独立浏览器面板」。此前该契约不可见，模型会把「没声明能力」读成「Envoy 不能看
  视频」，转而去侧边栏开页面、撞上验证码，再拿封面帧当观察证据。
- 隔离浏览器改为**默认全程静默**：登录浮层先被关闭再判定页面状态，浮层自身文案
  （如「验证码登录」）不再被误判成安全验证；确实需要人工验证时返回
  `challenge_required`，不再自动弹窗。可见浏览器回退只属于人类自己的交互式终端：
  stdin 与 stdout 同时是 TTY 且显式设置 `ENVOY_VISIBLE_BROWSER=1`；在 agent、MCP
  宿主或任何非交互进程里一律拒绝，agent 不得自行设置该变量。
- 修复抖音类 feed 页拿不到原片：播放器起步只有 `blob:` MSE 源甚至没有源，Envoy
  现在会短暂等待真实 HTTP 片源出现（已播放的 MSE 页面立即返回），忽略
  `blob:`/`data:` 播放器内部地址，并用每个候选前 64 KiB 的 sha256 指纹把同一文件的
  镜像地址（签名 CDN + `aweme/v1/play` 跳转）归并成一个下载目标，不再把多候选
  整体拒绝或误判为“没有唯一的页面视频地址”。真机验证：抖音单条视频静默取回
  7.0 MB 原片、216 秒时长、109 段中文 ASR 与 12 个章节，全程无窗口、无人工介入。
- 新增 `envoy auth import --from chrome|edge|brave|vivaldi|chromium|safari`
  （按 `--domain` 或 `--url` 限定范围）把用户本机浏览器已登录的会话写进 Envoy
  自己的 profile，另有 `envoy auth status` 与 `envoy auth clear`。导入前由模型
  征得用户同意，不主动读 cookie；密钥从系统钥匙串取，Chromium 的 `v10/v11`
  值按 OSCrypt 解密、剥离 `SHA256(host_key)` 前缀，Safari 走 `Cookies.binarycookies`；
  落盘为 0600、只保留 45 天内的 cookie。
- 修复「封面预览顶替原片」再次复现：抖音的封面 teaser 从静态预览域
  （`*.douyinstatic.com`）自动播放，会在真播放器挂载前抢先绑定。现在等待
  真播放器时直接排除预览域，候选按实测字节数排序并用 sha256 去重，若页面只
  暴露 teaser 就**不下载**，返回 `browser_media_preview_only` 与
  `page_player_bound: false`，并附 `needs_user_action` 让模型请用户选择注入
  cookie 或一次性可见登录。连续三轮真机实测（其中一轮是全新进程、cookie 仓库为空、
  且 yt-dlp 明确报 “Fresh cookies are needed”）均为 19,604,211 字节、690.6 秒、
  354 段 ASR、6 个候选源（镜像域在 `v95-web-sz` / `v11-weba` 之间漂移），
  归并到同一文件：仅靠“点掉登录浮层”即可稳定取回原片。
- 被登录/验证挡住时，`challenge_required` 的 `details.needs_user_action`
  统一给出 `kind`（`login_or_verification`；站点只是要新鲜 cookie、不一定要账号时
  为 `cookies_required`）、`host`、要转述的 `question` 和两条人类动作
  `options`：`import_cookies`（由用户在自己的终端运行
  `envoy auth import --from chrome --domain <host>`，导入自带站点范围，授权一个
  站点不等于交出浏览器全部 cookie）与 `human_sign_in`（请用户登录/验证一次）。
  每条选项都带 `requires_human: true` 和 `instruction`，不带可直接执行的
  `command` 字段；模型据此询问并等待人类，而不是把选项当工具调用、自行打开
  窗口或设置 `ENVOY_VISIBLE_BROWSER`。三宿主 Skill 文案同步。
- 素材已下载、ASR 也跑通、但音轨里没有人声时（纯音乐、无声帖）不再返回
  `blocked` 的空回答：现在返回 `result: partial`，保留标题块与
  `page_player_bound: true`、时长，并在 `coverage.modalities.video.details.no_speech`
  与 `unresolved` 里写明「音轨无可识别语音，需要画面或换源」，模型据此告诉用户，
  而不是把可读的来源报成读取失败。
- 平台识别补齐国内内容站：B 站短链（`b23.tv`、`bili2233.cn`）、移动站
  `m.bilibili.com`、知乎问答/专栏（`article_page`）、微信公众号文章
  （`mp.weixin.qq.com`）不再落进 generic 分支；短链后续按跳转后的真实视频页处理。
- 转写质量可核验：ASR 结果新增 `speech_seconds`、`speech_coverage`、
  `transcript_confidence`。当人声占比过低（例如纯音乐），`unresolved` 会说明
  「音频主要是音乐或噪声，转写可能不可靠」。B 站实测：212 秒 MV 里 whisper
  只吐出一句 2.5 秒的幻觉台词（占比 1.2%），现在会被标成 low，而不会被当成
  全文转写直接交付。

- 新增 DeepSeek Harness 插件 `integrations/dsh/envoy-dsh`：在插件管理器中一次安装即可挂载
  `envoy mcp`（默认 `core`）、注册 Envoy Skill，并通过 `agent/pre-step` 实现链接优先路由；
  保留原有读取、发现、续读和视觉交接能力；高级工具可用 `ENVOY_MCP_TOOLSET=full` 开启。
- 新增 Claude Code 与 Codex 插件（`integrations/claude-code`、`integrations/codex`），共享
  `envoy hook prompt` 的 URL 检测、内网过滤、显式退出和失败开放策略。路由提示默认按用户
  消息自动选择中文或英文（`ENVOY_HOOK_LANGUAGE=auto`）。Claude Code 直接加载插件 hook；
  Codex 0.156 起只加载用户级 hook，需执行一次 `envoy hook install --host codex` 写入
  `~/.codex/hooks.json`。
- 该路由只处理用户给出的链接、视频/音频和需要完整来源证据的读取，**不替代**宿主自带联网
  搜索；模型遇到链接时优先用分段读取/`envoy_expand`/`envoy_search` 控制上下文。
- 媒体下载（yt-dlp）与页面读取、Chromium 统一走同一套出网策略：**默认直连**，只有直连失败且
  存在可用代理时才自动回退；`ENVOY_BROWSER_PROXY` 仍可指定代理或设为 `none/off` 禁止代理。
  此前 yt-dlp 会自行采用系统代理，导致系统代理不可用时页面能读、视频/ASR 抓取却报 SSL 握手
  超时；现在两条路径的出口一致，且失败后的回退只发生一次。
- OCR 新增 macOS Vision 与 RapidOCR 后端，默认语言改为 `auto`（中文优先、兼容英文），新增
  `envoy ocr status|test`，并支持 `ENVOY_OCR_BACKEND` / `ENVOY_OCR_LANG`。
- 新增 MCP `--toolset core|full`（环境变量 `ENVOY_MCP_TOOLSET`）：`core` 只声明
  `envoy_read`、`envoy_expand`、`envoy_search`、`envoy_observe_visual`，工具声明从约
  19.9 KB 降到约 8.2 KB。`envoy mcp` 现在默认 `core`；Claude Code、Codex、通用 Skill
  和 DSH 插件的配置统一只写 `envoy mcp`，因此 `ENVOY_MCP_TOOLSET` 是唯一的全局开关：
  在启动宿主的环境里导出 `full` 即可获得本地视频、主页发现、合集、回放、视觉历史和
  doctor。此前配置里的 `--toolset` 参数会遮蔽该环境变量，现已移除。工具集在进程启动时
  固定，避免中途换 schema 破坏缓存。
- 新增 DSH Skill 生成器与测试，将全部 MCP 工具名适配为 `mcp__envoy__*`（`core` 默认外露
  四个，`full` 暴露十个），并检查生成文件与 CLI 版本同步。
- 新增 `envoy hook status --host <claude-code|codex|dsh> [--json]`：只读地报告 hook 投递
  方式、配置文件、wrapper 是否存在、以及 Codex 是否已安装；Codex 的 `/hooks` 信任发生在
  CLI 内且不落盘，因此诚实报告为 `trusted: unknown`，不臆测。
- 新增 `scripts/check_versions.py --check`：交叉校验 10 处版本声明（两个 `pyproject.toml`、
  三个插件/市场清单、DSH bundle、Skill 打包脚本和两个测试常量）与 CLI 权威版本一致，
  并接入 CI；`scripts/sync_host_skills.py --check` 同时防止宿主技能副本漂移。
- `envoy_read` 现在直接接受本地文件路径和 `file://` URL（视频走本机 Whisper，原文件不上传），
  失败原因同时写进 `content[0].text` 与 `structuredContent.errors[0].details`，不再只回
  `source_unavailable` 而没有细节。
- MCP 读取结果的可行动句柄写进 `content[0].text`：`material_id`、`source_revision`，以及
  “用 `envoy_search`/`envoy_expand` 继续、不要重读 URL”。同一 URL 再次读取直接复用本进程
  已保留的物料（`served_from_retained_material`，不发网络请求），确实要重抓时传
  `refresh: true`；重抓遇到登录、验证或源不可用时降级为保留物料并附
  `notices[].refetch_error`，而不是返回 `blocked`。本服务不声明 MCP resources 能力，
  `list_mcp_resources` 为空是设计如此，物料只经 structuredContent 下发。
- 原始视频改为**默认不落盘**、按需保留：读请求可用
  `retention.raw_media: persistent`（配合 `derived_ttl_seconds`）把下载的视频留在
  Envoy 数据目录，同一 `source_revision` 复读直接复用这些字节（`raw_media_reused: true`）
  而不是再下一次 19.6 MB；`envoy cache status` 新增 `video_media` 计数/占用，
  `envoy cache prune` 一并清理孤儿目录。
- ASR 未就绪时不再先下载再报错：视频读取直接返回 `partial`，带
  `media_fetched: false`、`media_fetch_skipped: local_asr_unavailable` 与安装提示，
  不再为一个必然失败的转写白下整段视频。
- 新增 `envoy asr status [--json]`：报告当前模型、引擎、checkpoint 是否在盘以及下一步；
  `envoy doctor` 的五行摘要同时列出 whisper 模型、ffmpeg/ffprobe、浏览器与已导入 cookie
  数。磁盘上已有的其他 checkpoint 只在状态里列出（`asr_models_cached`），不会静默顶替
  用户显式或已持久化的模型选择。
- 长转写不再悄悄截断：超过 200 段时结果带 `transcript_segments_returned`、
  `transcript_truncated: true`，并在 `unresolved` 里给出从哪一秒继续读
  （`scope.start_seconds`），而不是看起来像完整转写。
- 抽帧时间点钳制在视频时长内（`-ss` 不超过 `duration - 0.05`），片尾请求不再返回空帧。
- 页面章节列表可能因页面未加载完而偏短：当最后章节早于时长的 90% 时，结果带
  `chapters_incomplete: true` 与 `page_chapter_span`，避免把半截章节当成完整目录。
- Windows cookie 导入补齐：从 `Local State` 的 `os_crypt.encrypted_key` 经 DPAPI 取
  主密钥，`v10` 值按 AES-256-GCM 解密（纯 Python 实现，不引入新依赖）；Chrome 127+
  的 `v20` app-bound 值需要浏览器提权服务，明确失败并指向 `ENVOY_VISIBLE_BROWSER=1`，
  不再笼统报“无法解密”。该路径以 NIST/McGrew 向量与注入式 DPAPI 单测验证，尚未在
  真实 Windows 主机上跑过。
- `envoy_read` 的工具描述现在声明高级块（`budget`、`retention`、`refresh`），
  `envoy_observe_visual` 声明 `session_id`，模型不必猜；JSON schema 仍只保留必填项，
  避免工具声明挤占每次请求的前缀上下文。
- `challenge_required` / `auth_required` 现在如实标注 `retryable: true`，并在剩余预算
  不少于 5 秒时内部重试一次（`internal_retry_attempts`），用于站点抖动后自行恢复；
  仍不绕过验证。
- 页面本身已报出登录/验证墙时，回退浏览器即使起不来也保持 `challenge_required`，
  不再降级成 `missing_capability`：要人做的事没有因为本机缺浏览器而消失，运行时
  失败原因写进消息。
- 页面视频绑定不再“看到播放器就用第一个地址”：`envoy_read` 依次用房源 JSON-LD
  `contentUrl`、`og:video`、播放器状态、主播放器 `currentSrc` 与 URL 身份确认候选，
  字节数只在已归因的镜像之间排序；无法唯一确认时**不下载**，返回
  `media_selection: "unconfirmed"` 并把视觉选择交回模型。候选探测跟随重定向
  （最多 5 跳、每跳最多读 64 KiB），并把已耗字节计入下载预算。
- 长转写按共享预算分段交付：新增 `transcript_processed_until_seconds`、
  `transcript_delivered_until_seconds`、`transcript_segment_count`、
  `transcript_unprocessed_seconds`、`continuation_available` 与
  `transcript_continuation_start_seconds`，可以从没读到的秒数继续，而不是把截断
  当成完整转写。
- 保留物料缓存修复：被拦截的重读不再把错误写进缓存，而是回退到已有物料并带
  `refetch_error`；`PublicPageError` 保留 `needs` 与 `request_id`；OCR 比较改为
  对称；URL fragment 进入缓存键；`visual_observation` 不再被普通读取冒充；只有
  `refresh` 严格为 `true` 才强制刷新；`compact` 成为独立缓存维度。
- cookie 域范围改用公共后缀私有表与 registrable domain 规则（含 20 个托管后缀）：
  `www.ck`、`city.kawasaki.jp`、`*.compute.amazonaws.com` 这类主机按站点匹配而不是
  按后缀匹配；单标签主机只接受 `localhost` 与 IP 字面量，其余一律 fail-closed；
  cookie 导入失败的提示改成「请人类在自己的终端登录一次」，不再给可直接执行的
  环境变量命令。
- 评测口径修正：评级默认只用终端答案并给每行写 `answer_source`，transcript 回收
  文本需显式 `INCLUDE_RECOVERED=1` 且只作诊断；引用只统计文章级
  `citation_hits`，host 归属降级为诊断列；facts 阈值与 516 的重复计数写进所有表
  脚注；token 对比只给倍数并写明「envoy 更贵」，不再出现负百分比形式的「节省」。
  验收记录与产物按重算结果重写，撤回 `81/516`、`29 vs 42`、`95/120` 等旧数字。

### English

- Install and upgrade paths now agree: `INSTALL.md` names release `0.1.9` in its
  prose, wheel name, and Release link, because `envoy hook install` only exists
  from 0.1.7 on while the document used to install 0.1.6 first.
- The DSH bundle launcher no longer accepts any existing runtime: on every mount
  it compares `envoy --version` with the bundled minimum, upgrades its private
  venv in place when the runtime is older (the venv is separate from `~/.envoy`,
  so ASR/OCR settings and downloaded models are kept), and fails with the
  upgrade command when `ENVOY_BIN` or a PATH runtime is too old.
- `integrations/release-manifest.json` plus `scripts/check_release_manifest.py`
  declare the Skill/plugin version, the minimum compatible runtime, and the
  protocol versions in one place; installers read them, `release.yml` emits the
  published copy with the source commit and artifact sha256 values, and CI fails
  on drift.
- Auto release tags only after CI succeeds on the same SHA (up to 40 minutes of
  waiting) and lets the tag push be the single `release.yml` trigger; the tag
  push and the `workflow_call` used to publish each release twice.
- An unwritable state directory (host sandbox, read-only container, CI mounting
  `$HOME` read-only) no longer fails the read: the material root probes the
  configured path, then `~/.envoy`, then the system temp directory; an
  unavailable cross-process lock degrades to an unserialized read and says so
  in `coverage.modalities[*].details.state_lock`, while the evidence already
  gathered is still returned.
- State-directory failures now name the fix instead of returning
  `invalid_request` or letting an MCP client see `[object Object]`: the CLI
  command carries an `ENVOY_LOCAL_DATA_DIR=<writable dir>` retry, and the MCP
  payload returns `state_not_writable` with a `requires_human` instruction to
  restart the host process against that directory.
- The visible `envoy_read` text carries the action on a `partial` result: the
  `unresolved` reasons and `needs_user_action` question plus its site-scoped
  options,
  with real gaps ahead of cosmetic exclusions. When `include_visual` was
  requested and the source delivered no frames or images, the text says so
  explicitly, so a cover still or a comment section is not rewritten into an
  observed visual style.
- The switch that unlocks frames is now declared, not hidden: the
  `envoy_read` / `envoy_read_local_video` `inputSchema` lists
  `host_capabilities` (`video_input: none|native|frames`, `image_input:
  none|local_file|uri`) and `retention` (`raw_media: ephemeral|session|persistent`,
  `derived_ttl_seconds`). When a read requests `include_visual` without declaring
  frame capability, the visible text and
  `coverage.modalities.video.details.visual_requirement` carry the exact
  `host_capabilities {video_input: frames, image_input: local_file}` plus
  `retention {raw_media: persistent}` request, with the instruction to re-read
  with it instead of opening a separate browser panel. The contract used to be
  invisible, so a model read "no capability declared" as "Envoy cannot see
  video", opened the page in its own browser, hit a captcha, and treated the
  cover still as observed evidence.
- Reading in the isolated browser is now **silent by default**: a dismissible
  login dialog is cleared before the page state is judged, so the dialog's own
  wording (for example a `验证码登录` tab) is no longer mistaken for a captcha.
  A page that genuinely needs verification returns `challenge_required`
  instead of opening a window; visible browser mode belongs to a human's own
  interactive terminal only (stdin and stdout must both be a TTY plus an
  explicit `ENVOY_VISIBLE_BROWSER=1`) and is refused in an agent, MCP host, or
  any non-interactive process, so an agent must never set that variable.
- When the page itself already returned a login or verification wall, a fallback
  browser that cannot start no longer downgrades the result to
  `missing_capability`: the human step is still there, and the runtime failure is
  named in the message.
- Fix original-video capture on feed pages such as Douyin: the player may start
  with only a `blob:` MSE source or none at all. Envoy now waits briefly for a
  real HTTP source (returning immediately when the page is already playing
  MSE), ignores `blob:`/`data:` player internals, and groups mirror URLs of one
  file (a signed CDN link plus an `aweme/v1/play` redirect) by the sha256 of
  their first 64 KiB, so one post yields one download target instead of a
  rejected multi-candidate or ambiguous set. Verified against the live site:
  one Douyin post returned a 7.0 MB source file, 216 seconds of duration, 109
  Chinese ASR segments, and 12 chapters with no window and no user action.
- Add `envoy auth import --from chrome|edge|brave|vivaldi|chromium|safari`
  (scoped by `--domain` or `--url`) to copy a session the user already signed in
  to in their own browser into Envoy's profile, plus `envoy auth status` and
  `envoy auth clear`. The model asks before importing and never reads cookies
  on its own; the key comes from the system keychain, Chromium `v10/v11` values
  are OSCrypt-decrypted with the `SHA256(host_key)` prefix stripped, Safari is
  read from `Cookies.binarycookies`, and the store is written 0600 with cookies
  older than 45 days dropped.
- Fix the cover-teaser regression that replaced the real post: Douyin's cover
  teaser autoplays from the static preview stack (`*.douyinstatic.com`) and can
  bind before the real player mounts. The wait now excludes preview hosts,
  candidates are ranked by probed byte size and deduped by sha256, and a page
  that exposes only the teaser is **never downloaded** — it returns
  `browser_media_preview_only` with `page_player_bound: false` and a
  `needs_user_action` block instead. Three consecutive live runs — one of them a
  fresh process with an empty cookie store, where yt-dlp itself demanded
  "Fresh cookies (not necessarily logged in)" — each produced 19,604,211 bytes,
  690.6 seconds, 354 ASR segments and 6 candidates, with the mirror moving
  between the `v95-web-sz` and `v11-weba` CDNs for the same file. Dismissing the
  login dialog is enough; no cookie import is needed for this class of URL.
- A login or verification wall now carries a structured
  `details.needs_user_action`: the `kind` (`login_or_verification`, or
  `cookies_required` when a site only wants fresh cookies rather than an
  account), the `host`, a `question` to relay, and two human-action `options`:
  `import_cookies` (the user runs
  `envoy auth import --from chrome --domain <host>` in their own terminal; the
  import carries the site scope, so granting one site never means handing over
  the whole browser's cookies) and `human_sign_in` (the user signs in or passes
  verification once). Every option carries `requires_human: true` and an
  `instruction`, never a runnable `command`, so the host model asks and waits
  instead of calling an option, opening a window, or setting
  `ENVOY_VISIBLE_BROWSER` itself. The Claude Code, Codex, and DSH skill wording
  is synced with the same behavior.
- A source that downloads and transcribes but whose audio holds no speech (a
  music clip or a silent post) no longer returns an empty `blocked` answer: it
  returns `result: partial` with the title block, `page_player_bound: true`, the
  duration, `coverage.modalities.video.details.no_speech`, and an `unresolved`
  line saying the audio carries no detectable speech and frames or another
  source would be needed — the model reports that instead of calling a readable
  source a failed read.
- Chinese content sites are classified instead of falling through to generic:
  Bilibili share links (`b23.tv`, `bili2233.cn`), the mobile host
  `m.bilibili.com`, Zhihu answers and columns (`article_page`), and WeChat
  official-account articles (`mp.weixin.qq.com`). Share links are followed, then
  handled as the video page they resolve to.
- Transcription quality is now checkable: ASR responses carry
  `speech_seconds`, `speech_coverage`, and `transcript_confidence`, and an
  `unresolved` line explains a low value. On a live 212 s Bilibili music video
  whisper emitted a single 2.5 s hallucinated line (1.2% of the clip); that is
  now marked low instead of being delivered as the transcript.

- Route media downloads (yt-dlp) through the same outbound policy as page reads
  and Chromium: connect directly by default and fall back to a proxy only after
  a direct attempt fails. `ENVOY_BROWSER_PROXY` still pins a proxy or disables
  proxying with `none`/`off`. yt-dlp previously adopted the operating-system
  proxy on its own, so with an unreachable proxy a page could be read while its
  video/ASR capture failed with an SSL handshake timeout.
- Add MCP `--toolset core|full` (also `ENVOY_MCP_TOOLSET`): `core` declares only
  `envoy_read`, `envoy_expand`, `envoy_search`, and `envoy_observe_visual`,
  reducing resident tool declarations from about 19.9 KB to about 8.2 KB.
  Bare `envoy mcp` now defaults to `core`. Every bundled config (Claude Code,
  Codex, the generic skill host, and the DSH bundle) launches plain
  `envoy mcp`, so `ENVOY_MCP_TOOLSET` is the single global switch: export
  `full` in the environment that starts the host to add local video, profile
  discovery, collections, replay, visual history, and doctor. A `--toolset`
  argument in a bundled config used to shadow that variable and has been
  removed. The toolset stays fixed at process startup so schemas never change
  mid-session.
- Add the `integrations/dsh/envoy-dsh` DeepSeek Harness bundle: one Plugin
  Manager install mounts plain `envoy mcp` (core default), registers the Envoy
  skill, and enables link-first routing through `agent/pre-step` while
  preserving reading, discovery, expansion, and visual handoff behavior;
  advanced tools remain available with `ENVOY_MCP_TOOLSET=full`.
- Add Claude Code and Codex plugins (`integrations/claude-code`,
  `integrations/codex`) sharing the `envoy hook prompt` URL detection,
  private-address filtering, explicit opt-out, and fail-open policy. The notice
  language defaults to `auto` (Chinese prompt → `zh`, otherwise `en`). Claude
  Code loads plugin hooks directly; Codex 0.156+ only loads user-level hooks,
  so run `envoy hook install --host codex` once to write
  `~/.codex/hooks.json`.
- Routing only covers user-supplied links, video/audio, and reads that need
  full-source evidence. It does **not** replace the host's web search; the
  model uses bounded reads plus `envoy_expand` / `envoy_search` to control
  context.
- Add macOS Vision and RapidOCR OCR backends, default the language to `auto`
  (Chinese first, English compatible), add `envoy ocr status|test`, and support
  `ENVOY_OCR_BACKEND` / `ENVOY_OCR_LANG`.
- Add a DSH skill generator and tests that adapt every MCP tool name to
  `mcp__envoy__*` (four exposed by `core`, all ten under `full`) and keep
  generated files in sync with the CLI version.
- `envoy_read` now accepts a local file path or `file://` URL directly (video
  goes through on-device Whisper; the original file is never uploaded), and a
  failure carries its reason in both `content[0].text` and
  `structuredContent.errors[0].details` instead of a bare `source_unavailable`.
- Every read result carries the actionable handle in `content[0].text` —
  `material_id`, `source_revision`, and "continue with `envoy_search` /
  `envoy_expand`, do not re-read the URL" — so a host that drops
  `structuredContent` can still continue. Reading the same URL again answers
  from the material retained in the process
  (`served_from_retained_material`, no network call); pass `refresh: true` for
  a deliberate new read, and a refresh that hits sign-in, a challenge, or a
  source error falls back to the retained material with a `refetch_error`
  notice instead of `blocked`. This server declares no MCP resources
  capability, so an empty `list_mcp_resources` is by design; material only
  travels in `structuredContent`.
- Downloaded source video is now **not kept by default**, and retention is
  explicit: a read may pass `retention.raw_media: persistent` (with
  `derived_ttl_seconds`) to keep the file under Envoy's data root, where a repeat
  read of the same `source_revision` reuses those bytes
  (`raw_media_reused: true`) instead of downloading the same 19.6 MB again.
  `envoy cache status` reports a `video_media` category and `envoy cache prune`
  removes orphaned media directories.
- A missing ASR runtime no longer costs a download first: the video read returns
  `partial` immediately with `media_fetched: false`,
  `media_fetch_skipped: local_asr_unavailable`, and the install hint, instead of
  fetching a whole video for a transcription that cannot run.
- Add `envoy asr status [--json]`: the model in use, the engine, whether the
  checkpoint is on disk, and the next step; `envoy doctor` now prints a five-line
  summary of the Whisper model, ffmpeg/ffprobe, browser setup, and imported
  cookie count. Other checkpoints already on disk are listed
  (`asr_models_cached`) but never silently replace an explicit or persisted
  model selection.
- Long transcripts no longer truncate silently: beyond 200 segments the result
  carries `transcript_segments_returned` and `transcript_truncated: true`, and
  `unresolved` names the second to continue from (`scope.start_seconds`) instead
  of leaving a partial transcript looking complete.
- Frame seeks are clamped inside the reported duration (`-ss` never exceeds
  `duration - 0.05`), so a tail frame request no longer comes back empty.
- A page chapter list can be short because the page has not finished loading:
  when the last chapter ends before 90% of the duration the result carries
  `chapters_incomplete: true` and `page_chapter_span`, so a half list is not
  presented as the full table of contents.
- Windows cookie import is implemented: the master key is unwrapped with DPAPI
  from `os_crypt.encrypted_key` in `Local State`, and `v10` values are decrypted
  as AES-256-GCM (pure Python, no new dependency). Chrome 127+ `v20` app-bound
  values need the browser elevation service, so they fail explicitly and point
  at `ENVOY_VISIBLE_BROWSER=1` instead of reporting a generic decryption error.
  This path is covered by NIST/McGrew vectors and injected DPAPI unit tests; it
  has not yet run on a real Windows host.
- The `envoy_read` tool description now declares the advanced blocks (`budget`,
  `retention`, `refresh`) and `envoy_observe_visual` declares `session_id`, so a
  model need not guess; the JSON schema still declares only required fields so
  the per-request tool prefix stays small.
- Page-video binding no longer takes the first address that looks like a player:
  `envoy_read` confirms candidates through feed JSON-LD `contentUrl`,
  `og:video`, player state, the main player's `currentSrc`, and URL identity,
  and ranks byte size only among mirrors already attributed to one file. When
  it cannot confirm one file it **does not download**, returns
  `media_selection: "unconfirmed"`, and hands the visual choice back to the
  model. Probing follows redirects (at most 5 hops, at most 64 KiB per hop) and
  charges the bytes read to the download budget.
- Long transcripts are delivered in shared-budget slices:
  `transcript_processed_until_seconds`, `transcript_delivered_until_seconds`,
  `transcript_segment_count`, `transcript_unprocessed_seconds`,
  `continuation_available`, and `transcript_continuation_start_seconds` let a
  caller resume from the second it has not read instead of mistaking a
  truncated transcript for a complete one.
- Retained-material cache fixes: a blocked refetch no longer poisons the cache
  and falls back to the existing material with `refetch_error`;
  `PublicPageError` keeps `needs` and `request_id`; OCR comparison is
  symmetric; the URL fragment is part of the cache key; a `visual_observation`
  is not passed off as a plain read; only `refresh` strictly equal to `true`
  forces a refetch; `compact` is an independent cache dimension.
- Cookie domain scope now uses the public-suffix private list and registrable
  domains (20 hosting suffixes), so hosts such as `www.ck`,
  `city.kawasaki.jp`, and `*.compute.amazonaws.com` match by site rather than
  by suffix; single-label hosts accept only `localhost` and IP literals, and
  everything else fails closed. Cookie-import errors now ask the human to sign
  in once in their own terminal instead of printing a runnable environment
  variable command.
- Evaluation methodology fixes: grading uses terminal answers only, stamps
  `answer_source` on every row, and admits transcript-recovered text only
  behind an explicit `INCLUDE_RECOVERED=1` diagnostic switch; citations count
  only article-level `citation_hits` while host attribution is a diagnostic
  column; every table footnote states the facts threshold and the duplicated
  516 count; token comparisons report a multiplier and say Envoy is more
  expensive, never a negative percentage that reads as savings. The acceptance
  record and artifacts are regenerated from the recomputed run, withdrawing
  `81/516`, `29 vs 42`, `95/120`, and the other superseded numbers.

**Known boundaries**

- Codex 0.156+ ignores plugin-supplied lifecycle hooks; the plugin provides
  MCP only, and the hook must be installed at user level.
- Vision OCR is macOS-only; RapidOCR/PaddleOCR require optional extras, and
  PaddleOCR/RapidOCR paths are mock-tested rather than validated on every
  platform.

## 0.1.6 - 2026-10-22

### 中文

- 原创代码与文档采用 Apache-2.0；发行包包含 `LICENSE` 和 `NOTICE`。
- 网络、依赖、登录或验证恢复后，允许使用同一 operation key 重试可恢复的读取；已完成材料与视觉交接可复用。
- 增加实验性小红书图文/分享链接路由，保留图集来源、顺序，并明确未知总量。
- 扩展常见平台 URL 路由与 headless 回退；路由可识别不代表平台已真实验收，也不代表绕过站点限制。
- 结构化报告 HTTP 404，不将其直接推断为删除或封锁；仅使用用户配置或明确认可的可信代理。
- 将 Agent 安装入口前置，说明 Skill、runtime、MCP 的分步配置，并增加注意脱敏的反馈表单。

**已知边界**

- Whisper 与 FFmpeg 为可选依赖；缺少 ASR 或只有稀疏帧时，不能视为完整视频证据。本版未新增平台真实验收声明。
- 抖音主页发现为实验能力：规范作者主页、最多保存 100 个候选、快照内分页；不支持日期范围检索，批量预算由宿主记账。
- 独立 runtime 暂不支持扫描版 PDF OCR。
- v1.0 前仍需重复验证全新安装和真实链接、ASR 恢复流程，并完成宿主实际用量对比。

### English

- Adopt Apache-2.0 for original code and documentation; include `LICENSE` and
  `NOTICE` in distributions.
- Retry recoverable reads with the same operation key after network,
  dependency, login, or verification recovery; reuse completed materials and
  visual handoffs.
- Add experimental Xiaohongshu image-note/share routing with source-bound
  ordering and explicit unknown-total coverage.
- Expand common platform URL routing and headless fallback. URL routing does
  not imply real-world acceptance or bypass site restrictions.
- Return structured HTTP 404 diagnostics without assuming deletion or
  blocking. Use only configured or explicitly approved trusted proxies.
- Put Agent installation first, clarify separate Skill/runtime/MCP setup, and
  add redaction-aware feedback forms.

**Known boundaries**

- Whisper and FFmpeg are optional. Missing ASR or sparse frames are not full
  video evidence. This release adds no new real-platform acceptance claims.
- Douyin discovery is experimental: canonical author URLs, up to 100 saved
  candidates, snapshot pagination, no date-range query, and host-accounted
  batch budgets.
- Scan-only PDF OCR is unavailable in the standalone runtime.
- v1.0 readiness still requires repeatable fresh-install and real-link
  acceptance, ASR recovery validation, and measured host-usage comparisons.
