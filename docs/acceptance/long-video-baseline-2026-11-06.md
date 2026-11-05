# 长视频可信基准：900 s / 2400 s 两组「0 超限」+ 人工正确性评判（2026-11-06）

本页回答一件事：把 Envoy 的读预算设成 **900 s** 与 **2400 s** 两组，用真实长/短视频各跑一遍，两组是否都 **0 次超限**，以及返回的转写是否与真实音频对得上。

先说结论，再说口径与证据：

1. **仓库里不存在「900 s / 2400 s 两档」这个产品定义。** 这两个数只出现在 A/B 评估里，是**两臂各自的墙钟上限**（基线 900 s、envoy 臂 2400 s），不是 `Budget` 的档位。产品侧只有一个标量 `budget.max_work_seconds`。
2. 因此本页采用的**等价口径**是：同一条读路径，只把 `budget.max_work_seconds` 分别设成 900 与 2400，对同一素材各跑一遍，逐字段对拍。等价性是**可证**的（第四节，`file:line` + 实测同 sha）。
3. 两组各跑 2 遍 + 1 个全量复核，**全部 exit 0、`errors: []`、无 `budget_exceeded`、实测耗时 2.09–2.44 s（占各自上限的 0.09%–0.27%）→ 两组均 0 超限**。另有一个 `max_work_seconds=1` 的反向对照，**按预期超限失败**，证明这个标量真的在卡，而不是只记不用。
4. 人工正确性：短样本 6/6 句、长样本 391/391 句与 YouTube **人工字幕轨**逐字相同；抽检的首 5 句 + 末 5 句全部 `正确`。**但必须如实标注：两侧同源（Envoy 也是取人工字幕轨），所以这验证的是「读回来的转写没有损坏/错位」，不是独立的 ASR 准确率。**
5. **未压到上限**：字幕路径是 O(秒级) 而不是 O(视频时长)，四组都在 ~2.2 s 结束，所以「0 超限」成立但**不构成对该上限的压力测试**；真正吃满 900/2400 s 的下载 + 本地 ASR 长跑路径本机**跑不了**（无 `whisper`，e2e 脚本依赖未启动的后端栈），标 `not_run`。

证据等级（沿用本仓库约定）：`verified` = 本次实测；`unit_checked` = 代码路径核对；`not_run` = 明确未执行。

---

## 一、口径：900 s / 2400 s 到底是什么

### 1.1 不是产品档位

`packages/envoy-core/src/envoy_core/schemas/read_v1.py:152`：

```python
max_work_seconds: int = Field(default=180, ge=1, le=86_400)
```

一个标量，默认 180，上限 86 400。`Budget` 里的其余字段（`max_response_chars` 等）与它无联动；`validate_totals` 只校验字符/字节/范围类字段，不碰工作时长。全仓库对 `packages/` 的检索（排除 `.venv/`、`build/lib/`）中，**没有任何 900/2400 的档位表或常量**；产品代码里出现的字面量 900 只有两处无关的浏览器超时（`packages/envoy-cli/src/envoy_cli/browser.py:537` 的 `timeout_seconds: float = 900.0`、`packages/envoy-cli/src/envoy_cli/mcp.py:328` 的 `{minimum: 30, maximum: 900}`），与读预算无关。

### 1.2 它们的真实出处：A/B 两臂的墙钟上限

`docs/acceptance/envoy-ab-v1-2026-10-25.md:8`：

> 两臂墙钟上限不同（900 s vs 2400 s），所以这是探索性观察，不是同预算对照。

同页 `:15` 的表格写明「每轮墙钟上限 | **900 s**（计划值） | **2400 s**（run-log 记录）」。代码侧对应 `benchmarks/fixed-set-v1/run_ab.py:355-359`：

```python
# Which wall-clock cap this row was cut with. The arms do not share one
# cap any more (the baseline keeps the 900 s control limit, the envoy arm
# gets 2400 s for long video), so a "blocked" row is only interpretable
# next to the cap that produced it.
"turn_timeout_seconds": timeout,
```

`run_ab.py:420` 的 `--timeout` 默认 1200，`:422-426` 的 `--envoy-timeout` 允许 envoy 臂单独给上限，`:587` 用 `(args.envoy_timeout or args.timeout) if with_envoy else args.timeout` 落盘到 `turn_timeout_seconds`。复现命令见 `docs/acceptance/envoy-ab-v1-2026-10-25.md:96-104`（`--arm baseline --timeout 900`、`--arm envoy --envoy-timeout 2400`）。

**所以**：900 是基线臂（通用 agent + 读工具）的整轮墙钟预算，2400 是 envoy 臂的整轮墙钟预算；它们描述的是「一次带读任务的对话轮最多跑多久」，不是「一次读请求最多跑多久」。

### 1.3 本页实际采用的等价口径

把「一次对话轮的墙钟上限」换成「一次读请求的工作预算」，取同一条路径：

| 组 | `budget.max_work_seconds` | 样本 | 说明 |
| --- | --- | --- | --- |
| 900 组 | 900 | 短 + 长 | 与基线臂同名义上限 |
| 2400 组 | 2400 | 短 + 长 | 与 envoy 臂同名义上限 |

读请求其余预算保持默认（`max_response_chars` 12 000、`max_total_response_chars` 48 000），只额外加了一次 900 s + `max_response_chars=30 000` 的**全量复核**轮，用来核对视频真正的尾部（见第六节）。

---

## 二、复现协议

- 主机：macOS；时间：2026-11-06 01:0x +08:00；代理 `http://127.0.0.1:7897`（`HTTPS_PROXY`/`HTTP_PROXY`）。
- 入口：`packages/envoy-cli/.venv/bin/envoy read --request-json <path>`（注意 `read` **不接受位置参数 URL**，请求体走 `--request-json`）。
- 素材：
  - 短：`https://www.youtube.com/watch?v=jNQXAC9IVRw`（Me at the zoo，19 s）
  - 长：`https://www.youtube.com/watch?v=iCvmsMzlF7o`（The power of vulnerability，1250 s ≈ 20:50）
- 请求模板（每个 run 一个文件，落在 `raw/req_<run>.json`）：

```json
{"request_id": "p02-long-900",
 "source": {"url": "https://www.youtube.com/watch?v=iCvmsMzlF7o"},
 "goal": "transcript with timestamps",
 "budget": {"max_work_seconds": 900}}
```

- 计时与出口码由外层 shell 记录（`date +%s%N` 前后差 + `$?`），写进 `raw/manifest.json` 的 `elapsed_ms` / `exit_code`；**这是本页唯一不能从 JSON 回执重算的两个数**，重算脚本改为校验「耗时 ≤ 上限」。
- 原始回执、请求体、对照 stderr、真值字幕全部归档在：
  `/Users/apple/Desktop/2026/Envoy/docs/acceptance/artifacts/long-video-baseline-2026-11-06/raw/`
- 真值来源：`yt-dlp` 下载的 YouTube 字幕轨，`ground-truth/gt_*.en.vtt`（`.en.vtt` = 人工轨，`.en-en.vtt` = 自动轨；本次两侧命中的都是人工轨，回执里 `transcript_source: "manual_subtitles"` 可对）。

复现一条：

```bash
cd /Users/apple/Desktop/2026/Envoy
cat > /tmp/req.json <<'JSON'
{"request_id": "p02-long-900",
 "source": {"url": "https://www.youtube.com/watch?v=iCvmsMzlF7o"},
 "goal": "transcript with timestamps",
 "budget": {"max_work_seconds": 900}}
JSON
packages/envoy-cli/.venv/bin/envoy read --request-json /tmp/req.json > /tmp/out.json
```

---

## 三、数据表

### 表 1 · 短样本（19 s，`jNQXAC9IVRw`）

| run | 上限(s) | exit | 实测耗时(ms) | 占上限 | state/result | transcript_source | 时长(s) | processed_until | 字符 返回/总数 | continuation | unresolved | 超限 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| short_900 | 900 | 0 | 2095 | 0.23% | completed/ready | manual_subtitles | 19 | null | 294/294 | false | 1 | 否 |
| short_900b | 900 | 0 | 2137 | 0.24% | completed/ready | manual_subtitles | 19 | null | 294/294 | false | 1 | 否 |
| short_2400 | 2400 | 0 | 2274 | 0.09% | completed/ready | manual_subtitles | 19 | null | 294/294 | false | 1 | 否 |
| short_2400b | 2400 | 0 | 2090 | 0.09% | completed/ready | manual_subtitles | 19 | null | 294/294 | false | 1 | 否 |

4 次 `errors: []`；转写文本 sha256 相同（`bedac813acf2…`），即 900 与 2400 两组回执逐字一致。唯一 unresolved 是固定的能力声明：`Video frames were not inspected; this material contains transcript evidence only`（只要没看帧，就会带这一条，与本页结论无关）。

### 表 2 · 长样本（1250 s，`iCvmsMzlF7o`）

| run | 上限(s) | exit | 实测耗时(ms) | 占上限 | state/result | transcript_source | 时长(s) | processed_until | 字符 返回/总数 | continuation | next_offset | 超限 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| long_900 | 900 | 0 | 2440 | 0.27% | completed/partial | manual_subtitles | 1250 | null | 11254/22005 | true | 11254 | 否 |
| long_900b | 900 | 0 | 2159 | 0.24% | completed/partial | manual_subtitles | 1250 | null | 11254/22005 | true | 11254 | 否 |
| long_2400 | 2400 | 0 | 2333 | 0.10% | completed/partial | manual_subtitles | 1250 | null | 11254/22005 | true | 11254 | 否 |
| long_2400b | 2400 | 0 | 2189 | 0.09% | completed/partial | manual_subtitles | 1250 | null | 11254/22005 | true | 11254 | 否 |
| long_900_full | 900 | 0 | 2227 | 0.25% | completed/ready | manual_subtitles | 1250 | null | 22005/22005 | false | — | 否 |

长样本的 4 次 `errors: []`、转写 sha 相同（`a2d886ae8ea0…`）、coverage 全字段相同。

**两点必须讲清：**

1. `result=partial` **不是超限**，也不是时间没跑完。根因是**回执字符预算**：`returned_chars=11254 / total_chars=22005`，`continuation_available=true`、`next_offset=11254`——转写被 `max_response_chars`（默认 12 000）截断，第 5 行 `long_900_full` 把该预算放到 30 000 后一次返回全部 391 句、`result=ready`。字符预算与工作预算正交，工作预算没被触发。
2. `processed_until_seconds=null` + 时长 1250 s：时间轴是**整段处理**的，不是只处理了前 595 s。截断发生在「返回」这一步（`max_response_chars`），所以被截断的回执里最后一句停在 595.68 s，而视频真实结尾在 20:11.627。

### 表 3 · 反向对照（证明上限真的在卡）

| run | 上限(s) | exit | 实测耗时(ms) | 结果 |
| --- | --- | --- | --- | --- |
| control_1 | 1 | 1 | 193 | 未产生回执（0 字节），stderr 走到 `MediaBudgetExceeded` |

stderr 末端（`raw/control_1_stderr.txt`）：

```
packages/envoy-cli/src/envoy_cli/reader.py:10491  max_work_seconds=min(_remaining_work_seconds(request), _SUBTITLE_PHASE_SECONDS)
packages/envoy-cli/src/envoy_cli/reader.py:3289   raise MediaBudgetExceeded("The request max_work_seconds budget is exhausted")
envoy_cli.media.MediaBudgetExceeded: The request max_work_seconds budget is exhausted
```

即：同一个入口、同一个样本，只要把上限压到 1 s，读立刻被预算挡下并失败。因此表 1/表 2 的 exit 0 是「预算没被触发」，不是「预算形同虚设」。

---

## 四、为什么 900 与 2400 是同一个机制的两次取值（等价性证据）

1. **唯一消费点只看标量。** `packages/envoy-cli/src/envoy_cli/reader.py:3283-3291` 的 `_remaining_work_seconds(request)` 在无 deadline 上下文时 `return max(int(request.budget.max_work_seconds), 1)`；有 deadline 时返回 `int(deadline - time.monotonic())`，`< 1` 就 `raise MediaBudgetExceeded(...)`。没有任何分支读「900」或「2400」。
2. **截止时间只由该标量派生。** `reader.py:3455` 用同一个值设置 `_READ_WORK_DEADLINE`，`:3458` 的 `execute()` 包住整个读取；`collect.py:513,524` 同样以 `max(int(remaining_work()), 1)` 下传，`collect.py:486` 的 worker 默认 120 只是兜底。
3. **子阶段是「min(剩余, 阶段上限)」。** 字幕阶段 `reader.py:166` 定义 `_SUBTITLE_PHASE_SECONDS = 45`，`reader.py:10491` 取 `min(_remaining_work_seconds(request), _SUBTITLE_PHASE_SECONDS)`——所以 900 与 2400 在这一阶段都会先被 45 s 夹住，行为完全相同（这也是四组都 ~2.2 s 就结束的机制原因之一）。`reader.py:1662,2576,2595,2654` 也都只传 `max_work_seconds=_remaining_work_seconds(request)`。
4. **worker 侧按 deadline 硬停。** `packages/envoy-cli/src/envoy_cli/media.py:164-205`：`timeout = max(int(max_work_seconds), 1)`，循环里 `remaining = deadline - time.monotonic()`，`<= 0` 抛 `MediaBudgetExceeded(f"Media worker exceeded max_work_seconds ({timeout})")`；`:871`、`:902-911` 是下载/媒体侧同口径的硬停。
5. **实测互补证据。** 若两档走不同逻辑，900/2400 的回执不可能逐字节同构——实测：短样本 4 次同 sha、长样本 4 次同 sha、coverage 全字段相同（`raw/manifest.json` + 第三节表）；同时 `cap=1` 对照组立刻失败（表 3）。一正一反，方向一致。

结论：**提高 `max_work_seconds` 只会放宽同一个 deadline；不存在「900 s 走 A 路径、2400 s 走 B 路径」的分叉。** 因此「900 组 0 超限」与「2400 组 0 超限」在实现层面是同一件事的两次测量，而不是两套需要分别验收的机制。

---

## 五、0 超限证据

判定超限的口径（三条任一即算超限，由 `scripts/acceptance/long_video_baseline_recompute.py` 机械判定）：

1. 出口码非 0；
2. 回执 `errors[]` 里出现 `budget_exceeded` / `budget_enforcement_unavailable`，或 message 命中 `max_work_seconds`；
3. 实测耗时 > 上限。

结果（重算脚本输出）：

```
runs=10 cap_arms=9 zero_overrun=True control_overran=['control_1'] diverged=none pass=True
```

- 900 组（`short_900`, `short_900b`, `long_900`, `long_900b`, `long_900_full`）：5 次全 exit 0、`errors: []`、耗时 2.095–2.440 s（上限 900 s）。
- 2400 组（`short_2400`, `short_2400b`, `long_2400`, `long_2400b`）：4 次全 exit 0、`errors: []`、耗时 2.090–2.333 s（上限 2400 s）。
- 两组都没有任何 `budget_exceeded` / `work_budget_exhausted` 记录，`errors` 全空。
- 反向对照 `control_1`（上限 1 s）**超限**，与设计预期一致。

**同时如实说明这份证据的强度**：上限占用量最高只有 0.27%，说明这条路径**离上限还很远**，也就意味着本页**没有**验证「接近上限时的行为」（截断策略、部分结果返回、退出码语义）。这一项按 `not_run` 记账，见第八节。

---

## 六、人工正确性评判

### 6.1 判定方法

- 真值：`ground-truth/gt_<videoId>.en.vtt`（YouTube 人工字幕轨，与回执 `transcript_source=manual_subtitles` 对应）。
- 逐句对齐：按时间戳配对，比较文本（空白/大小写归一）。全量对照由 `scripts/acceptance/long_video_baseline_recompute.py --ground-truth` 产出：它逐句比对归档回执与 VTT，只读本地文件、不联网（命令与输出见第七节）。
- 判定词：`正确` / `错误` / `不可判定`。
- **同源声明**：两侧都来自同一条人工字幕轨，因此本节的「正确」等价于「读回来的转写与源字幕逐字一致、时间戳未错位」，**不能**外推为「Envoy 的语音识别准确率」；后者需要独立 ASR 参照，本机无 `whisper`，`not_run`。

### 6.2 短样本（19 s，共 6 句；样本太短，首 5 与末 5 必然重叠，故 6 句全列）

| # | 位置 | 时间戳 | Envoy 返回 | 与源字幕 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | 首 1 | 00:01.200 | All right, so here we are, in front of the elephants | 逐字相同 | 正确 |
| 2 | 首 2 | 00:05.318 | the cool thing about these guys is that they have really... | 逐字相同 | 正确 |
| 3 | 首 3 | 00:07.974 | really really long trunks | 逐字相同 | 正确 |
| 4 | 首 4 | 00:12.616 | and that's cool | 逐字相同 | 正确 |
| 5 | 首 5 | 00:14.421 | (baaaaaaaaaaahhh!!) | 逐字相同 | 正确 |
| 6 | 末 1 | 00:16.881 | and that's pretty much all there is to say | 逐字相同 | 正确 |

（首 5 = 第 1–5 句，末 5 = 第 2–6 句，重叠 4 句；此处按「全部 6 句各自判定」呈现，不重复凑数。）

### 6.3 长样本 · 截断回执（1250 s，返回前 185 句，止于 09:55.679）

| # | 位置 | 时间戳 | Envoy 返回 | 与源字幕 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | 首 1 | 00:16.860 | So, I'll start with this: a couple years ago, an event planner called me | 逐字相同 | 正确 |
| 2 | 首 2 | 00:20.345 | because I was going to do a speaking event. | 逐字相同 | 正确 |
| 3 | 首 3 | 00:22.418 | And she called, and she said, | 逐字相同 | 正确 |
| 4 | 首 4 | 00:24.132 | "I'm really struggling with how to write about you on the little flyer." | 逐字相同 | 正确 |
| 5 | 首 5 | 00:27.728 | And I thought, "Well, what's the struggle?" | 逐字相同 | 正确 |
| 6 | 末 5 | 09:34.990 | in order to be who they were, which you have to absolutely do that | 逐字相同 | 正确 |
| 7 | 末 4 | 09:39.618 | for connection. | 逐字相同 | 正确 |
| 8 | 末 3 | 09:43.323 | The other thing that they had in common was this: | 逐字相同 | 正确 |
| 9 | 末 2 | 09:50.910 | They fully embraced vulnerability. | 逐字相同 | 正确 |
| 10 | 末 1 | 09:55.679 | They believed that what made them vulnerable made them beautiful. | 逐字相同 | 正确 |

> 该回执的「末 5 句」是**返回文本**的末尾（视频第 595 s），不是视频的末尾。仅凭这一份回执无法判断 595 s 之后是否正确，因此**视频真实尾部**单列在 6.4，用全量回执复核。

### 6.4 长样本 · 全量回执（`long_900_full`，391 句，覆盖到 20:11.627）

| # | 位置 | 时间戳 | Envoy 返回 | 与源字幕 | 判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | 首 1 | 00:16.860 | So, I'll start with this: a couple years ago, an event planner called me | 逐字相同 | 正确 |
| 2 | 首 2 | 00:20.345 | because I was going to do a speaking event. | 逐字相同 | 正确 |
| 3 | 首 3 | 00:22.418 | And she called, and she said, | 逐字相同 | 正确 |
| 4 | 首 4 | 00:24.132 | "I'm really struggling with how to write about you on the little flyer." | 逐字相同 | 正确 |
| 5 | 首 5 | 00:27.728 | And I thought, "Well, what's the struggle?" | 逐字相同 | 正确 |
| 6 | 末 5 | 20:00.743 | then we stop screaming and start listening, | 逐字相同 | 正确 |
| 7 | 末 4 | 20:04.735 | we're kinder and gentler to the people around us, | 逐字相同 | 正确 |
| 8 | 末 3 | 20:07.086 | and we're kinder and gentler to ourselves. | 逐字相同 | 正确 |
| 9 | 末 2 | 20:10.125 | That's all I have. Thank you. | 逐字相同 | 正确 |
| 10 | 末 1 | 20:11.627 | (Applause) | 逐字相同 | 正确 |

### 6.5 全量一致性（不只是抽检）

| 样本 | Envoy 句数 | 源字幕 cue 数 | 拼接文本 | 逐句命中 | 首/末时间戳 |
| --- | --- | --- | --- | --- | --- |
| 短（19 s） | 6 | 6 | 完全相同（217 字符） | 6/6 | 1.200 / 16.881 全同 |
| 长（1250 s，全量回执） | 391 | 391 | 完全相同（16923 字符） | 391/391 | 16.860 / 1211.627 全同 |

拼接后整段文本相同、句数与末时间戳（`20:11.627`）都一致，说明既没有丢句、没有重复、也没有时间轴漂移。

---

## 七、重算与复现

一切数字都能从归档回执重算，不依赖本页正文：

```bash
cd /Users/apple/Desktop/2026/Envoy
# 表格 + 0 超限判定（exit 0 表示两组均 0 超限且逐字段一致）
packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py
# 机器可读版本
packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py --json
# 人工正确性对照（回执 vs 归档的 YouTube 字幕轨）
packages/envoy-cli/.venv/bin/python scripts/acceptance/long_video_baseline_recompute.py --ground-truth
```

脚本输出（本次实跑，节选）：

```
| run | sample | cap(s) | exit | elapsed(ms) | state/result | errors | segments | first/last (s) | overrun |
| short_900 | short | 900 | 0 | 2095 | completed/ready | none | 6 | 1.2/16.881 | no |
| short_900b | short | 900 | 0 | 2137 | completed/ready | none | 6 | 1.2/16.881 | no |
| short_2400 | short | 2400 | 0 | 2274 | completed/ready | none | 6 | 1.2/16.881 | no |
| short_2400b | short | 2400 | 0 | 2090 | completed/ready | none | 6 | 1.2/16.881 | no |
| long_900 | long | 900 | 0 | 2440 | completed/partial | none | 185 | 16.86/595.679 | no |
| long_900b | long | 900 | 0 | 2159 | completed/partial | none | 185 | 16.86/595.679 | no |
| long_2400 | long | 2400 | 0 | 2333 | completed/partial | none | 185 | 16.86/595.679 | no |
| long_2400b | long | 2400 | 0 | 2189 | completed/partial | none | 185 | 16.86/595.679 | no |
| control_1 | long | 1 | 1 | 193 | None/None | none | None | None/None | **YES** |
| long_900_full | long | 900 | 0 | 2227 | completed/ready | none | 391 | 16.86/1211.627 | no |

| sample | caps | transcript identical | coverage identical |
| short | [900, 2400] | True | True |
| long | [900, 2400] | True | True |

runs=10 cap_arms=9 zero_overrun=True control_overran=['control_1'] diverged=none pass=True
```

人工对照（真值在 `raw/ground-truth/`，脚本按 6.1 的口径逐句比对，本次输出；脚本取每个样本**返回字符最多**的那份回执，因此长样本自动选中 `long_900_full`）：

```
=== short (short_900b): envoy_segs=6 gt_cues=6 joined_text_equal=True
    envoy_chars 217 gt_chars 217
    envoy segments whose text appears verbatim in GT: 6/6
    first/last envoy 00:01.200/00:16.881  GT 00:00:01.200/00:00:16.881
=== long (long_900_full): envoy_segs=391 gt_cues=391 joined_text_equal=True
    envoy_chars 16923 gt_chars 16923
    envoy segments whose text appears verbatim in GT: 391/391
    first/last envoy 00:16.860/20:11.627  GT 00:00:16.860/00:20:11.627
EXIT=0
```

（时间戳书写不同：回执是 `mm:ss.mmm`，VTT cue 是 `hh:mm:ss.mmm`；脚本比对数值与文本，不受格式影响。）

---

## 八、未达成 / 未覆盖（`not_run`）与如实说明

| # | 项 | 状态 | 具体原因 |
| --- | --- | --- | --- |
| 1 | 「900 s / 2400 s 两档」产品定义 | **不存在** | 仓库无此档位；只有 A/B 两臂墙钟上限（`envoy-ab-v1-2026-10-25.md:8,15`、`run_ab.py:355-359`）。本页用「同一标量取 900 / 2400」作等价口径。 |
| 2 | 接近上限时的行为（部分回执、超时错误语义） | `not_run` | 字幕路径 ~2.2 s 就结束，占上限 ≤0.27%，没有把上限跑满。要跑满需下载 + 本地 ASR 长跑，见 #3。 |
| 3 | 下载 + 本地 ASR 长路径 | `not_run` | 本机 `ModuleNotFoundError: No module named 'whisper'`，无本地 ASR 兜底；`scripts/run_large_video_delivery_e2e.py` 还依赖 `app.services.crud_service`、`app.workers.jobs.preprocess_job`、`DATABASE_URL` 这套**未启动**的后端栈（`docs/acceptance/long-video-acceptance-2026-11-01.md` 已记为 7 个场景 `not_run`）。 |
| 4 | 独立 ASR 准确率（非字幕轨） | `不可判定` | 两侧同源（都用 YouTube 人工字幕轨），本页只能说「与源字幕逐字一致」，说不出识别准确率。 |
| 5 | 视频画面/帧内容 | `not_run` | 四次回执都带 unresolved：`Video frames were not inspected; this material contains transcript evidence only`；本页只评转写，不评帧。 |
| 6 | 端到端 checkpoint 续读在 900/2400 下的表现 | 部分 | 已跑通 `task-checkpoint create` → `next`（`raw/ck_create_out.json`、`raw/ck_next_out.json`），但 checkpoint 生成的 expand 请求**不带 `max_work_seconds`**，走默认 180 s，因此它不是 900/2400 的验证对象；这条仅作记录，不计入本页结论。 |
| 7 | 真值字幕的版权/保存 | 已归档 | `raw/ground-truth/*.vtt` 仅供本次比对与复现使用。 |

另外两点自我约束，避免读者误读：

- **耗时表含缓存/顺序偏置**：同一素材重复读会复用磁盘上已抓到的字幕，第 2 次往往略快（如 `short_2400` 2274 ms → `short_2400b` 2090 ms）。重复轮的作用是给**稳定区间**，不是做受控性能基准。
- **本页没有改动任何产品代码**，也没有为了让基准通过而调参；所有结论来自现有代码路径 + 实测回执。

---

## 九、产物清单

| 路径 | 内容 |
| --- | --- |
| `docs/acceptance/long-video-baseline-2026-11-06.md` | 本页 |
| `docs/acceptance/artifacts/long-video-baseline-2026-11-06/raw/manifest.json` | 全部 run 的元信息（上限、出口码、耗时、归档文件名、对比分组、真值路径） |
| `…/raw/req_*.json` | 10 份请求体（含 `max_work_seconds` 实际取值） |
| `…/raw/out_*.json` | 9 份回执（`out_control_1.json` 为 0 字节，超限未产生回执） |
| `…/raw/control_1_stderr.txt` | 反向对照的 `MediaBudgetExceeded` 栈 |
| `…/raw/ck_create_out.json`、`ck_next_out.json` | checkpoint create / next 的回执 |
| `…/raw/ground-truth/*.vtt` | 真值字幕（人工轨 + 自动轨） |
| `scripts/acceptance/long_video_baseline_recompute.py` | 只读重算：重出表 1/2/3、判 0 超限、判两档是否同构；不联网、不改任何东西 |
