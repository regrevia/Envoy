# 默认安装体积：≈178 MB（2026-11-06）

- 基线：`main` 工作区（HEAD `19edcbc`；版本仍为 `0.1.45`，本次不改版本、不改 CHANGELOG）
- 结论：`pip install envoy-cli` / 安装脚本默认只装 runtime（≈178 MB）；Chromium（≈683 MB）
  与 Whisper 权重（≈140 MB）改为首次使用时按需获取，缺失时报错里直接给出获取命令。
- 相关任务：`task-9`；体积数字的来源与「实测/未实测」边界见第三节，**不要把 178 MB 当成
  本机干净安装实测**。

## 一、验收目标

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 默认安装不含 Chromium 与 Whisper 权重 | 完成 | 安装器默认档 `minimal`；`pyproject.toml` extras 只加不减 |
| 缺 Chromium 时报错含 `envoy browser setup` | 完成 | 第四节；`tests/cli/test_minimal_install_capabilities.py` |
| 缺 Whisper 时报错含 `envoy asr install --model <model>` | 完成 | 第四节；同上 |
| `needs_user_action` 带上能力与命令 | 完成 | `details.needs_user_action = {kind, capability, command}` |
| 文档说明默认体积、首次获取命令、网络要求 | 完成 | `README.md`、`integrations/skills/envoy/INSTALL.md` |
| `pytest tests/cli -x -q` | 通过 | 第五节 |
| `check_versions.py --check` | 通过 | 第五节 |

## 二、改了什么

体积不是靠删依赖降下来的：`playwright`（pip 包）仍是核心依赖，默认安装里本来就没有
Chromium 二进制（683 MB）；Whisper 依赖与权重也只由 `asr install` 拉取。本次把「默认档」
从 `full` 翻成 `minimal`，并让两条「首次使用」路径的提示带上命令。

| 文件 | 变更 |
| --- | --- |
| `integrations/install.sh` | 默认 `ENVOY_PROFILE=minimal`；跳过浏览器/转写时打印要跑的命令 |
| `integrations/install.ps1` | 同上，`-Profile` 默认 `minimal` |
| `packages/envoy-cli/pyproject.toml` | 新增 additive extras：`browser`、`asr`、`full`（核心依赖不变） |
| `packages/envoy-cli/src/envoy_cli/browser.py` | `BrowserRuntimeUnavailable.next_action`；缺 Chromium 的报错写明一次性下载≈683 MB |
| `packages/envoy-cli/src/envoy_cli/reader.py` | 按报错文本为 `MISSING_CAPABILITY` 派生 `needs_user_action`（`action_required` 语义不动） |
| `packages/envoy-cli/src/envoy_cli/local_video.py` / `media.py` | 缺 Whisper 的报错补上 `envoy asr install --model <model>` |
| `README.md` / `INSTALL.md` | 默认档、首次获取命令、体积口径与平台差异 |
| `tests/test_install_script.py` / `tests/cli/test_installer_ps1_static.py` | 默认档断言改为 `minimal` |
| `tests/cli/test_minimal_install_capabilities.py` | 新增 8 条首次使用提示的测试 |

## 三、体积证据

口径先写清楚，避免把不同来源的数字混成一个「实测值」。

| 组成 | 数字 | 来源与性质 |
| --- | --- | --- |
| 默认安装（runtime） | **≈178 MB** | 既有口径：0.1.26 发行时在 macOS 上对本机 uv tool 目录做 `du -sm` 得到的 178 MB，其中最大一项是 Playwright 的 **pip 包**（本次现测 147 MB），不是浏览器二进制。这是**换算口径，不是本次干净安装的实测**。 |
| Chromium | ≈683 MB | 代码内的真机实测注释：`browser.py:383-388`「683 MB on the measured Windows host: 412 MB headed Chromium plus a 267 MB headless shell」。本机**未安装**（`~/.cache/ms-playwright` 不存在），因此没有本地数字。 |
| Whisper 权重 `base` | ≈140 MB | 上游 `base.pt` ≈139 MB；Windows 真机下载记录 145,262,807 B（`DEV/INSTALL-PROFILES.md` 第七节）。本机 `~/.cache/whisper` 为 0 B，未下载。 |

本机现测（2026-11-06，macOS arm64，`du`）：

```sh
$ du -sh packages/envoy-cli/.venv
241M    packages/envoy-cli/.venv            # 开发 venv：含 pytest/reportlab 等开发依赖，非默认安装口径

$ du -sm packages/envoy-cli/.venv/lib/python3.11/site-packages/playwright
147     .../site-packages/playwright        # 默认安装里最大的一项（pip 包，不是 Chromium）

$ du -sh ~/.envoy/asr
748K    /Users/apple/.envoy/asr              # ASR 状态/缓存目录，只有元数据

$ ls -d ~/.cache/ms-playwright ~/.cache/whisper
/Users/apple/.cache/whisper                  # 存在但为空
0B      /Users/apple/.cache/whisper
# ~/.cache/ms-playwright 不存在：本机从未通过 envoy 下载 Chromium
```

结论：默认安装不产生 683 MB 的浏览器下载，也不产生 140 MB 的权重下载；把三项都装齐时的
总量 ≈178 + 683 + 140 MB（后两项平台相关）。因此「从 ≈1 GB 降到 ≈178 MB」是**默认安装**
的对比，不是「装完所有能力」的对比。

## 四、首次使用时的提示（本次验收重点）

缺 Chromium（`browser.py:1068-1074`，`setup_required=True`）：

> Envoy's browser is not installed yet; run `envoy browser setup` once (one-time download of
> about 683 MB, 1-2 minutes) and retry. Reading did not start, so nothing was downloaded and
> this was not marked as a page failure.

缺 Whisper（`local_video.py`，经 reader 的 blocked 响应）：

> Local video ASR is unavailable because Whisper is not installed; run
> `envoy asr install --model base` once (about 140 MB) to enable it

这两条文本会经 `reader._capability_user_action()` 变成结构化的
`details.needs_user_action = {"kind": "install_capability", "capability": "browser" |
"speech_to_text", "command": "envoy browser setup" | "envoy asr install"}`，MCP 层再把
`next_action` 渲染成一行 `next: …`。派生只看 `ErrorCode.MISSING_CAPABILITY`，普通页面失败
（如 `EMPTY_CONTENT`）不会带安装命令——这一条也有测试守着，避免把「页面没有正文」误导成
「去 pip 安装」。

`errors[].action_required` 的语义没有改：它继续只对「需要人来做」的
`challenge_required` / `auth_required` 为 true；缺能力走结构化的 `needs_user_action`，
所以 `test_reader_media_binding.py` 里既有的 action_required 断言保持原样。

安装器侧的对应提示（跳过时不静默）：

```
envoy install: skipped browser setup (profile minimal); Chromium (about 683 MB) is fetched by
`envoy browser setup` the first time a page needs it
envoy install: skipped speech recognition (profile minimal); Whisper base is fetched by
`envoy asr install --model base` the first time a video needs it
```

网络要求：默认安装只需要能访问 PyPI（或镜像）；`envoy browser setup` 需要能访问
Playwright 的浏览器 CDN；`envoy asr install` 需要能访问 PyPI 与 Whisper 权重源。离线机器
应在安装时选 `--profile full`（或 `standard` + 预置权重），不要让首次使用去下载。

## 五、测试与检查

```sh
$ ENVOY_ASR_MODEL_CACHE=/tmp/envoy-asr-cache \
    packages/envoy-cli/.venv/bin/python -m pytest tests/cli -q
1206 passed in 58.46s
exit=0
```

**为什么带这个 env 覆盖**：本机 `~/.cache/whisper` 是指向外部盘
（`/Volumes/US202/caches/whisper`）的软链，该目录 `os.listdir` 会阻塞（本机实测 >10s，多次
复现），于是 `media.asr_model_slot()` 去那里开锁文件时卡住，
`tests/cli/test_media_asr_checkpoint.py::test_a_partial_checkpoint_resumes_at_its_last_chunk`
会挂死（`timeout 200` 也跑不完）。这是本机磁盘/挂载状态问题，不是本次改动引入的：该测试文件
在 `git status` 里干净、最后一次改动是 `a9666cb`，与本次 diff 无关。把模型目录指到 `/tmp`
后同一套用例 0.05s 跑完，全套 1206 个用例 58s 全绿。若日后 CI 或他人机器上重复出现挂死，
先查 `ENVOY_ASR_MODEL_CACHE` / `asr_model_cache_dir()` 指向的路径能否正常列目录。

```sh
$ python scripts/check_versions.py --check
authoritative version: 0.1.45 (packages/envoy-cli/pyproject.toml)
all 11 version declarations match (2 docs checked)

$ bash scripts/check_installer_offline.sh
12/12 installer checks passed
```

新增/更新的针对性用例（在完整套件里已包含，此处单列便于定位）：

```sh
$ packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_minimal_install_capabilities.py -x -q
8 passed

$ packages/envoy-cli/.venv/bin/python -m pytest tests/test_install_script.py \
    tests/cli/test_installer_ps1_static.py -x -q
36 passed

$ packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_reader_media_binding.py \
    tests/cli/test_browser_runtime.py tests/cli/test_browser_auto_setup.py \
    tests/cli/test_minimal_install_capabilities.py -q
100 passed
```

## 六、没做 / 边界

- 本机没有干净安装一次再 `du`：178 MB 是既有换算口径，683 MB / 140 MB 是代码注释与真机
  记录里的数字，已在第三节逐项标注来源与性质，未伪造「本次实测」。
- 没有在 Windows 真机重跑默认档（本机只有 macOS）；静态测试只保证两个安装器的默认值、
  跳过提示文本一致。
- 未改版本号、未改 `CHANGELOG.md`、未 `git commit`：提交与发行说明由 Lead 处理。
- 本页的代码引用以工作区为准；`reader.py`、`netpolicy.py` 等工作区里同时有另一位队友的改动，
  本页只描述与「缺能力时给出命令」相关的部分。
- 完整套件是在 `ENVOY_ASR_MODEL_CACHE=/tmp/…` 下跑绿的：本机默认模型目录在外部盘上列目录会
  阻塞（见第五节），与本改动无关；换到正常磁盘路径即为标准的
  `pytest tests/cli` 行为。
