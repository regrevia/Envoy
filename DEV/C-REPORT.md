# C 线报告（安装 / 打包 / 跨平台）— 0.1.24

基线 0.1.23 发行版 → 0.1.24 交付提交（构建提交见 `release-manifest.json` 的 `source_commit`）。
交付目录：`/Users/apple/Desktop/2026/envoy-delivery/0.1.24/`。

## 1. 做了什么（按用户能感知的顺序）

| # | 变化 | 文件 | 状态 |
| --- | --- | --- | --- |
| C1 | `install.sh` 变成一条命令装完：固定单一 runtime、识别宿主（dsh/claude-code/codex）、安装期准备浏览器、无宿主时回落写 MCP 条目；新增 `path` 子命令与 8 个环境变量覆盖 | `integrations/install.sh` | `unit_checked`（离线用例 6/6） |
| C2 | 新增 `process_control.py`：Windows 用 `OpenProcess`/`GetExitCodeProcess` 探活、`taskkill /PID /T /F` 停止；auth 会话取消走同一条路径 | `packages/envoy-cli/src/envoy_cli/process_control.py`、`auth_session.py` | `unit_checked`（14 用例） |
| C3 | 读路径缺浏览器快速失败为 `MISSING_CAPABILITY` 并给 `envoy browser setup`；重下载留在安装期/独立 setup，不进宿主 300 s 时限 | `browser.py`、`cli.py` | `unit_checked`（9 用例） |
| C4 | 发行工作流补齐：先构建全部产物（含 DSH TGZ 与 `install.ps1`）再生成清单和 `SHA256SUMS`，新增 4 个 `--check` 门与 npm 发布步骤 | `.github/workflows/release.yml` | `packaged`（本机按同顺序复刻） |
| C5 | 新增 Windows 安装脚本，与 POSIX 脚本行为对齐（pin、`envoy.cmd`、PATH、MCP 配置合并带备份） | `integrations/install.ps1` | `not_run`（本机无 `pwsh`） |
| C6 | 离线安装用例脚本 | `scripts/check_installer_offline.sh` | 10/10 通过 |
| C7 | `envoy doctor` 新增 `runtime` 段：pin 状态（pinned / unpinned / stale_pin / shadowed）+ 每条一句修复动作，`--json` 同步给 `runtime.install` | `install_state.py`、`cli.py`、`INSTALL.md` | `unit_checked`（9 用例 + doctor 回归 12 用例） |
| C8 | 安装脚本收尾打印浏览器状态与安装状态（非 pinned 点名 PATH 指向），POSIX 与 Windows 两份对齐 | `install.sh`、`install.ps1` | `unit_checked`（10/10 离线用例，含 shadowed 分支） |
| C9 | 新增装前校验器 `verify_delivery.py`：离线核对摘要/清单/wheel 渠道、依赖与包内模块/tgz 版本/Skill 包/两个安装脚本的版本解析契约，一条命令替代手工比对 | `scripts/verify_delivery.py`、交付目录同名额 | `unit_checked`（10 用例，含摘要篡改、`+preview` 默认拒收/`--allow-preview` 放行、缺依赖拒收） |
| C10 | 留出集冻结工具 `freeze_holdout.py`：候选列表按种子抽样、去重排序、写 sha256 与行数组；默认拒写已有文件，`--check` 校验留出集未被替换；`--apply-plan` 把冻结结果直接写回计划（已有冻结留出集时拒写）；`--check --plan` 抓出手改过的计划；`holdout.dependent_entries` 列出量这个词条的条目 | `scripts/freeze_holdout.py`、交付目录同名额 | `unit_checked`（14 用例，含重复候选、篡改行、`--force` 拒写）；`--apply-plan` 另外拒绝「冻结行数与计划写明的 size 不符」和「留出集 id 与计划写的 id 不符」，`--check --plan` 会把 id 不符也报出来 |
| C11 | 实验计划干跑工具 `dry_run_experiments.py`：校验 35 条条目（id 唯一/状态词表/需求词表/占位符/留出集状态/轴 count 与条目数一致），并按真实 CLI 表面校验 `envoy` 子命令与参数——计划里出现 `--pages`/`--json` 这类 CLI 没有的开关会直接报错，占位符未加引号也报错；渲染单条命令且不执行任何网络动作 | `scripts/dry_run_experiments.py`、交付目录同名额 | `unit_checked`（17 用例，含重复 id、未知状态、未知需求、冻结留出集缺 rows、散文命令被拒、依赖条目 id 不存在、读公网 URL 的条目必须列进依赖集、轴 count 不符、CLI 无此参数、未知子命令、占位符未加引号） |
| C12 | 发布链路离线契约：`release.yml` 必须按标签取版本、四项门禁先跑、五个产物都构建、清单在产物之后 `SHA256SUMS` 之前生成、缺 `NPM_TOKEN` 只跳过不掩盖失败；两个安装脚本不得出现字面版本号，资产 URL 一律由运行时解析的版本拼 | `tests/cli/test_release_workflow.py`、`tests/cli/test_installer_version_resolution.py` | `unit_checked`（6 + 5 用例；字面版本号零命中） |
| C13 | 公网守卫判定一次到位：`_public_url_rejection` 返回 public / policy / dns 三值并按 host 缓存，读路径只解析一次域名、由同一个结论决定报错码与文案（此前用 bool 再补查一次，两次解析可能不一致）；`_host_lookup_failed` 删除 | `packages/envoy-cli/src/envoy_cli/reader.py`、`browser.py`、`tests/cli/test_url_guard_determinism.py` | `unit_checked`（5 用例，全程不打真 DNS；受影响 13 个测试文件 321 passed） |
| C14 | 实测结果写回工具 `record_experiment.py`：把一条结果连同 `evidence`/`observed_at` 写进计划，拒绝无证据的通过/失败、拒绝把已跑条目改回 `not_run`（除非 `--force`），留出集未冻结时拒绝记 `holdout.dependent_entries` 里的条目，计划级 `status` 随条目重算；不带 `--write` 只干跑 | `scripts/record_experiment.py`、交付目录同名额、`tests/cli/test_record_experiment.py` | `unit_checked`（13 用例，含未知 id、无证据、降级拦截、干跑不动字节、聚合状态、坏计划先拒）；写回后额外打印一行 `progress <完成>/<总数>（按轴）`，留出集未冻结时点名它挡住了哪些条目 |
| C15 | 下载摘要校验不再静默跳过 | `integrations/install.sh`（`verify_checksum`）、`scripts/check_installer_offline.sh` | `implemented` + `unit_checked`（离线用例 `10/10`；`pytest tests/test_install_script.py` 9 passed，另 2 例受本机 Xcode 许可限制、旧提交同样失败）；POSIX 侧取不到 `SHA256SUMS` 或无 `shasum`/`sha256sum` 时各打一行说明，取到但缺该文件条目直接 `die`；`install.ps1` 侧同样的两种情形也各走一条（不可达→警告继续、缺条目→失败），两平台语义一致 |
| C16 | 安装源可覆盖（含 Claude Code 市场源），并用环回镜像做端到端验证 | `integrations/install.sh`（`ENVOY_RELEASES_BASE` / `ENVOY_MANIFEST_URL`，两平台；`install.ps1` 同款 `$Script:ReleasesBase`）、`scripts/check_installer_e2e_local.sh`、`tests/cli/test_installer_version_resolution.py`、`tests/cli/test_installer_ps1_static.py`、`tests/cli/test_install_docs.py` | `implemented` + `unit_checked`（离线用例 `10/10`；环回 e2e `3/3`；ps1 静态 4 passed；安装相关 `pytest` 25 passed（另 2 例 Xcode 许可阻塞），另 2 例同前）；四处下载统一走 `$RELEASES_BASE`，宿主机库 `ENVOY_REPO` 一处覆盖全部默认 URL，默认仍是公开路径，镜像/本地目录按 `v<版本>/` 布局即可服务；e2e 在 127.0.0.1 上完整跑「解析版本 → 下载 → 校验 → 解包」，篡改摘要后确认安装失败 |
| C17 | bootstrap 与宿主派发分离：`ensure_runtime()` 只负责「一个固定绝对路径的 runtime」（uv/Python 引导、venv 重建、固定版本、pin 校验、浏览器安装期准备），不认任何宿主；`install_auto()` 之后才按 `find_dsh` / `claude` / `codex` 分别派发，三者的装载路径互不依赖，没有宿主时只写 MCP 条目并打印 `path` 提示。读路径不再触达安装动作（`envoy asr install` 是显式命令） | `integrations/install.sh`（`ensure_runtime` / `install_auto`）、`packages/envoy-cli/src/envoy_cli/cli.py` | `implemented` + `unit_checked`（`tests/test_install_script.py` 9 passed，另 2 例被本机 Xcode 许可挡在 exit 69；`sh scripts/check_installer_offline.sh` 10/10 覆盖 bootstrap 不看宿主） |

## 2. 通过的检查（本轮真跑）

- 定向回归 19 个文件：**298 passed in 34.57s**（exit 0）。
- 媒体三件套（`test_video_media` + `test_media_asr_checkpoint` + `test_media_asr_resume`）：**37 passed in 3.99s**。
- `tests/cli/test_process_control.py`：**14 passed**（含注入 `nt` 平台的 Windows 分支）。
- `tests/cli/test_browser_auto_setup.py`：**8 passed**。
- `sh scripts/check_installer_offline.sh`：**10/10**（含「旧 PATH 不覆盖 pin」「无宿主回落 MCP」「收尾报 browser 与 install state」「下载根可覆盖」）。
- `sh scripts/check_installer_e2e_local.sh`：**14/14 通过（含 dry-run 只报计划不落盘、mcp 分支、`claude-code`/`codex` 两条一键安装分支：marketplace 源跟随 `ENVOY_REPO` 且干跑不写文件，以及 DSH 真主机分支：包地址走同一根且干跑不调宿主 CLI）**（环回镜像：校验通过解包、摘要不符中止；不进公网、不装 wheel）。
- `tests/cli/test_install_docs.py`：**4 passed**（README 中/英的一行命令 URL 必须来自 `install.sh` 的 `ENVOY_REPO` 默认；命令里的每个 target 都得在 `case` 里存在；用户可见 target 都得在文档出现——`all` 因此补进 README）。
- `tests/cli/test_installer_ps1_static.py`：**4 passed**（`$Script:` 变量先赋值后使用、括号配平、镜像钩子各只有一处；本机无 PowerShell，故只做静态面）。
- 安装/守卫/进程四文件定点回归（`test_install_state`、`test_browser_auto_setup`、`test_process_control`、`test_url_guard_determinism`）：**36 passed in 0.64s**。
- `tests/cli/test_install_state.py`：**9 passed**；含 doctor 断言的四个测试文件 `-k doctor`：**12 passed**。
- `tests/cli/test_url_guard_determinism.py`：**5 passed**（解析不可达域名只查 1 次、私网地址只查 1 次、共享缓存命中不再查、DNS 失败不被误记成 policy、字面量与坏 scheme 零解析）；受影响 13 个测试文件 **321 passed**。
- `tests/cli/test_dry_run_experiments.py`：**17 passed**；`test_record_experiment.py` **13 passed**；`test_freeze_holdout.py` **14 passed**（三文件 44 passed；含 `test_verify_delivery.py` 的全套为 56 passed）（新增「命令必须以可执行程序开头」，把 35 条计划里的散文步骤挪进 `notes`）。
- `tests/cli/test_record_experiment.py`：**13 passed**；对真实计划跑 `record_experiment.py --json` → `plan_status: not_run, entries: 35, not_run: 35, holdout: pending_sample`（不改文件）。
- `tests/cli/test_verify_delivery.py`：**12 passed**；对真实交付目录跑 `python scripts/verify_delivery.py --dir …` → **delivery ok (13 checks)**（新增第 13 项：交付目录里 `EXPERIMENT-PLAN.json` 的每条命令都要落在真实 CLI 表面上，`--pages`/`--json` 一类不存在的开关在打包校验阶段就报错）。
- `check_versions.py --check`：11 项声明一致（2 份文档也查）。
- `check_release_manifest.py --check`：`Envoy 0.1.24, runtime >= 0.1.24`。
- `build_envoy_dsh_plugin.py --check`、`sync_host_skills.py --check`：均为 current。
- 打包产物可用性：`uv run --offline --with <wheel> envoy --version` → `envoy-cli 0.1.24 (core 0.1.24)`；用交付 wheel 跑 `doctor --json` 得 `channel: release`、`source_dirty: false`、`runtime.install.status = unpinned`（空 `ENVOY_HOME`）。

## 3. 打包结果（release 渠道，C7/C8 之后重建）


预览候选：`/Users/apple/Desktop/2026/envoy-delivery/0.1.24-preview/`（`local-preview` 渠道，wheel 带 `+preview.g6b5928537057`，`doctor` 报 `channel local-preview`、`source_revision f03184184294`，5 产物 + 清单 + `SHA256SUMS` + `verify_delivery.py` + `PREVIEW-NOTES.md`），只给在场首测，不上传。
| 文件 | 作用 |
| --- | --- |
| `envoy_cli-0.1.24-py3-none-any.whl` | runtime（CLI + 内嵌 core） |
| `envoy-skill.zip` | Claude Code / Codex / DSH 共用的 Skill 包 |
| `envoy-dsh-0.1.24.tgz` | DSH bundle（npm 包） |
| `install.sh` | macOS / Linux 一键安装 |
| `install.ps1` | Windows 一键安装 |
| `release-manifest.json` | 发行清单（版本、min_runtime、协议、artifacts 摘要） |
| `SHA256SUMS` | 全部发行资产的 sha256（权威摘要来源） |

摘要一律以 `SHA256SUMS` 为准（`shasum -a 256 -c SHA256SUMS`）；
构建用的源码提交记在 `release-manifest.json` 的 `source_commit`。本文不再复制哈希，避免文档与产物脱节。

`SHA256SUMS` 覆盖上表 6 个文件（5 产物 + 清单；校验和不含自身）；`release-manifest.json` 的 `artifacts` 覆盖 5 个
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
`EXPERIMENT-PLAN.json`（35 条实验，全部 `not_run`）+ `RUN-WHEN-PRESENT.md`
（人 + 网络在场时的执行顺序）。

- `tests/cli/test_release_workflow.py`、`tests/cli/test_installer_version_resolution.py`：**6 passed / 7 passed**（新增 Claude Code 市场源跟随 `ENVOY_REPO`）（可离线重跑，ruff clean）。

## 计划命令与真实 CLI 对齐（2026-10-28）

交付目录里的 `EXPERIMENT-PLAN.json` 之前有 21 条 `envoy read <url> --json` /
`--budget-json` / `--pages` 形态的命令：`read` 只接受 `--request-json <路径|->`，
其余开关不存在，拷出去就是 argparse 报错。现已全部改成生产形态：

- 19 条 `read` 条目 → `sh -c 'printf %s "{…}" | envoy read --request-json -'`，
  预算写在请求体 `budget.max_work_seconds` / `budget.max_download_bytes`；
- D07（分批读同一份 PDF）与 D11（读完最后页 `delivery_gap=false`）改成
  「预算分段再读 + read→expand 到 `next_offset=null`」，因为读接口没有页范围参数，
  原写法表达的观察点无法从 CLI 触达，`expected`/`notes` 写明这一点；
- `axes.install.count` 由 10 改成 13（与条目数一致），并新增校验
  `count == len(entries)`，防止计数与条目再次漂移；
- V10 的 `<id>`、I13 的 `<owner>/Envoy` 补引号，避免被 shell 当重定向。

验证：桩 `envoy`（离线、只 `json.loads` stdin）跑通 22 条 `envoy`/`sh -c` 命令；
两个交付目录 `dry_run_experiments.py --check` 均 `plan ok: 35 entries (not_run 35);
holdout pending_sample`，`shasum -a 256 -c SHA256SUMS` 均通过，两份计划逐字节相同。
