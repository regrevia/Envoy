# A 线交付报告 —— 媒体/采集预算与断点续跑（0.1.23 基线）

- 工作树：`/Users/apple/Desktop/2026/envoy-a`，分支 `wt/envoy-a`，基线 `main=fbdede5`（release 0.1.23）
- 未触碰：安装器、workflow、integrations、dist、版本号；主仓库 `/Users/apple/Desktop/2026/Envoy` 未改动
- 本轮提交：`a9666cb`（A1/A2/A4 边界）、`2d9e8c1`（A5）、`9b04c4d`（A3）、报告单独一次
- 全部改动只在工作树内，未 push、未 merge

## 一、变更清单

### A1 断点身份不再包含临时绝对路径（`media.py`）

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `media.py:1345` `ASR_DECODE_CONFIG` | 把解码参数固化进身份（16k、task、temperature、fp16、condition_on_previous_text） | 已实现 / 单测覆盖 |
| `media.py:1350` `_asr_source_digest` | 按 1 MiB 分块流式 sha256 音频字节，不读 stat、不读路径 | 已实现 / 单测覆盖 |
| `media.py:1365` `_asr_identity` | 身份 = `asr-checkpoint/v2\|revision:<可信版本>\|content:<字节摘要>\|model=…\|decode=…\|range=<start:.3f>-<end:.3f>`，取 sha256 前 20 位；有可信 revision（远端解析出的 `video:{id}:{duration}:{bytes}`）时优先用它，避免每次重读整段音频 | 已实现 / 单测覆盖 |

关键语义：同样的字节换一个临时目录 → 命中同一个断点；内容变了 → 文件名变化，旧断点自然成为孤儿（不会被误用）；旧格式断点或 `identity` 对不上的断点不会续跑（`same_work` 守卫）。

远端入口已接线：`reader.py:5799 _read_video_asr` 用下载结果拼出 `asr_source_revision = f"video:{video_id}:{duration}:{downloaded_bytes}"`，作为 `source_revision=` 传入 `transcribe_audio_file`；拿不到 video_id 时退回字节摘要。

### A2 续跑游标、无声块与合并（`media.py:1432` `_transcribe_with_worker`、`media.py:1574` `transcribe_audio_file`）

- 游标单调：`next_seconds = max(本轮确认到的块尾, 上一断点 next_seconds)`，不会因为事件乱序或重启回退。
- 不覆盖旧进度：`persist` 在「状态非 complete 且本轮没有前进且没有新片段」时直接不写盘，超时/worker 失败时旧断点原样保留。
- 空洞块不倒退：静音/无台词块仍按 `end_seconds` 推进游标并计入进度，不会被当成"没进展"。
- 语言继承：`prior_language` 从断点读回，续跑不会退回自动检测导致的语种翻转。
- 合并按块身份：`_merge_asr_segments`（`media.py:1392`）以 `(start, end)` 四舍五入到 3 位小数为键合并已载入片段与新片段，续跑不产生重复台词。
- 全新区间零进展：不写任何断点文件，不产生"空进度"断点挡住后续重试。

### A3 collect 预算真实生效（`collect.py`）

| 位置 | 内容 | 状态 |
| --- | --- | --- |
| `collect.py:32` `_record_work_usage` | 以 `collect-work-<run_id>-<sha256(candidate)[:16]>` 为稳定 operation_id 调 `record_usage`，把 discovery/单项耗时记进 manifest；revision 冲突（长任务期间租约心跳 bump 了 revision）时重读一次 manifest 再试一次，仍失败则本次记账放弃、不打断采集 | 已实现 / 单测覆盖 |
| `collect.py:474` `collect` | 每次运行一个 monotonic 时钟：`remaining_work()` = `max_work_seconds - 已耗时`；发现阶段拿剩余预算；单项开始前剩余 <1s 就 `break`（该项保持 `selected` 留给下次）；子 `ReadRequest` 带 `Budget(max_work_seconds=max(int(剩余),1))` 和稳定 `idempotency_key="collect:<cid>:<rid>:<candidate>"[:256]`；单项异常记为 `failed` 后继续；`finally` 里按实际耗时记账 | 已实现 / 单测覆盖 |

要点：子任务不再拿到默认 180s；登录等待/被跳过的项不消耗处理时间；预算耗尽的运行下次从原地继续，而不是重新给一份额度；`_record_work_usage` 吞掉 `CollectionError`，记账失败不会中止采集。

### A5 置信度分母改为"已转写区间"（`reader.py`）

- `reader.py:8017` `_transcript_confidence(segments, duration_seconds, *, processed_from=None, processed_until=None)`：分母 = `processed_until - processed_from`；未知或非正时回退整段时长（保持旧行为，外部只传两个参数的调用不受影响）；分母 ≤30s 仍视为短片不惩罚。
- 两处调用已改：`reader.py` 约 2039（`_read_video_asr` 组装 ASR 证据处，`processed_from=scope.start_seconds`、`processed_until=asr_processed_until`）与约 5610（`_video_subtitle_response`，用 `requested_start` 与 `extracted["processed_until_seconds"]`）。
- 相应提示文案从"of this Ns clip"改为"of the Ns transcribed so far"，避免把"只转写了前 40s"说成"整段几乎没语音"。

### A4 整文件解码边界（只做边界与 TODO）

`media.py` `_whisper_worker` 在 `whisper.load_audio` 前加了边界说明：当前引擎在分块前把整段音频解成一个 float32 数组（16k 下约 230 MB/小时），分块循环其实只读窗口；未来应以 `read_audio_range(path, start, end) -> ndarray` 之类的惰性窗口接口替换，`load_audio` 保留为回退。**未改引擎**，原因见第三节。

### 测试文件（新增/修改）

- `tests/cli/test_media_asr_resume.py`（新增，8 个用例）①续跑游标不倒退 ②无新块不覆盖旧断点 ③空/静音块保留进度 ④同内容不同临时目录命中同一断点键 ⑤来源 revision 变化拒绝旧进度
- `tests/cli/test_collect_budget.py`（新增，5 个用例）⑥子请求拿真实剩余预算（5s 不是 180s）⑦预算耗尽剩余项保持 selected ⑧耗时按稳定 stage id 记账 ⑨重跑复用同一 idempotency key ⑩租约心跳 bump revision 后记账仍生效
- `tests/cli/test_reader_transcript_confidence.py`（新增，4 个用例）部分转写按已转写区间评分、同一语音按全长评分应为 low、带起始偏移的窗口、未知窗口回退全长
- `tests/cli/test_media_asr_checkpoint.py`（修改 1 行）：fake worker 接受新增关键字参数

## 二、验证记录（本机实测，命令可直接复跑）

测试环境说明：系统/项目/uv 工具环境都没有 pytest；本轮用 `uv pip install --offline --target /tmp/envoy-a-pt pytest` 装到临时目录，再用带齐依赖的 tool 环境解释器跑：

```bash
cd /Users/apple/Desktop/2026/envoy-a
PYTHONPATH=/tmp/envoy-a-pt:packages/envoy-cli/src:packages/envoy-core/src \
  /Users/apple/.local/share/uv/tools/envoy-cli/bin/python -m pytest <files> -q
```

| 命令（目标） | 结果 |
| --- | --- |
| `pytest tests/cli/test_media_asr_checkpoint.py tests/cli/test_media_asr_resume.py -q` | **10 passed in 1.24s** |
| `pytest tests/cli/test_collect_budget.py -q` | **5 passed in 1.84s** |
| `pytest tests/cli/test_reader_transcript_confidence.py -q` | **4 passed**（与下一行同批 19 passed） |
| `pytest tests/cli/test_browser_runtime.py -q -k "speech or transcript or asr or video"` | **20 passed, 40 deselected in 1.78s**（含既有音乐视频误引用用例） |
| `pytest tests/cli/test_collect.py tests/cli/test_douyin_collection.py -q` | **34 passed, 1 failed in 27.10s**；失败项 `test_douyin_note_read_preserves_album_order_and_never_calls_asr` 在基线 `fbdede5` 的干净 worktree 上同样失败（图片交付 URI 退回 `data:`），**属既有问题，与本轮改动无关** |
| `python -m py_compile` 三个改动源文件 | 通过 |

全部用例都是 fake worker / 打桩 `read_public_page` / 小临时文件，无网络、无模型、无 torch、无 OCR、无浏览器；最长一批 27s，单批均 <60s。

## 三、未完成 / not_run 及原因

1. **真实长视频/长音频的断点续跑**：需要真实下载 + Whisper 推理，本轮硬约束内不做 → `not_run`。纯逻辑层已由 ⑤ 组用例覆盖（命中/拒绝/不倒退）。
2. **真实 worker 超时、被杀时旧断点保留**：需要真跑 torch 子进程并精确制造超时 → `not_run`；单测用假 worker 复现了同一 persist 分支。
3. **collect 真实平台的预算记账与登录暂停**：需要访问公开站点/登录 → `not_run`；单测以桩验证剩余预算、break 位置与 idempotency key。
4. **心跳与预算记账的真实并发窗口**：单测人为制造 revision 冲突并验证重试；真实场景（>心跳间隔的长读）需真实验收确认。
5. **A4 惰性窗口解码**：明确不做引擎替换（无 GPU 目标无法测量资源上限，换解码路径风险高）→ 只留边界说明与 TODO。
6. **全量 pytest / `scripts/dev_test.sh`**：按约束未跑，只跑上述定向批次。

## 四、明天与用户一起做真实验收（V01–V10）

前置：在 `/Users/apple/Desktop/2026/envoy-a` 用干净环境装一次可编辑包；`ENVOY_ASR_CHECKPOINT_DIR` 指向临时目录便于观察断点。

- **V01 断点身份换目录仍命中**：同一段音视频在两个不同临时目录各跑一次，第二次日志/返回里 `cached` 或 `resumed` 为真、断点文件名一致（观察 `~/.envoy/asr` 下 JSON 的 `identity`）。
- **V02 内容变化即失效**：改一段音频（重编码/截断）再跑，生成新断点文件，旧断点不被续用。
- **V03 续跑不回退**：跑到中途 Ctrl-C / 让预算耗尽，再跑一次；检查新断点 `next_seconds` ≥ 旧值，片段无重复（同一 `(start,end)` 只出现一次）。
- **V04 成功后不重复计费**：完整转写后再跑同源，命中 complete 缓存，耗时接近 0。
- **V05 部分窗口不误判音乐**：对一条长视频只读前 1–2 分钟（`scope.end_seconds`），确认不再报"整段几乎无语音"，`transcript_confidence` 为 normal，提示里写"已转写的 Ns"。
- **V06 collect 子预算生效**：`envoy collect --url <profile> --out <file> --max-work-seconds 5`，用 `--json` 看子项 `budget`/耗时；总耗时应落在 5s 量级而不是 180s。
- **V07 预算耗尽可续跑**：把 `--max-work-seconds` 设很小，跑完后剩余项仍是 `selected`；再次执行同一命令从原地继续，不从头重读。
- **V08 记账可读**：`envoy collection-checkpoint` 读回该 run，`usage.work_seconds` 随运行增长，`applied_operations` 里有 `collect-work-<run>-…`。
- **V09 登录暂停不烧预算**：故意给一个需要登录的链接，确认暂停后剩余项仍 selected，且记账没有把等待时间算进去。
- **V10 回滚验证**：把本轮三个 commit revert 后跑同一条长视频，确认 V01/V03/V06 的行为退回旧版（作为改动确实生效的反证），再恢复。

每项记录：命令、期望、实际、耗时。V01/V03/V05/V06 是关键门禁；任何一项不过，先停在那一项，不要继续往后跑。

## 五、Lead 在共享文件里要做的集成（我不动这些）

1. **合并**：`git merge wt/envoy-a`（或 cherry-pick `a9666cb`、`2d9e8c1`、`9b04c4d`）。三笔提交互不冲突，均只碰 `media.py` / `reader.py` / `collect.py` / `tests/cli/`。
2. **公开接口兼容**：`_transcript_confidence` 新增的是仅关键字参数、默认值保持旧行为，`reader.py` 之外的调用方（若有）无需改；`transcribe_audio_file` 新增 `source_revision=None`，旧调用方不受影响。
3. **checkpoint 目录**：断点文件名从路径/大小/mtime 键改为内容/可信 revision 键，老断点文件不会再被命中，可以安全清理（建议发布说明里提一句）。
4. **CLI 无需改动**：`collect` 的 `--max-work-seconds` 已由 `cli.py` 传入（`args.max_work_seconds`），本轮只改了被调用侧；`read` 侧的 `Budget` 默认值未改。
5. **文档/变更记录**：建议在 release note 写"A1 断点续跑按内容身份"、"A3 collect 预算真实下传"两条；不改版本号，由 Lead 决定发版节奏。
6. **发版冒烟**：合并后至少跑本报告第二节前 4 条命令（约 25s）确认无回归，再走真实 V01–V10。

## 六、风险与回滚

- 断点身份改为内容摘要后，无 revision 的本地音频首次会多读一遍文件（sha256 流式，1 MiB 分块，几十秒音频可忽略；长音频成本低于重转写）。
- `collect` 的 `break` 语义依赖 `max_work_seconds` 是"处理预算"而非"墙钟上限"：登录暂停、写盘、渲染不计入，符合原 API 语义；若 Lead 希望含全部墙钟时间，需要再讨论。
- 回滚：三笔提交均为独立小提交，`git revert` 即可；`tests/cli/` 新增文件不影响运行期。
