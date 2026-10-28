# 轻量性问题反馈（Windows 侧实测，转 macOS 侧处理）

- 来源：Windows 11 / PowerShell 7.6.9 / CPython 3.11.9 真机，代码基线 `84f247c`（v0.1.24）
- 分工：**Windows 适配与可用性由 Windows 侧负责**（`install.ps1`、进程/路径/编码/权限面）；
  **本文只报轻量性问题**，即启动开销、依赖体积、模块结构，归 macOS 侧。
- 本文不附改动，只给测量方法与建议；数字均可按文中命令复现。

## 一、启动开销：每次调用固定付 ~650–720 ms

`envoy` 是「宿主每次工具调用都起一个新进程」的形态，所以 import 期成本是逐次支付的，不是一次性的。

实测（本机，冷热各三次，`PYTHONPATH` 指向 `packages/*/src`）：

| 命令 | 端到端 | 其中 import |
| --- | --- | --- |
| `envoy --version` | **657 ms** | ~650 ms |
| `envoy doctor --json` | **767 ms** | ~650 ms |
| `python -c "import envoy_cli.cli"` | 724 / 646 / 651 ms | — |

`-X importtime` 的累计时间排序（cumulative，微秒）：

```
549699  envoy_cli.cli
280203    envoy_cli.mcp
274789      envoy_cli.local_source
273966        envoy_cli.reader        ← 单文件 350 KB，import 期就跑完整个模块体
219455    envoy_core.schemas.read_v1
136654      httpx                    ← 只有读网页才需要
129352      pypdf                    ← 只有读 PDF 才需要
 86103        pypdf._doc_common
 63165      pydantic_core
```

结论：`cli` → `mcp` → `local_source` → `reader` 这条链在**任何子命令**下都被完整加载，
而 `httpx`（213 ms 单测）和 `pypdf`（175 ms 单测）只在 PDF/网页读路径上才用得到。

### 建议 L1：把 `httpx` / `pypdf` 改成函数内延迟 import

只动 `reader.py`（和用到它们的少数模块）的模块顶部 import，改为在真正发起请求 / 解析 PDF 的函数里 import。
预期收益：`--version`、`doctor`、`hook`、`path`、`browser` 这些高频命令砍掉约 200–400 ms；
读路径首次多一次 import，总时间不变。这是本组建议里性价比最高的一条。

### 建议 L2：`envoy --version` 走快速路径

`--version` / `path` / `--help` 不碰 reader，可在 `cli.main()` 解析完 argv 之前先判这三个开关，
直接打印后返回，完全不 import `mcp`/`reader`。预期 `--version` 从 657 ms 降到 100 ms 量级。
（宿主的健康检查、脚本里的版本探测会反复调它。）

### 建议 L3：拆分 `reader.py`

单文件 350 KB / `mcp.py` 90 KB，既是 import 期成本的结构性原因，也让任何读路径改动都要过一遍大文件。
建议按「取材料 / 解析（HTML、PDF、媒体）/ 组装证据」拆成子模块，`reader` 只留对外门面。
拆分本身不改变行为，但能让 L1 的延迟 import 落在更小的边界上。

## 二、安装体积：Chromium 是最大头，且被描述成「1–2 分钟」

实测本机 `%LOCALAPPDATA%\ms-playwright`：

| 目录 | 体积 |
| --- | --- |
| `chromium-1223`（全量 headed） | **412 MB** |
| `chromium_headless_shell-1223` | **267 MB** |
| `ffmpeg-1011` | 3 MB |
| **合计** | **683 MB** |

发行 wheel 与运行时依赖（`pip download --no-deps` 实测）：

| 包 | wheel |
| --- | --- |
| `playwright`（本体，不含浏览器） | **37.0 MB** |
| `yt-dlp` | 3.0 MB |
| `pydantic` + `pydantic-core` | 8.8 MB |
| `pypdf` | 3.3 MB |
| `httpx` | 0.7 MB |
| 可选 `openai-whisper` 带入的 `torch` | **447 MB**（本机实测目录体积） |

`install.ps1` / `install.sh` 对浏览器的措辞是 `browser runtime (one-time, about 1-2 minutes)`，
实测 683 MB 在普通家宽上远不止 1–2 分钟，用户会以为卡死。

### 建议 L4：默认只装 headless shell

`playwright install chromium --only-shell`（或装完删掉全量 `chromium-*` 目录）可省约 **412 MB**。
Envoy 的读路径默认就是 headless；只有「需要用户接管登录」时才要 headed 窗口，
那时再按需补装全量。这既省 400 MB，也不影响既有能力。

### 建议 L5：安装期的体积与时间预期要写实

把 `about 1-2 minutes` 换成带体积的说法（例如 `Chromium ~270 MB / ~680 MB（含 headed）`），
并在 `envoy doctor` 里报「浏览器占用多少 MB」，用户能判断该不该跳（`ENVOY_SETUP_BROWSER=0`）。

### 建议 L6：`torch` 不应在默认安装路径上出现

`openai-whisper` 是 optional（`[media]`），但要确保任何一键安装路径都不会顺手把它拉进来。
447 MB 对一个「装完就能读链接」的工具过重；建议 `envoy asr install` 保持显式、且安装前先报预计体积。

## 三、依赖面

`pyproject.toml` 的必装依赖是 `httpx` / `pypdf` / `pydantic` / `yt-dlp` / `playwright`。
其中 `yt-dlp` 和 `playwright` 只服务视频与动态页，纯图文/PDF 用户用不到。

### 建议 L7：考虑把 `playwright` 与 `yt-dlp` 降为「首次用到时提示安装」

按现有 `missing_capability` 的既有设计（缺浏览器时读路径快速失败并给 `envoy browser setup`），
这两项完全可以复用同一套语义：缺 `yt-dlp` 时对视频链接报 `missing_capability` + 安装提示，
而不是让每个装 Envoy 的人先付 40 MB 的 wheel。

## 四、复现命令

```powershell
# 启动开销
$env:PYTHONPATH="<repo>\packages\envoy-cli\src;<repo>\packages\envoy-core\src"
python -X importtime -c "import envoy_cli.cli" 2>&1 | Sort-Object { [int](($_ -split '\|')[1].Trim()) } -Descending | Select-Object -First 20

# 浏览器体积
Get-ChildItem "$env:LOCALAPPDATA\ms-playwright" -Directory | ForEach-Object {
  $sz = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum
  "{0,-32} {1,8:N0} MB" -f $_.Name, ($sz/1MB)
}

# wheel 体积
python -m pip download --no-deps --only-binary=:all: --dest $env:TEMP\wheels yt-dlp playwright
```

## 五、边界

- 以上数字来自一台机器一次采样，不是基准测试；结论（谁贵、贵多少的量级）稳定，具体毫秒数会随磁盘冷热浮动。
- 本文不涉及 Windows 适配问题（`install.ps1` 干跑失败、PATH 编码降级、ASCII 丢中文等），那些由 Windows 侧另行修复。

## 六、顺带发现：`install.sh` 与 CLI 的 pin 路径约定不一致（需 mac 侧确认）

这不是轻量性问题，但两边必须一致，故一并记在这里。

CLI 侧 `install_state.pin_file()` 是 **`<ENVOY_HOME>/runtime-path`**（设了 `ENVOY_HOME` 时跟随它）：

```text
pin_file (no ENVOY_HOME): C:\Users\...\.envoy\runtime-path
pin_file (ENVOY_HOME=xyz): C:\Users\...\AppData\Local\Temp\xyz\runtime-path
```

而 `install.sh`（[integrations/install.sh:40](integrations/install.sh#L40)）写死 `$HOME/.envoy/runtime-path`，
**不随 `ENVOY_HOME`**。结果：任何设了 `ENVOY_HOME` 的用户装完后，`envoy doctor` 报 `unpinned`
（pin 写到了 A，doctor 去 B 找）。

Windows 侧已改为跟随 `ENVOY_HOME`（`$Script:EnvoyHome`），与 CLI 对齐并在真机验证 `doctor` 报 `pinned`。
`install.sh` 建议做同样一处改动（`RUNTIME_PIN="${ENVOY_RUNTIME_PIN:-${ENVOY_HOME:-$HOME/.envoy}/runtime-path}"`），
否则两平台在同一台机器上行为仍会分叉。默认不设 `ENVOY_HOME` 时两者都是 `~/.envoy/runtime-path`，所以这是
「只在用户显式搬迁运行时才暴露」的缺陷。
