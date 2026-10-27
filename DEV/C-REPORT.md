# C 线报告（安装 / 打包 / 跨平台）— 0.1.24

基线 `main = fbdede5`（0.1.23）→ 本轮 HEAD `65ba636`（0.1.24）。
交付目录：`/Users/apple/Desktop/2026/envoy-delivery/0.1.24/`。

## 1. 做了什么（按用户能感知的顺序）

| # | 变化 | 文件 | 状态 |
| --- | --- | --- | --- |
| C1 | `install.sh` 变成一条命令装完：固定单一 runtime、识别宿主（dsh/claude-code/codex）、安装期准备浏览器、无宿主时回落写 MCP 条目；新增 `path` 子命令与 8 个环境变量覆盖 | `integrations/install.sh` | `unit_checked`（离线用例 6/6） |
| C2 | 新增 `process_control.py`：Windows 用 `OpenProcess`/`GetExitCodeProcess` 探活、`taskkill /PID /T /F` 停止；auth 会话取消走同一条路径 | `packages/envoy-cli/src/envoy_cli/process_control.py`、`auth_session.py` | `unit_checked`（14 用例） |
| C3 | 读路径缺浏览器快速失败为 `MISSING_CAPABILITY` 并给 `envoy browser setup`；重下载留在安装期/独立 setup，不进宿主 300 s 时限 | `browser.py`、`cli.py` | `unit_checked`（9 用例） |
| C4 | 发行工作流补齐：先构建全部产物（含 DSH TGZ 与 `install.ps1`）再生成清单和 `SHA256SUMS`，新增 4 个 `--check` 门与 npm 发布步骤 | `.github/workflows/release.yml` | `packaged`（本机按同顺序复刻） |
| C5 | 新增 Windows 安装脚本，与 POSIX 脚本行为对齐（pin、`envoy.cmd`、PATH、MCP 配置合并带备份） | `integrations/install.ps1` | `not_run`（本机无 `pwsh`） |
| C6 | 离线安装用例脚本 | `scripts/check_installer_offline.sh` | 6/6 通过 |

## 2. 通过的检查（本轮真跑）

- 定向回归 19 个文件：**298 passed in 34.57s**（exit 0）。
- 媒体三件套（`test_video_media` + `test_media_asr_checkpoint` + `test_media_asr_resume`）：**37 passed in 3.99s**。
- `tests/cli/test_process_control.py`：**14 passed**（含注入 `nt` 平台的 Windows 分支）。
- `tests/cli/test_browser_auto_setup.py`：**9 passed**。
- `sh scripts/check_installer_offline.sh`：**6/6**（含「旧 PATH 不覆盖 pin」「无宿主回落 MCP」）。
- `check_versions.py --check`：11 项声明一致（2 份文档也查）。
- `check_release_manifest.py --check`：`Envoy 0.1.24, runtime >= 0.1.24`。
- `build_envoy_dsh_plugin.py --check`、`sync_host_skills.py --check`：均为 current。
- 打包产物可用性：`uv run --offline --with <wheel> envoy --version` → `envoy-cli 0.1.24 (core 0.1.24)`；`envoy doctor --json` 能跑通并在 `envoy --version` 场景下正确报版本。

## 3. 打包结果（release 渠道，源码提交 65ba636）

| 文件 | 大小 | SHA256 |
| --- | --- | --- |
| `envoy_cli-0.1.24-py3-none-any.whl` | 238992 | `562ae938864313e76a7663495e34078501b33b45ee7a6ffa9d1a08884a36479c` |
| `envoy-skill.zip` | 29531 | `9ba77acbc8f3cf668de7e7a114d04bf35d941497b391a4d5e4257c372bdd2bea` |
| `envoy-dsh-0.1.24.tgz` | 99188 | `43b3769f3b8e5154bd70d4e499111d157306b3c7e9d5fa1109f4d2b9d2425f4a` |
| `install.sh` | 19341 | `4d0a5dcaeca9de9db7f35f806e4268bd4330cebd200626e57633e9e4dc9b1972` |
| `install.ps1` | 20372 | `cae7d3e92fabdf8f6ce85a4aa3649e9831c94d979261edee0046b8234ebc84d1` |
| `release-manifest.json` | 979 | `c1f01c727df903ce828e93e940d74ccb714cda401dd64065c472c79a3d08562f` |

`SHA256SUMS` 覆盖上表 6 个文件；`release-manifest.json` 的 `artifacts` 覆盖 5 个
（清单不含自身）。产物名与安装脚本拼的 URL 一致：`envoy_cli-<v>-py3-none-any.whl`、
`envoy-dsh-<v>.tgz`、`envoy-skill.zip`。

## 4. 本轮没跑（=`not_run`，不得对外当通过）

- Windows x64 真机装（无 `pwsh`/`powershell`）、Linux x64 真机装；
- 真实 `envoy browser setup` Chromium 下载；
- 真实网页 / 长视频 / 长 PDF / ASR / OCR 端到端；
- GitHub Actions 发行作业本身（本机按步骤复刻了构建与校验和）；
- npm 发布（无 token）；
- 五个指标本轮均无新读数，对外文案不动。

## 5. 交付清单

`/Users/apple/Desktop/2026/envoy-delivery/0.1.24/`：
6 个发行资产 + `SHA256SUMS` + `COMPATIBILITY.md`（平台能力矩阵，逐项标
implemented / unit_checked / packaged / not_run）+ `DEVELOPMENT-REPORT.md` +
`EXPERIMENT-PLAN.json`（32 条实验，全部 `not_run`）+ `RUN-WHEN-PRESENT.md`
（人 + 网络在场时的执行顺序）。
