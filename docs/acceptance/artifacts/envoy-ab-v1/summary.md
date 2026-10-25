# A/B 结果（服务端账本口径）

轮次：120；每轮上限：baseline 900s（计划值）；envoy 2400s（run-log 记录）。

口径：completed 指进程在上限内退出，blocked 指撞到墙钟上限；两者都不代表答案正确。
verified = 0：本报告不含人工 review，没有轮次能算作质量已验证。

| 臂 | 轮次 | completed（进程退出，非质量） | blocked（超时） | 账本 input tokens | 中位 input | 账本 output | 墙钟合计 s |
|---|---|---|---|---|---|---|---|
| baseline | 60 | 19 | 41 | 25733603 | 216678.0 | 472107 | 44143.3 |
| envoy | 60 | 60 | 0 | 98689702 | 804922.0 | 2827612 | 26087.6 |


| 臂 | 工具调用合计 | 工具调用中位 | 首次证据中位 ms | 重复抓取 | facts 命中/总数（阈值 0.6） | 文章级引用命中 | host 级归属（诊断） | 答案中位字符 |
|---|---|---|---|---|---|---|---|---|
| baseline | 261 | 0.0 | 88006.0 | 8 | 47/516 | 10 | 12 | 0.0 |
| envoy | 291 | 0.5 | 48365.0 | 2 | 197/516 | 6 | 25 | 8142.5 |

facts 阈值＝每条 claim 的英文特征词（≥4 字母、去停用词）覆盖率 ≥0.6；facts 总数 516＝20 包事实×3 轮重复计数，不是 516 条唯一事实。
文章级引用命中＝答案里出现与声明来源同一文章的规范化 URL（host+path+文章参数）；host 级归属只是诊断，不当作引用。
评级默认只用终端 answers.jsonl；transcript 回收文本不进入两臂对比（见 grade 的 answer_source）。

门禁：{"every_request_has_metadata": true, "cache_read_zero": true, "salts_present": true, "server_ids_unique": true, "failed_requests_excluded": 2093, "cap_seconds": 900, "envoy_cap_seconds": 2400, "passed": true}
配对（两臂都 completed 共 19 轮）：envoy 账本 input 合计 17422152 / baseline 2904111 = 6.0 倍（envoy 更贵）。
口径：上句是两臂都 completed 的轮次按账本 input 合计相比（19 对）；four-questions.md 里两臂账本均非零的配对看的是每对相对变化的中位数，两个口径不同，不互相印证。
