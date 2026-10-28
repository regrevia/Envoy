# Windows 交接单：在一台 PowerShell 7 机器上验收 v0.1.25 的 `install.ps1`

这份是给**在 Windows 上干活的 agent** 看的，自包含：不需要仓库、不需要 macOS 那边的东西，
只要这台机器能上网。目标只有一句：**证明发行版 `install.ps1` 在真实 Windows 上能装出可用
runtime，并把证据原样带回来**。对应实测计划条目 **I03（Windows x64 干净装）**。

不要写代码、不要改仓库、不要发版。只执行命令、记录输出。

---

## 1. 边界（先看这条）

- **不要**执行任何长视频 / 长 PDF / 大模型任务：本次只验收安装，不是指标复跑。
- **不要**改 `install.ps1`、不要改 `SHA256SUMS`、不要 `npm publish`、不要动 git。
- **不要**用管理员权限安装（脚本设计成 per-user：`%LOCALAPPDATA%` + `%USERPROFILE%\.envoy`）。
- 每条命令的实际输出（stdout+stderr）和退出码都要留在 transcript 里，**不要改写、不要润色**。
- 装完之后**不要**手工删 `%USERPROFILE%\.envoy` 或 runtime 目录；这是证据。
- 机器上原本就装有 envoy 的话（旧 0.1.23），直接在 §3 之前记录现状，然后照做——
  「旧 runtime 不得抢 PATH」正是要观测的点之一。

## 2. 前置：确认这台机器能装

在 PowerShell 7 里整段粘：

```powershell
$PSVersionTable.PSVersion.ToString()
[Environment]::OSVersion.VersionString
$env:PROCESSOR_ARCHITECTURE
whoami
(Get-Command uv  -ErrorAction SilentlyContinue).Source
(Get-Command python -ErrorAction SilentlyContinue).Source
(Get-Command envoy -ErrorAction SilentlyContinue).Source
Test-Path "$HOME\.envoy\runtime-path"
Test-Path "$env:LOCALAPPDATA\envoy-dsh\runtime"
```

判据：`uv` 或 `python`（3.11+）**至少有一个存在**，否则先装 `uv`（`winget install astral-sh.uv`
或 `pipx install uv`），脚本会以「neither uv nor python is available」直接拒绝安装，
这属于环境缺依赖，不是安装器缺陷。其余项只是记录现状。

开一份 transcript，后面所有命令都在它里面跑：

```powershell
Start-Transcript -Path "$HOME\envoy-install-$(Get-Date -Format yyyyMMdd-HHmm).log"
```

## 3. 取发行文件并校验（发行物就是交付物，别从 main 拿脚本）

```powershell
$dst = "$HOME\envoy-0.1.25"; New-Item -ItemType Directory -Path $dst -Force | Out-Null
Set-Location $dst
$base = "https://github.com/regrevia/Envoy/releases/download/v0.1.25"
foreach ($f in "install.ps1","SHA256SUMS","release-manifest.json") {
  Invoke-WebRequest -Uri "$base/$f" -OutFile $f -UseBasicParsing -TimeoutSec 120
}
Get-FileHash install.ps1 -Algorithm SHA256 | ForEach-Object { "$($_.Hash.ToLower()) install.ps1" }
Get-Content SHA256SUMS
```

断言：`install.ps1` 的哈希必须等于

```
25936ae5dd4b4c1a34dc0cae925fb8663c7fce66b463c25f89b6524e7788fe3c
```

（`SHA256SUMS` 里那行就是它；`envoy_cli-0.1.25-py3-none-any.whl` 是
`3655b6d4680187ceda1883dc9c66a23119b539e350b90b311b7fc825be5d22f2`，脚本会自己再核一次。）
对不上就停，把两行哈希原样带回来。

## 4. 干净机先做「离线探针」（有旧安装就跳过这步）

目的是验「读不到 release manifest 时明确报错、且不留下半截 pin」：

```powershell
$env:ENVOY_MANIFEST_URL = "http://127.0.0.1:9/manifest.json"
powershell -ExecutionPolicy Bypass -File .\install.ps1 skill; "EXIT=$LASTEXITCODE"
Test-Path "$HOME\.envoy\runtime-path"
Remove-Item Env:\ENVOY_MANIFEST_URL
```

断言：输出含 `could not read the release manifest; set ENVOY_VERSION`，`EXIT=1`，
`Test-Path` 为 `False`（没有写出 pin）。若机器上已有旧 pin，就只记录报错文本和退出码，
并把这条标成 `not_applicable_preexisting_install`。

## 5. 真装（I03 主体）

用 `skill` 分支做验收：它只装 runtime + 浏览器 + skill 包，不往宿主机 CLI 里塞插件，
避免在验收机上制造额外副作用。**不要**无参数跑 `install.ps1`——那会按检测结果自动装
Claude Code / Codex 插件（那是 I11/I12，不在本次范围）。

```powershell
Set-Location "$HOME\envoy-0.1.25"
$t0 = Get-Date
powershell -ExecutionPolicy Bypass -File .\install.ps1 skill; "EXIT=$LASTEXITCODE"
"SECONDS=" + [int]((Get-Date) - $t0).TotalSeconds
```

预期输出顺序（措辞可有差异，语义要对）：`envoy install: runtime 0.1.25 -> ...`、venv 与
pip 安装、`pin ... -> C:\Users\<you>\.envoy\runtime-path`、`shim ...\envoy.cmd -> ...`、
`added ...\.envoy\bin to the user PATH (open a new terminal)`、
`browser runtime (one-time, about 1-2 minutes)`。浏览器下载失败**不算安装失败**（脚本降级为
警告并提示之后跑 `envoy browser setup`），要如实记录它是成功还是降级。

## 6. 断言清单（逐条记录 `OK` / `FAIL` + 原始输出）

**新开一个 PowerShell 7 窗口**（PATH 只有新进程才刷新），跑：

```powershell
where.exe envoy
envoy --version
Get-Content "$HOME\.envoy\runtime-path"
Get-Content "$HOME\.envoy\bin\envoy.cmd"
(Get-Item "$HOME\.envoy\runtime-path").LastWriteTime
[Environment]::GetEnvironmentVariable("Path","User") -split ";" | Where-Object { $_ -like "*.envoy*" }
envoy doctor --json | Out-String
```

| # | 断言 | 期望 |
|---|------|------|
| A1 | `where.exe envoy` | 第一行是 `C:\Users\<you>\.envoy\bin\envoy.cmd` |
| A2 | `envoy --version` | `envoy 0.1.25` |
| A3 | pin 文件 | 一行绝对路径，指向存在的 `...\envoy-dsh\runtime\Scripts\envoy.exe` |
| A4 | shim 内容 | 两行：`@echo off` + `"<runtime>\envoy.exe" %*` |
| A5 | 用户 PATH | 恰好含 `...\.envoy\bin`，且**没有**重复项、没有旧的 0.1.23 runtime 目录 |
| A6 | `envoy doctor --json` | 合法 JSON；`ok`/`ready` 为 true，`version` 为 `0.1.25`，浏览器项为 ready（若 §5 降级则记为 `browser_not_ready_with_reason`） |
| A7 | 插件/宿主未被改写 | 机器上没有多出来的 claude/codex 插件目录（本步只装 skill） |

`envoy doctor --json` 的完整输出要原样贴回（这是唯一能判浏览器与运行时是否真就绪的证据）。

## 7. 幂等重跑

```powershell
Set-Location "$HOME\envoy-0.1.25"
powershell -ExecutionPolicy Bypass -File .\install.ps1 skill; "EXIT=$LASTEXITCODE"
```

断言：输出含 `using envoy 0.1.25 from ...\Scripts\envoy.exe (pinned)`，
**不重新下载 wheel、不重建 venv**，pin 内容不变（对比 §6 的 `LastWriteTime`）。
PATH 不得出现第二条 `.envoy\bin`（A5 复检）。这条同时覆盖 I08 的 Windows 侧半边
（中断重跑的完整语义只在中途 Ctrl-C 才成立，本次不做，别声称做过）。

## 8. 可选探针（有时间再做，各条独立，失败也只是信息）

- **S1 路径含中文与空格**：`$env:ENVOY_HOME="$HOME\envoy 测试\runtime"; $env:ENVOY_RUNTIME_PIN="$HOME\envoy 测试\pin.txt"`，
  再跑一次 `install.ps1 skill`，断言 pin/shim 路径没被空格截断；做完删掉这两个环境变量。
- **S2 跳过浏览器**：`$env:ENVOY_SETUP_BROWSER="0"` 在**全新** `ENVOY_HOME` 下跑，断言出现
  `skipped browser setup`；`envoy doctor` 会报浏览器未就绪——这是预期，不是缺陷。
- **S3 固定版本**：`$env:ENVOY_VERSION="0.1.25"` 时不请求 manifest 也能装（断网也能进到下载步骤）。
- 探针若污染了用户环境（改了 PATH、动了默认 pin），做完要说明改了什么、怎么恢复的。

## 9. 带回来的证据（就这一个文件）

把结果写成 UTF-8 JSON，保存到 `$HOME\envoy-0.1.25\windows-evidence.json`，**并在回话里贴全文**：

```json
{
  "machine": {"ps_version": "", "os": "", "arch": "", "user": ""},
  "entry": "I03",
  "started_at": "2026-10-28T12:00:00+08:00",
  "duration_seconds": 0,
  "install_ps1_sha256": "25936ae5...",
  "prereq": {"uv": "", "python": "", "preexisting_envoy": ""},
  "offline_probe": {"ran": true, "message": "", "exit_code": 1, "pin_written": false},
  "dry_run": {"ran": false, "note": "ENVOY_DRY_RUN=1 可选，跑了就贴前 20 行"},
  "install": {"command": "install.ps1 skill", "exit_code": 0, "browser": "ready|degraded", "log_excerpt": []},
  "checks": {"A1": {"ok": true, "observed": ""}, "A2": {}, "A3": {}, "A4": {}, "A5": {}, "A6": {"json": {}}, "A7": {}},
  "rerun": {"exit_code": 0, "reused_pinned_runtime": true, "pin_changed": false, "path_duplicated": false},
  "probes": [],
  "failures": [],
  "transcript_path": "C:\\Users\\...\\envoy-install-20261028-1200.log"
}
```

`checks` 里每个键都填 `{"ok": true|false, "observed": "<原样输出或关键行>"}`，不要只写结论。
`failures` 里每条写 `{"check": "A6", "symptom": "", "raw": "", "suspected_cause": "", "reproducible": true}`。

最后：

```powershell
Stop-Transcript
```

把 transcript 路径和 `windows-evidence.json` 全文一起交回。**不要**自己改实测计划的
`status`——记录由 macOS 侧用 `record_experiment.py` 统一写回，避免两边格式打架。

## 10. 已知坑（按这个顺序排查）

1. `powershell -File .\install.ps1 -DryRun` **会失败**：脚本没有 `-DryRun` 开关，`-DryRun`
   会被当成宿主名，直接打印用法并 `exit 2`。干跑的正确写法是 `$env:ENVOY_DRY_RUN="1"` 再跑。
2. `where.exe envoy` 命中的是旧 0.1.23 路径：说明新装的 shim 没进 PATH 或旧的在 PATH 更前面，
   记录 `[Environment]::GetEnvironmentVariable("Path","User")` 原文，别手工删路径。
3. 装完当前窗口里 `envoy` 仍不可用：路径写进的是「用户 PATH」，只对**新进程**生效，开新窗口再试。
4. `ExecutionPolicy` 拦脚本：一律用 `powershell -ExecutionPolicy Bypass -File .\install.ps1 ...`，
   不要 `Set-ExecutionPolicy` 改机器策略。
5. 浏览器下载慢或被拦：只要 §5 打出降级警告并给出 `envoy browser setup` 提示，就算通过（记 `degraded`）。
6. 中文/空格路径异常：先确认用的是 PS7 而不是 `powershell.exe` 5.1，再把 `$env:ENVOY_HOME` 原文贴回。
7. 任何 `pip` 报 TLS/代理错误：记录 `$env:HTTP_PROXY`/`$env:HTTPS_PROXY`/`netsh winhttp show proxy` 输出，别自行改代理。

## 11. 对应关系（给记录方看，执行方不用管）

- 本条主体 → 计划 `axes.install.entries` 的 **I03**（`requires: user_present,network,windows_host`，
  `expected: 与 macOS 同等结果，envoy.cmd 进用户 PATH`）。
- §4 离线探针 ≈ I10 的 Windows 侧观测；§7 ≈ I08 的 Windows 侧半边；§8 S1 ≈ I06、S2/S3 为补充探针。
  这些**只作为补充证据**写进 I03 的 `notes`，不得把它们各自标成 I10/I06/I08 的 `unit_checked` 或
  `passed`——那几条的完整判据在 macOS/Linux 侧，且需要人在场逐条确认。
- 证据回来后由 macOS 侧执行：
  `python3 record_experiment.py --plan EXPERIMENT-PLAN.json --id I03 --status ... --evidence ...`
  （确切开关见该脚本 `--help`，不要凭记忆写）。
