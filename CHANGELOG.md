# 更新日志 / Changelog

## Unreleased

### 中文

- 新增 DeepSeek Harness 插件 `integrations/dsh/envoy-dsh`：在插件管理器中一次安装即可挂载
  `envoy mcp`、注册 Envoy Skill，并通过 `agent/pre-step` 实现链接优先路由；保留原有读取、
  发现、续读和视觉交接能力。
- 新增 Claude Code 与 Codex 插件（`integrations/claude-code`、`integrations/codex`），共享
  `envoy hook prompt` 的 URL 检测、内网过滤、显式退出和失败开放策略。路由提示默认按用户
  消息自动选择中文或英文（`ENVOY_HOOK_LANGUAGE=auto`）。Claude Code 直接加载插件 hook；
  Codex 0.156 起只加载用户级 hook，需执行一次 `envoy hook install --host codex` 写入
  `~/.codex/hooks.json`。
- 该路由只处理用户给出的链接、视频/音频和需要完整来源证据的读取，**不替代**宿主自带联网
  搜索；模型遇到链接时优先用分段读取/`envoy_expand`/`envoy_search` 控制上下文。
- OCR 新增 macOS Vision 与 RapidOCR 后端，默认语言改为 `auto`（中文优先、兼容英文），新增
  `envoy ocr status|test`，并支持 `ENVOY_OCR_BACKEND` / `ENVOY_OCR_LANG`。
- 新增 DSH Skill 生成器与测试，将十个 MCP 工具名适配为 `mcp__envoy__*`，并检查生成文件与
  CLI 版本同步。

### English

- Add the `integrations/dsh/envoy-dsh` DeepSeek Harness bundle: one Plugin
  Manager install mounts `envoy mcp`, registers the Envoy skill, and enables
  link-first routing through `agent/pre-step` while preserving reading,
  discovery, expansion, and visual handoff behavior.
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
- Add a DSH skill generator and tests that adapt the ten MCP tools to
  `mcp__envoy__*` and keep generated files in sync with the CLI version.

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
