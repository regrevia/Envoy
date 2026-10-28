# Envoy 轻量化：Windows 侧现状与优化建议

- 基线 commit：`84f247c`（main，版本 0.1.24）
- 本文面向用户与 Windows 侧同事：第 2、3 节是 Windows 真机实测；第 2 节同时列出 mac 侧同口径数字用于对照结构，**两侧不可直接相减**（见第 1 节）。

## 1. 环境与测量口径

| 侧 | 环境 | 测量人/来源 |
| --- | --- | --- |
| Windows | Win11 / PowerShell 7.6.9 / CPython 3.11.9 | Windows 侧同事真机实测，已知值，本文直接引用，未复测 |
| mac | macOS Darwin arm64 / CPython 3.11.15 | 本机 `scripts/measure_startup.py`，5 次采样取中位数 |

mac 侧数字来自本机 [DEV/lightweight-measurements.json](/Users/apple/Desktop/2026/Envoy/DEV/lightweight-measurements.json)（2026-10-28 生成）。口径：

```bash
cd /Users/apple/Desktop/2026/Envoy
packages/envoy-cli/.venv/bin/python scripts/measure_startup.py \
  --repeat 5 --json DEV/lightweight-measurements.json
```

- 直接使用仓库内解释器 `packages/envoy-cli/.venv/bin/python`，`PYTHONPATH=<repo>/packages/envoy-core/src:<repo>/packages/envoy-cli/src`，入口统一 `-c "from envoy_cli.cli import entrypoint; entrypoint()"`。
- 计时是子进程墙钟（`perf_counter` 包住 `subprocess.run`），含解释器启动；每例 5 次，**不剔除首次冷启动**，因此 min 更接近热态、max 会带一次冷态。
- `doctor --json` 允许失败：脚本记录 returncode 与耗时，不因非零退出中断。

**两侧不能相减的原因**：硬件（Apple Silicon vs x86/ARM Windows 机器）、文件系统与杀毒软件实时扫描、NTFS 元数据开销、Python 构建（python.org 3.11.9 vs uv 3.11.15）、冷热缓存都不同。可比的只有**结构与占比**（import 链长度、体积构成、哪一段最贵），不能把 657 ms 与 130 ms 的差值解释成“Windows 慢多少”。

## 2. 启动耗时

### 2.1 墙钟耗时：Windows 实测 vs mac 同口径

Windows 列为已知实测值（直接引用，未复测）；mac 列为改动后本机 `scripts/measure_startup.py --repeat 7` 的中位数（`DEV/lightweight-measurements.json`）。

| 命令 | Windows | mac（中位数） |
| --- | ---: | ---: |
| `envoy --version` | 657 ms | **109.1 ms**（改动前 130.1 ms） |
| `envoy doctor --json` | 767 ms | 394.8 ms（改动前 368.4 ms，L3 未做） |
| `python -c "import envoy_cli.cli"` | 724 / 646 / 651 ms | **106.8 ms**（改动前 127.8 ms） |
| `python -X importtime -c "import envoy_cli.cli"` | 见下表累计值 | 110.1 ms |

`--version` 的 657 ms 里约 650 ms 是 import 链：`envoy_cli.cli → envoy_cli.mcp → envoy_cli.local_source → envoy_cli.reader`。两侧结构一致：改动前 mac 上 `--version`（130.1 ms）与裸 `import envoy_cli.cli`（127.8 ms）几乎相同，说明 `--version` 并没有走任何“轻”路径，它和真正读一个链接付了几乎相同的 import 代价；这也是 L2 收益推算的依据。

### 2.2 import 累计耗时对照（`-X importtime` 的 cumulative 列）

Windows 为实测 µs；mac 为 JSON 中 5 次运行的中位数（精确模块名）。空白表示该模块未进入 mac 的 top 15。

| 模块 | Windows 累计 (µs) | mac 累计 (ms) | 备注 |
| --- | ---: | ---: | --- |
| `envoy_cli.cli` | 549699 | 102.7 | 入口，链上最大节点 |
| `envoy_cli.mcp` | 280203 | 29.1 | MCP/工具面 |
| `envoy_cli.local_source` | 274789 | 27.7 | 本地文件分类（B1 新增） |
| `envoy_cli.reader` | 273966 | 27.3 | 阅读器主体 |
| `envoy_core.schemas.read_v1` | 219455 | 52.7 | 响应 schema，pydantic 建模 |
| `httpx` | 136654 | — | 网络客户端，默认依赖 |
| `pypdf` | 129352 | 19.1 | PDF 解析，默认依赖 |

结论（两侧一致）：import 成本集中在 **`envoy_cli.cli` → `mcp`/`local_source`/`reader` 这条链**，以及被 `read_v1` 拉进来的 pydantic、被 reader 拉进来的 pypdf/httpx。`--version`、`doctor`、`path` 这类不读内容的命令却要付整条链的钱，是 L2/L3 的直接依据。

## 3. 安装体积（Windows 实测）

| 组件 | 体积 | 来源/说明 |
| --- | ---: | --- |
| Playwright Chromium 合计 | 683 MB | `chromium-1223` 412 MB（headed）+ `chromium_headless_shell-1223` 267 MB + `ffmpeg-1011` 3 MB |
| pip 包 `playwright` | 37.0 MB | 浏览器驱动本体，不含上面的浏览器资产 |
| pip 包 `yt-dlp` | 3.0 MB | 平台视频解析 |
| pip 包 `pydantic`（含 `pydantic-core`） | 8.8 MB | schema 校验 |
| pip 包 `pypdf` | 3.3 MB | PDF |
| pip 包 `httpx` | 0.7 MB | 网络 |
| pip 包 `torch` | 447 MB | 仅 `[media]` extra 引入（含 `openai-whisper`） |

体积大头是浏览器资产（683 MB）与 `[media]` 的 torch（447 MB），两者都不是“读一个网页/PDF”所必需。

## 4. 依赖面

`packages/envoy-cli/pyproject.toml` 当前把 `httpx`、`pypdf`、`pydantic`、`yt-dlp`、`playwright` 全部放在默认 `dependencies`；`[media]` 只声明 `openai-whisper`（它会把 torch 拉进来）；另有 `[ocr]` / `[ocr-vision]` / `[ocr-rapid]` 三个可选 extra（Pillow 等）。

| 能力 | 默认路径 | 可选路径 |
| --- | --- | --- |
| 读网页 / HTTP 抓取 | `httpx`（默认） | — |
| 读 PDF | `pypdf`（默认） | — |
| schema/结构化输出 | `pydantic` + `pydantic-core`（默认） | — |
| MCP / 浏览器抓取、截图 | `playwright` pip 包 + 下载 Chromium 683 MB（默认） | 可改为按需安装（L4/L7） |
| 平台视频（YouTube 等） | `yt-dlp`（默认） | 可降级为可选（L7） |
| 音视频转写（Whisper） | 不装 | `[media]` → `openai-whisper` → torch 447 MB |
| 本地图片 OCR | 不装 | `[ocr]` / `[ocr-vision]` / `[ocr-rapid]` |

## 5. 建议 L1–L7

状态按 `implemented` / `unit_checked` / `packaged` / `not_run` 标注；能测的只测了 mac，**改动后的 Windows 数字仍未实测**（见第 6 节）。预计收益的基线是上表的 Windows 实测 importtime 与体积。

### L1 把 `httpx` / `pypdf` 改成惰性导入

- **改什么**：`reader.py` 顶层的 `import httpx`、`from pypdf import PdfReader` 移到首次使用处；新增 `_lazy_module.py`，用 PEP 562 模块级 `__getattr__` 提供代理，保持 `reader.httpx` / `reader.PdfReader` 的既有访问方式（测试和 monkeypatch 不用大改）。
- **预计收益**：省掉 `httpx` 累计 136 ms + `pypdf` 累计 129 ms（Windows importtime），`--version`/`doctor`/纯网页读取都不再付 PDF 与部分网络的 import 钱。
- **风险**：缺依赖的错误从启动时推迟到首次使用；类型检查需要 `TYPE_CHECKING` 或字符串注解；测试里对 `reader.httpx`/`reader.PdfReader` 的 monkeypatch 语义要确认代理可写；第三方可能直接 `from envoy_cli.reader import PdfReader`，代理必须支持。
- **归属**：mac 核心（`reader.py` + 新 `_lazy_module.py`）。状态：`implemented` + `unit_checked`；mac 上 `import envoy_cli.reader` 后 `sys.modules` 无 httpx/pypdf（`tests/cli/test_lightweight_startup.py`），墙钟 `import envoy_cli.cli` 127.8 → 106.8 ms。Windows 侧未复测。

### L2 `--version` / `path` / `--help` 走 argv 快速路径

- **改什么**：`cli.py` 在 import `reader`/`mcp`/浏览器模块之前先看 `sys.argv[1:]`；命中 `--version`、`path`、`--help`/`-h` 时只用最小实现输出并退出，不 import 重量级模块。
- **预计收益**：`envoy --version` 657 ms → 约 150 ms 量级（去掉 ~650 ms import 链的大头）。
- **风险**：快速路径与 argparse 的帮助文本可能漂移（需以同源文案或测试锁住）；参数顺序/组合（`envoy --version extra`、`envoy -h`）要覆盖；Windows 控制台编码要显式 UTF-8。
- **归属**：mac 核心（`entry.py` + `cli.py` 兼容 shim）。状态：`implemented` + `unit_checked`；`envoy --version` 130.1 → 109.1 ms。改动比预计小：`--version` 原本也走 argparse，省下的是 read 栈的 import，不是整条 650 ms 链；Windows 侧未复测。

### L3 `envoy doctor --json` 复用快速路径 + 惰性探测

- **改什么**：doctor 同样先走 L2 的 argv 快速路径，再按需 import 各探测项所需模块（install/runtime 检查不需要 reader/pypdf/浏览器）。
- **预计收益**：doctor 767 ms 去掉大部分 import 链成本，接近 L2 的水平；`--json` 机器可读输出不变。
- **风险**：doctor 的检查项彼此独立，惰性化后错误信息要在对应检查里报，不能因 import 失败整体崩；JSON 字段必须保持兼容。
- **归属**：mac 核心（`cli.py`）。状态：`not_run`

### L4 Chromium 只装 headless shell 或按需安装

- **改什么**：安装脚本改为 `playwright install --only-shell`，不下载 412 MB 的 headed `chromium-1223`；或在首次真正需要浏览器时再装。
- **预计收益**：安装体积 683 MB → 约 270 MB（267 MB headless shell + 3 MB ffmpeg），省 412 MB。
- **风险**：依赖 headed Chromium 的路径（WebGL、部分反爬页面、视频帧、可视化调试）会失败；需逐个验证 MCP 浏览器功能；`--only-shell` 与 Playwright 版本绑定，升级时要同步。
- **归属**：Windows 侧（安装/打包），mac 侧不改。状态：`not_run`

### L5 `doctor` 输出体积报告

- **改什么**：`doctor` 增加各组件目录大小（浏览器缓存、模型、pip 包）与“是否可删/如何重装”的提示，最好支持 `--json` 字段。
- **预计收益**：不改善启动耗时，但让用户自己发现 683 MB/447 MB 的大头，减少“装完怎么这么大”的支持成本。
- **风险**：扫描大目录会拖慢 doctor（需限制深度/只统计已知目录）；不同平台路径不同（Windows `%LOCALAPPDATA%\ms-playwright`、mac `~/Library/Caches/ms-playwright`）。
- **归属**：mac 核心（`browser.py` + `cli.py` doctor），Windows 侧验证路径。状态：`implemented` + `unit_checked`；`doctor --json` 的 `browser.disk` 给出 `path` / `total_bytes` / `total_mb` / `components`，文本模式追加 ` (N MB on disk)`。只统计 Playwright 缓存根下已知组件目录，不扫全盘。

### L6 保持 torch 只在 `[media]`，默认路径永不 import

- **改什么**：不需要改功能，只需守住边界：默认安装与 import 链里不得出现 torch/whisper；用测试或 import 钩子锁住（例如断言 `sys.modules` 无 torch）。
- **预计收益**：默认安装不背 447 MB；启动不多付 whisper import。
- **风险**：`[media]` 内部若有人顺手顶层 import whisper 会让 `[media]` 用户启动变慢；需要一条轻量回归测试。
- **归属**：mac 核心（依赖/测试），Windows 侧验证。状态：`implemented` + `unit_checked`；`packages/envoy-cli/pyproject.toml` 的 `dependencies` 不含 whisper/torch，`openai-whisper` 只在 `[media]`，`tests/cli/test_lightweight_startup.py` 同时锁住元数据与 import 链（`envoy_cli.cli` / `reader` / `mcp` 之后 `sys.modules` 无 torch/whisper/openai）。

### L7 评估把 `playwright` / `yt-dlp` 降级为可选依赖

- **改什么**：只在评估层面做兼容性分析：把两者从默认 `dependencies` 移到 extra（如 `[browser]`、`[video]`）后，哪些命令/技能/MCP 工具会缺能力、报什么错、文档怎么改。**本次不直接改 `pyproject.toml`**。
- **预计收益**：核心安装省 `playwright` 37 MB + `yt-dlp` 3 MB 的 pip 体积，以及默认不再触发 683 MB 浏览器下载；对只读网页/PDF 的用户最明显。
- **风险**：MCP 浏览器与平台视频是既有卖点，降级会改变默认体验；错误提示、首次使用引导、打包/安装脚本、技能文档都要同步；需要版本兼容矩阵。
- **归属**：Lead/架构评估，Windows 侧真机验证。状态：`not_run`

## 6. 未验证

- **L1 / L2 / L5 的收益只在 mac 上实测**（上表括号内为改动前后同一台机器的中位数）；Windows 侧的改动后数字未复测，不能与新装机数据直接相减。
- **L3 未实现**：`doctor --json` 仍走完整 import 链，394.8 ms 与改动前的 368.4 ms 属同一水平（差异在噪声范围内）。
- **L4 / L7 未实现**，只是建议；`--only-shell` 对现有浏览器流程的兼容性、降级依赖后的能力缺失清单都没有验证。
- **Windows 真机验证由 Windows 侧同事执行**：本文所有 Windows 数字均为其提供的已知值，本机未复测。
- mac 数字来自单机、单次会话、5 次采样；换机器或系统负载不同会有波动。mac 侧 `torch`/`pypdf` 之外的组件未单独测体积（未测量）。
- 两侧环境不同，**禁止用 mac 与 Windows 的差值下结论**（见第 1 节）。
