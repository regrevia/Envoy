# 更新日志 / Changelog

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
