# 更新日志 / Changelog

## Unreleased

### 中文

- 隔离浏览器改为**默认全程静默**：登录浮层先被关闭再判定页面状态，浮层自身文案
  （如「验证码登录」）不再被误判成安全验证；确实需要人工验证时返回
  `challenge_required`，不再自动弹窗。只有显式设置 `ENVOY_VISIBLE_BROWSER=1`
  才启用可见浏览器回退。
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
  cookie 或一次性可见登录。连续两轮真机实测均为 19,604,211 字节、
  690.6 秒、354 段 ASR（镜像域为 `v95-web-sz` / `v11-weba` 两个不同 CDN，
  归并到同一文件）。
- 被登录/验证挡住时，`challenge_required` 的 `details.needs_user_action`
  统一给出 `kind`（`login_or_verification`；站点只是要新鲜 cookie、不一定要账号时
  为 `cookies_required`）、`host`、要转述的 `question` 和两条可直接执行的
  `options`（`envoy auth import --from chrome --domain <host>` 与
  `ENVOY_VISIBLE_BROWSER=1`）。导入命令自带站点范围，授权一个站点不等于交出
  浏览器全部 cookie；模型据此询问用户，而不是自行猜测或静默失败；三宿主
  Skill 文案同步。

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

### English

- Reading in the isolated browser is now **silent by default**: a dismissible
  login dialog is cleared before the page state is judged, so the dialog's own
  wording (for example a `验证码登录` tab) is no longer mistaken for a captcha.
  A page that genuinely needs verification returns `challenge_required`
  instead of opening a window; visible browser mode now requires
  `ENVOY_VISIBLE_BROWSER=1`.
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
  `needs_user_action` block instead. Two consecutive live runs each produced
  19,604,211 bytes, 690.6 seconds, and 354 ASR segments, with the mirror moving
  between the `v95-web-sz` and `v11-weba` CDNs for the same file.
- A login or verification wall now carries a structured
  `details.needs_user_action`: the `kind` (`login_or_verification`, or
  `cookies_required` when a site only wants fresh cookies rather than an
  account), the `host`, a `question` to relay, and two runnable `options`
  (`envoy auth import --from chrome --domain <host>` and
  `ENVOY_VISIBLE_BROWSER=1`). The import option carries the site scope, so
  granting one site never means handing over the whole browser's cookies; the
  host model asks the user rather than guessing or failing silently. The Claude
  Code, Codex, and DSH skill wording is synced with the same behavior.

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
