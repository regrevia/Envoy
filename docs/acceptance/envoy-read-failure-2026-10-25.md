# DSH 会话 `session-915d8ef8` 的读取失败与幻觉复盘（2026-10-25）

会话标题「Envoy分析AI视频制作与SVG技术」，工作目录 `/Users/apple/Desktop/DSH`，
权限 `workspace-write`。本文只记录可从artifact复核的事实，以及据此做出的修复。

## 1. 现象

同一会话里 Envoy 失败三次，形态各不相同：

| 轮次 | 调用 | 模型实际看到 |
| --- | --- | --- |
| 1 | MCP `envoy_read`，`https://v.douyin.com/ncJzPGyPDIA/`，`include_visual: true` | `Error: [object Object]`，`isError: true` |
| 2 | 同上重试 | `Envoy read partial: 151 text characters, 2 blocks, 0 resources. material_id=mat-eee94267a98250978caaf9dc`（没有任何原因或动作） |
| 3 | CLI `envoy read --request-json /tmp/envoy-dy/req.json` | `{"error": "invalid_request", "message": "[Errno 1] Operation not permitted: '/Users/apple/.envoy/materials/.read-6f.lock'"}` |

随后模型产出的 `motion-video/README.md` 里写着「封面帧 ✅ 看到」「评论区 32 条可见」，
但 Envoy 交付物里两者都不存在。

## 2. 环境事实

- 该会话的 shell 在 `workspace-write` 沙箱内，`~/.envoy` 不可写：锁文件创建返回 `EPERM`。
- 保留物料 `~/.envoy/materials/mat-eee94267a98250978caaf9dc.json` 可复核：
  `text` 只有 `精选 推荐 AI抖音 … 2026 © 抖音 京ICP备16016397号-3 视频数据加载中`，
  `title_text` 等于 URL，`visual_resources: []`，`returned_chars: 75`，
  `source_coverage_unresolved` 写着「The isolated browser read the video page only;
  no transcript or video-frame inspection was completed」。
- 该次读取命中的是抖音壳层页面，播放器没有绑定真实片源（登录/验证墙）；
  这是平台限制，Envoy 无法绕过，也不该假装读到。
- 视频身份本身是对的：短链跳到 `https://www.douyin.com/video/7697696177046736143`，
  标题「GPT-6.1加了skill，还是和Opus5.5差距很大」，作者「泽轩604」。

## 3. 根因（三个独立缺陷，都在调用方可见的契约上）

1. **状态目录不可写 = 整次读取失败。** `_material_root()` 直接 `mkdir` 配置路径或
   `~/.envoy/materials`，`EPERM` 一路冒到 CLI 变成 `invalid_request`；MCP 分支只把
   未预期异常变成不透明 JSON-RPC 错误，客户端渲染成 `[object Object]`。宿主沙箱
   把 `$HOME` 设为只读是常见部署，不该等同于「这份来源读不了」。
2. **失败与部分结果没有行动信息。** MCP 的可见文本只报字符数、块数和 `material_id`，
   不带 `unresolved` 原因，也不带 `needs_user_action`；CLI 只给 errno。模型没有
   「播放器未绑定，需要导入 cookie 或可见登录」这句话，于是自己补了一个故事。
3. **没有画面时没有禁止声明。** 请求了 `include_visual`、来源是视频，但
   `resources: []`；文本里没有任何句子告诉模型「这次没有交付帧或图，不要描述画面」。
   封面 teaser 与评论区属于模型侧的既有知识/侧栏内容，缺了这句约束就被写成了观察结果。

## 4. 「串上下文」结论：不是记忆插件

插件名 `dsh-unlag`、`lite-memory`、`CreatorLab`、`dsh-video-preview` 第一次出现在
该会话自己的工具结果里：

- event 69：`ls -la /Users/apple/Desktop/DSH`（tool call 66）列出这些目录；
- event 76：`find /Users/apple/Desktop/DSH/{CreatorLab,dsh-video-preview}`；
- event 73 的 reasoning 明说「There is `dsh-video-preview` and `CreatorLab` dirs in
  workspace — interesting, maybe prior work」。

全量 446 条事件里没有任何记忆插件注入或检索记录。这是同一个工作目录被扫描后，
模型把邻居项目的介绍当成「本任务已有产出」延续下去，属于会话内自我引入，
不是 `lite-memory`（或任何记忆插件）串了上下文。

## 5. 修复（`7f5c0ad`）

- `envoy_cli/storage.py`（新）：物料根按「显式配置 → `~/.envoy` → 系统临时目录」
  依次探测可写落点，写探针失败即换下一个，并记录回退原因（`state_not_writable`）。
  `cache.py`、`operations.py`、`collections.py`、`reader.py` 全部改走该解析器。
- `media.optional_interprocess_lock`：`EACCES/EPERM/EROFS/ENOTDIR/ENOSPC/EDQUOT`
  时降级为「不串行执行」，其余异常照旧抛出。
- `reader._annotate_unserialized_read`：未持锁时在
  `coverage.modalities[*].details.state_lock = "skipped"` 写明事实与回退记录，
  已取到的证据照常返回，而不是整单失败。
- CLI/MCP 都把锁与状态目录错误归类为 `state_not_writable`，附可执行命令
  `ENVOY_LOCAL_DATA_DIR=<可写目录> envoy read|mcp`；其余内部异常在 MCP 上仍保持
  不透明（不泄漏内部诊断），只有这类宿主事实被解释。
- `mcp._tool_content_text`：`partial` 结果直接带 `unresolved` 与
  `needs_user_action`（问题原文 + 带站点范围的命令），真实缺口排在装饰性排除项之前；
  请求了视觉但没有交付帧/图时追加「No frames or images were delivered; do not
  describe on-screen visuals or shot rhythm as if they were observed.」

## 6. 验证

- `tests/cli/test_state_write_fallback.py`（11 项）覆盖：只读配置目录 + 只读
  `$HOME` 时回退到可写根、锁不可用时降级并在 details 标注、MCP partial 文本含
  行动句、纯文本读取不会被误加「无画面」声明、CLI/MCP 的 `state_not_writable`
  出口与修复命令。
- 真机复核：`ENVOY_LOCAL_DATA_DIR` 指向只读目录读同一条抖音短链，返回
  `result: partial`、`page_player_bound: false`、`needs_user_action.kind ==
  login_or_verification`，MCP 可见文本包含「Requires user action … envoy auth import
  --from chrome --domain douyin.com」与「No frames or images were delivered」。
- 全量 `tests/cli` 438 项通过；`ruff`、插件构建、宿主技能同步、版本一致性检查均通过。

## 7. 未解决与边界

- 抖音这条视频当前仍被登录/验证墙挡住播放器，Envoy 只能如实报
  `partial` + 需要用户动作，不能凭空取到原片；需要用户授权导入该站 cookie 或做一次
  可见登录。
- `needs_user_action` 的「仅封面 preview」分支目前只有单测覆盖，没有真实命中的样本。
- 2026-10-25 的 A/B 数据里，envoy 臂上限从 900 秒放宽到 2400 秒以容纳合法的长任务，
  baseline 仍为 900 秒；这是口径偏差，报告里按 `timed_out` 单独披露，不当作胜负。

## 8. 追加（同一会话的根因更正）

用户后来用同一条视频、同一台机器拿到帧并留在 README 里。本次修复后又用 CLI
复跑了一遍（`envoy read --request-json`，请求体写全 `scope.include_visual: true` +
`host_capabilities {video_input: "frames", image_input: "local_file"}` +
`retention {raw_media: "persistent"}`）：`media_fetched: true`、
`downloaded_bytes: 7,789,125`、`duration_seconds: 56.125`、5 个页面候选归并到
1 个片源、`frame_count: 6`（每帧 1080×1920，时间点 4.68/14.03/23.39/32.74/42.09/
51.45 s，落盘为 `~/.envoy/materials/frames-.../frame-00N.jpg`）、原片以
`raw_media_retention: persistent` 留在本地。yt-dlp 仍报 "Fresh cookies are needed"，
但页面自己暴露的签名片源可用，浏览器捕获路径照常取到原片。也就是说
第 3 轮之后「Envoy 取不到画面」的结论**不成立**，真正的阻塞是调用方契约：

- MCP `envoy_read` 的 `inputSchema` 当时没有声明 `host_capabilities` 与
  `retention`（只在服务端注释里写着「高级字段，正常使用不必发送」），模型无从
  得知「不声明能力 = 帧被门控」，于是把「只回文本」读成「Envoy 看不了视频」。
- 失败文本只给出 `Envoy read partial: 151 text characters, 2 blocks, 0 resources`
  和 `[object Object]`，既没写缺哪个字段，也没写可重试的请求；
  `coverage.modalities[*].details` 里虽有 `unresolved`，但宿主没转发 structuredContent。
- 于是模型改用主机自己的侧边栏浏览器打开页面（撞上验证码中间页），再把封面
  teaser/壳层文字当成观察证据——幻觉发生在这一步，不是 Envoy 抽帧失败。

对应修复（本次提交）：

- `mcp.py`：`READ_SCHEMA` / `LOCAL_VIDEO_SCHEMA` 声明 `host_capabilities`
  （`video_input: none|native|frames`、`image_input: none|local_file|uri`）与
  `retention`（`raw_media`、`derived_ttl_seconds`）；工具描述写明「要帧就三个字段
  一起发，只回文本时按回复里的字段重发，不要另开浏览器面板」。
- `reader.py`：未声明能力时在 `details.visual_requirement` 写出
  `host_capabilities {video_input: frames, image_input: local_file}` +
  `retention {raw_media: persistent}`，`unresolved` 同时给出同一句可执行说明。
- `mcp._tool_content_text`：把 `visual_requirement` 渲染进模型**可见文本**
  （不依赖 structuredContent），并明确「重发请求而不是开侧边栏浏览器」。
- 三宿主 Skill（DSH / Claude Code / Codex）同步为祈使句：取画面三个字段必须一起
  发；失败时读字段补齐后重发。

边界未变：抖音的登录/验证墙仍在，Envoy 不绕验证、不伪造覆盖；能取到的前提是
用户给出可用的公开片源或授权 cookie。第 7 节列的两条仍开放。

## 9. 复核（2026-10-26）：评测口径与首页数字

用户要求核实首页评测数字与分类口径。逐条复核结果：

1. **两臂上限不同已确认。** §7 记录的偏离在归档里成立：baseline 60 轮按计划 900 秒
   （`/tmp/AB3/run-meta.json` 的 `cap_seconds: 900`），其中 41 轮 exit 124 撞上限；
   envoy 60 轮按 2400 秒（`/tmp/AB6/plan-s*/run-log.jsonl` 每行
   `turn_timeout_seconds: 2400`），0 轮撞上限。完成率差距混合了工具与时间两个
   变量，不能当作能力差。逐轮记录并不完整：AB3 的 run-log 里 61 条 baseline、
   67 条 envoy attempt 没有逐行 `turn_timeout_seconds`，另有 12 条 baseline 与
   49 条 envoy 分别记为 900 / 2400 秒，所以只能按「计划 900 秒、envoy 实际按
   2400 秒重跑」表述。
2. **分类器不验证质量。** `delivered` 已更名为 `suspect_complete`（文本规则：
   未超时、未报错、答案不短、未说读不到），新增 `verified` 只认人工评审
   `answer_correct: true`；当前评审未填，两臂 `verified = 0`。按新口径重算：
   baseline `suspect_complete` 13/60、`timed_out` 41/60；envoy 55/60、0/60。
   归档 `summary.json` 是 92 轮（completed 19/56、23/36），首页原表
   23%/58%、70%/37%、95/120 均无法从归档复现，已从 README 撤回。
3. **来源计分过宽已修正。** 旧规则按 host 命中计分，同一公众号/同一平台多个
   来源互相计分；现要求文章级 canonical URL 相等，host-only 提及只进
   `legacy_host_mentions` 诊断字段。对旧 `grade.jsonl` 中 30 行答案文本逐字节
   相同的记录：旧规则 12 处命中 → 文章级 8 处，其中 2 行只引用分享入口
   （`iesdouyin.com/share/video/`）或接口地址（`aweme/detail/?aweme_id=`），
   没有具体文章/视频 id，旧规则却各给两个来源计分。合并数据上 envoy 的 host
   提及 25 处、文章级命中 6 处，高估是系统性的。facts 命中仍是 60% 词覆盖，
   不是正确性。
4. **计划顺序矛盾已修复。** `plan.json` 有 10 个包的 `first_arm` 与 `arms[0]`
   相反，而 `run_ab.py` 原先只按 `arms` 位置循环，声明的交织首发没有生效。
   现在按 `first_arm` 排序（`ordered_arms`），提供 `--check-plan` /
   `--strict-plan` 并在记录里写 `arm_position`，`plan.json` 已对齐
   （`order_problems: 0`）。已执行运行用的是单臂分片计划
   （`/tmp/split/plan-s*-{arm}.json`），其结果不受影响，但该批数据同样没有
   实现包内随机首发。

口径变更与对既有结果的影响逐条记在
`benchmarks/fixed-set-v1/AB-PROTOCOL.md` 的「变更（复核 2026-10-26）」。
