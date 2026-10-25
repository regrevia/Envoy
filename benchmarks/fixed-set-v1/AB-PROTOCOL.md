# A/B 评测协议（预注册，先于正式记录）

本文件在正式跑分**之前**写定，任何后续调整都必须在此追加「变更」小节并注明时间与理由，不许事后悄悄改口径。

## 1. 实验对象

- 固定集：`benchmarks/fixed-set-v1/manifest.json`，`manifest_sha256 = b6caf6b6b1ff1985fb3fcb23ef5af16a55f2e43bb10d56298411b6e914bac356`。
- 规模：**20 个 package / 4 层 × 5（bilibili、douyin、wechat、cross_platform）/ 40 个唯一公开 URL / 60 个轮次**（每包 1 个 initial + 2 个 follow-up）。
- 小红书层的排除是实测结论，不是取舍偏好：见 `XHS-ACCESS-FINDINGS.md`。三次抓取（短链 blocked、笔记链接无登录态 1368 字符仅标题与页脚、带登录态 9668 字符抓到首页推荐流）均无笔记正文，无法提供可逐字校验的 `quote`。

## 2. 两臂

| 臂 | 环境 | 差异 |
|---|---|---|
| A `baseline` | 本机 Claude Code 2.1.197，独立 `HOME`/`CLAUDE_CONFIG_DIR`/`XDG_*`/`TMPDIR`/workdir | 不装 Envoy 插件（`enabledPlugins: {}`） |
| B `envoy` | 同上 | 装 `envoy@envoy` 插件，`ENVOY_MCP_TOOLSET=full` |

- 两臂同一网关、同一模型（`anthropic/DeepSeek-V4.1-Flash`）、同一提示词、同一超时上限。
- 包内两臂使用互不相同的临时目录与 `session_id`；包与包之间不复用会话。
- 首次臂顺序由计划给定（`first_arm`），A/B 首发各 10 包，交织执行。

## 3. 轮次口径（本次预注册的关键参数）

- **每轮墙钟上限 900 秒，两臂相同。**（变更 2026-10-25 12:40：试跑阶段 420 秒会把合法的长任务判成 blocked——B 站视频 ASR 单次转录实测 548 秒、基线臂 curl 兜底单轮 300+ 秒——故上调至 900 秒；试跑数据整体作废，不计入结论。）
- 达到上限的轮次记为轮次结果 `blocked`，其答案不计入正确性评分，但**计入 token 与耗时的分母**（即「拿不到答案」是真结果，不许从统计里剔除）。
- 单轮内的模型重试（上游 400、网关重试）不改变轮次口径；用量以服务端账本按 `turn_correlation_id` 汇总为准。

## 4. 账本与缓存隔离（硬门禁）

数据只采信本地链路 `127.0.0.1:14050` → 上游 Anthropic 兼容端点，回调 `envoy_ledger.ledger_handler` 写入 `envoy_request_ledger`。

三条必须同时成立，否则**停止，不产出任何 token 节省百分比**：

1. 每条请求都能按 `run_id + package_id + arm + session_id + turn_correlation_id` 关联，且 `salt_sha256` 在该轮内两臂各自独立（每轮每臂一个新盐）；
2. 全部计入请求 `cache_read_input_tokens = 0`（缓存命中会让 B 臂的输入被错误记为“省 token”，必须排除）；
3. `server_request_id` 互不相同，且不等于本地 `chatcmpl-<uuid>` 调用 id。

已知且不隐瞒的形式说明：流式 CLI 流量记录的是上游流式 id `chatcmpl-<uuid>`；`chatcmpl-req-…` 形式的 body id 只在上游非流式响应出现。该形式差异不影响判据 3 的「互不相同且不等于本地 id」。

## 5. 指标

- 主指标：**每轮完成所需的服务端 `input_tokens` 合计**（含失败重试，按 `turn_correlation_id` 求和）。
- 次指标：轮次 `completed/blocked` 比例、墙钟时长、`output_tokens`。
- 正确性：由宿主 agent 按每包 `reference_facts` 逐条判定（引用必须落在来源快照内），评分规则不因臂不同而变。
- 汇总结论一律给**配对差**（同包同轮 A vs B）与各自的分位数，不给单一均值。

## 6. 诚实边界

- 不编造来源、facts 或数字；任一硬门禁失败即报告真实阻塞。
- 上游存在间歇性 tokenizer 400（`max context 262144` 被误判越界），不经网关亦可复现；受影响轮次如实记为失败/阻塞，不做插补。
- 小红书不可读、抖音事实为页面级（非口播转录）、B 站无站内字幕（转录来自本地 ASR 且有识别错误）——这些已写入各包 `coverage`，不得在报告里美化成“完整转录”。

## 评分口径（2026-10-25 运行期间确定，标注为事后口径）

三个机器指标都是保守的、可复核的自动检查，不做人工润色：

1. **facts 命中**：每个 `reference_fact` 的英文 `claim` 去停用词后取 ≥4 字母词集合，答案覆盖其中 ≥60% 记命中（`ab_grade.py`）。
2. **来源归属**：用冻结快照首行标题前 12 字、作者名或来源 host 在答案中出现判定该来源被署明，命中序号写入 `attributed_sources`。
3. **工具遥测**：从每臂隔离 `CLAUDE_CONFIG_DIR` 的会话 transcript 按轮次 prompt 指纹切分，统计 `tool_calls`、envoy 工具调用数、重复目标数、首次证据毫秒（`ab_telemetry.py`）。

这些口径在记录性运行开始后写入，故不作为预注册假设；预注册部分（包数、轮数、上限、两臂定义、门禁）未改动。事实正确性另有 host agent 抽检，抽检结论单独记录，不与上述自动指标混算。

## 运行事故记录（2026-10-25 13:05 前后，如实披露）

记录性运行开始后，本机宿主发生重启，随之被带走的还有隔离网关进程（`127.0.0.1:14050`）。表现是 `curl /health/liveliness` 返回 `000`（连接被拒），随后正在跑的 CLI 轮次在 ~176–186 秒内以 `exit=1` 退出、stderr 为空。这批行总计 26 条（envoy 13 / baseline 13），**不是模型结果**，属于基础设施损失。

处置：

1. 网关重启并加装 launchd 守护（`~/Library/LaunchAgents/local.envoy-ledger.watchdog.plist`，30 秒探活、失活即拉起），避免二次发生；重启后 `/health/liveliness` 复测 200。
2. `run_ab.py --resume` 只把 `exit_code ∈ {0, 124}` 的轮次视为终态；`exit=1` 的键会被重新执行，不因“已记录”而被跳过。
3. `ab_report.py` 按 `(package_id, arm, role)` 去重，取 `0 > 124 > 其它` 的最佳一次，并在 stderr 打印被替换的尝试数；报告同时保留 `failed_requests_excluded` 计数。
4. 账本行一律保留原始记录，不删不改：26 条故障行留在 `envoy_request_ledger` 与导出文件里，可被任何第三方复查其时间戳与 `exit` 分布。

判定：这些故障行不进入最终统计，替换它们的重跑结果与其共用同一 `turn_correlation_id`，因此每轮的 token、salt、`server_request_id` 仍可逐条对上账本。
