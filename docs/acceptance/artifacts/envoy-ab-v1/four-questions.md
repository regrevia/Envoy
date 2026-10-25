# Envoy 固定评测集 v1：四个问题的实测回答

- run_id: `envoy-ab-20261025-ff0ae997d591`
- 有效轮次：120（计划 120）
- 双覆盖配对：60（两臂都 completed：19）
- 缓存隔离：`verified_disabled`

## 1. 不装 Envoy 时链接能否读取

| 臂 | 轮次 | 文章级引用命中 | host 级归属（诊断） | 来源总数 | 答案中位字符 | 空答案 |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 60 | 10 | 12 | 120 | 3,125 | 31 |
| envoy | 60 | 6 | 25 | 120 | 8,142 | 0 |

文章级引用命中＝答案里出现与声明来源同一文章的规范化 URL（host+path+文章参数）；host 级归属（诊断）只是答案提到了该 host，不当作引用。

| 臂 | facts 命中/总数（阈值 0.6） | 有 fact 命中的轮次 |
|---|---:|---:|
| baseline | 47/516 | 14/60 |
| envoy | 197/516 | 49/60 |

facts 阈值＝每条 claim 的英文特征词（≥4 字母、去停用词）覆盖率 ≥0.6；总数 516＝20 包事实×3 轮重复计数，不是 516 条唯一事实。
评级默认只用终端 answers.jsonl；transcript 回收文本不进入两臂对比（见 grade 的 answer_source）。

## 2. token 消耗对比（服务端账本口径）

| 臂 | 计入请求 | 输入 token 合计 | 输入中位 | 输出 token 合计 | cache_read |
|---|---:|---:|---:|---:|---:|
| baseline | 834 | 25,733,603 | 216,678 | 472,107 | 0 |
| envoy | 2915 | 98,689,702 | 804,922 | 2,827,612 | 0 |

两臂都 completed 且两侧账本均非零的 17 对：envoy 输入 token 相对变化中位数 266.5%（正数＝envoy 更贵）。
口径：上句是每对 (envoy-baseline)/baseline 的中位数，不是账本合计的倍数；summary.md 的 completed 配对倍数按两臂 input 合计相比，两者不可互相换算。

## 3. 处理速度

| 臂 | 轮次 | 墙钟中位 s | 墙钟 p90 s | 首次证据中位 s | 撞上限 |
|---|---:|---:|---:|---:|---:|
| baseline | 60 | 900.0 | 900.1 | 88.0 | 41 |
| envoy | 60 | 255.7 | 884.6 | 48.4 | 0 |

## 4. 长任务（撞上限、多跳）

| 臂 | completed | blocked(124) | failed | 工具调用中位 | 重复抓取合计 |
|---|---:|---:|---:|---:|---:|
| baseline | 19 | 41 | 0 | 1.0 | 8 |
| envoy | 60 | 0 | 0 | 0.5 | 2 |

多跳（envoy 侧至少 1 次 envoy 调用）配对 28；含撞上限的配对 41。

## 门禁

```json
{
  "every_request_has_metadata": true,
  "cache_read_zero": true,
  "salts_present": true,
  "server_ids_unique": true,
  "failed_requests_excluded": 2093,
  "cap_seconds": 900,
  "envoy_cap_seconds": 2400,
  "passed": true
}
```

