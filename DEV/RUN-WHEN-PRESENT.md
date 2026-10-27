# 人 + 网络在场时的执行顺序 / Run when present

这一份是给「有人在旁边、能联网、能开 GUI」的那一次跑用的。**不要在无人值守时
自动执行**：里面有真实网页、真实 ASR / OCR 模型下载、Windows 与 Linux 真机安装。
本轮（0.1.24）这些全部是 `not_run`，`EXPERIMENT-PLAN.json` 里 35 条也全是
`not_run`。

## 0. 准备

```sh
cd <repo>
git log --oneline -3        # 确认在 0.1.24 交付提交上（见 release-manifest.json 的 source_commit）
sh scripts/check_installer_offline.sh        # 期望 10/10
sh scripts/check_installer_e2e_local.sh      # 期望 14/14（环回镜像，不进公网）
python scripts/check_versions.py --check
python scripts/check_release_manifest.py --check
```

交付包在 `/Users/apple/Desktop/2026/envoy-delivery/0.1.24/`。装之前跑一次交付校验，
一条命令覆盖摘要、清单、wheel/tgz/skill 与安装脚本的版本引用（离线，不需要装 wheel）：

```sh
cd /Users/apple/Desktop/2026/envoy-delivery/0.1.24
PYTHONDONTWRITEBYTECODE=1 python3 verify_delivery.py --dir .
# 期望最后一行：delivery ok: ... (13 checks)
```

`verify_delivery.py` 只读文件、不联网、不执行安装；它失败就不要往下装。其中一条会读 `EXPERIMENT-PLAN.json`：条目写 `passed`/`failed` 却没有 `evidence` 与 `observed_at`（或 `not_run` 却带着证据）时直接失败，所以别手改状态绕过 `record_experiment.py`。
需要手工核对摘要时仍是 `shasum -a 256 -c SHA256SUMS`。上面那行带 `PYTHONDONTWRITEBYTECODE=1`
是为了别在包里留下 `__pycache__`：脚本会导入同目录的干跑/写回模块，默认会写字节码。

想先摸一遍再动发行包，`../0.1.24-preview/` 是同一份代码的 `local-preview` 渠道候选
（wheel 名带 `+preview.g<rev>.<UTC>`），用 `python3 verify_delivery.py --dir . --allow-preview`
校验，装法见那目录的 `PREVIEW-NOTES.md`；它不替代发行包验证，也不上传。

## 0.5 只有半小时时的最小路径

时间不够跑全表时，按这个顺序做，信息量最大（每一步都能单独停）：

1. `sh scripts/check_installer_e2e_local.sh` → 期望 14/14（不联网，先确认安装派发本身没坏）；
2. §1a + §1b：macOS 干净装一次 `dsh`，看最后一行 runtime / protocol / browser 是否齐；
3. §8 先冻结留出集（`H12-2026-10-28`，12 行候选）——V01/V03/V04/V06/V07/V08 都等它，
   不先冻结就只能记 I 轴与 D 轴的部分条目；
4. §3 跑 V01 一条真实长视频，§4 跑 D01 一条真实长 PDF，各记一次 `record_experiment.py`；
5. 每次写回后看新增的 `progress <完成>/<总数>` 行：`long-pdf 0/12` 这类数字就是本次还差多少。

剩下的（Windows/Linux 真机、浏览器 OAuth、OCR 语种、失败重试路径）留到时间宽裕的一次。

## 1. 安装（先 macOS，后 Windows / Linux）

按顺序做，每步留一行记录：退出码、`cat ~/.envoy/runtime-path`、识别到的宿主、
`envoy doctor --json` 的 `browser.ready`、耗时。

```sh
# 1a 干净装（先看它打算做什么）
ENVOY_DRY_RUN=1 sh integrations/install.sh
# 1b 真装
sh integrations/install.sh
# 1c 覆盖升级（在已有 0.1.23 的机器上）
sh integrations/install.sh
# 1d pin 是否真的赢
~/.envoy/runtime-path >/dev/null; envoy --version   # 期望 0.1.24
```

Windows x64（需要一台真机）：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File install.ps1
where envoy            # 期望命中 ~\.envoy\bin\envoy.cmd
```

对应 `EXPERIMENT-PLAN.json` 的 `I01`–`I10`。一键安装那三条（`I11` Claude Code、`I12` Codex、`I13` 镜像源）在同一段里做，先干跑看计划：

```sh
ENVOY_DRY_RUN=1 sh integrations/install.sh claude-code
ENVOY_DRY_RUN=1 sh integrations/install.sh codex
ENVOY_DRY_RUN=1 sh integrations/install.sh dsh
ENVOY_DRY_RUN=1 ENVOY_REPO=acme/Envoy sh integrations/install.sh claude-code
```

干跑应只打印要执行的宿主命令、不写文件；真装以那个计划为准。

## 2. 浏览器与读路径（真实下载，允许慢）

```sh
envoy browser setup          # 只在这一次付下载时间，不要在读路径里付
envoy doctor --json          # 期望 browser.ready=true
```

跑失败分支的时候要制造「没有浏览器」的环境（换一个空 `ENVOY_HOME`），确认它
快速失败并给 `envoy browser setup`，而不是当场下载。对应 `COMPATIBILITY.md`
里「缺浏览器时读路径快速失败」那一行从 `unit_checked` 升到实测。

## 3. 长视频（V01–V10）

每条记录：`status`、`processed/total`、`gap`、墙钟秒数、下载字节、模型下载字节、
`next_action`。长视频务必显式抬预算，不要超时后重读：

```sh
sh -c 'printf %s "{\"request_id\":\"V02\",\"source\":{\"url\":\"<public-video-url>\"},\"budget\":{\"max_work_seconds\":900}}" | envoy read --request-json -'
```

`read` 只吃 `--request-json <路径|->`，预算写在请求体 `budget.max_work_seconds` 里；
计划里每条命令都是这个形态，可直接复制（`python3 dry_run_experiments.py --render V02`）。

中断类（V03、V04、V06、V08）要真按 Ctrl-C 或真改源，只记录观察到的事实。

## 4. 长 PDF（D01–D12）

```sh
sh -c 'printf %s "{\"request_id\":\"D02\",\"source\":{\"url\":\"/path/to/long.pdf\"},\"budget\":{\"max_work_seconds\":900}}" | envoy read --request-json -'
# 再读一次同一份文件：只解析未提交页，已提交页命中缓存
sh -c 'printf %s "{\"request_id\":\"D06\",\"source\":{\"url\":\"/path/to/long.pdf\"},\"budget\":{\"max_work_seconds\":900}}" | envoy read --request-json -'
# 取后续页：用 read 拿 material_id，再反复 expand 到 next_offset=null
sh -c 'printf %s "{\"request_id\":\"D11\",\"material_id\":\"$ENVOY_MATERIAL_ID\",\"max_chars\":100000}" | envoy expand --request-json -'
```

读接口没有显式页范围参数（`--pages` 不存在），页级进度只能靠预算分段与页游标验证；
`--json` 也不存在，输出默认就是 JSON。

重点看：`processed` 单调、重启后只解析未提交页、`parse_error` 页保留缺口、
最后一页之后 `delivery_gap=false`。

## 5. 发行作业（可选，需要权限）

**这一节只有拿到明确同意才做。** 关键一点：`packages/envoy-*/pyproject.toml` 现在就是
`0.1.24`，而 `origin` 上没有 `v0.1.24` tag——所以 **`git push origin main` 本身就是发布动作**：
`.github/workflows/auto-release.yml` 在同一个 SHA 的 CI 通过后会自己打 tag、跑
`.github/workflows/release.yml`、建 Release 并（有 `NPM_TOKEN` 时）发 npm 包。想先只推代码不发布，
必须先把 pyproject 版本改回已发布的 `0.1.23`，或者关掉 auto-release。

同意后按这个顺序：

```sh
git status --porcelain                # 必须为空；脏的工作树会在 CI 前就被自己拦住
git log --oneline origin/main..main | head -1   # 确认最上面是要发的提交
git push origin main
gh run list --workflow=auto-release.yml --limit 3   # 看它是否因版本变化而入列
```

然后盯三件事：

1. 同一 SHA 的 `ci.yml` 必须先绿（工作流会拒绝给 CI 已失败的 SHA 打 tag）；
2. Release 资产应是从该 tag 重建的五个产物 + `release-manifest.json` + `SHA256SUMS`，
   与本地 `../envoy-delivery/0.1.24/` **不是同一批字节**（本地包是本地构建，tag 触发的是 CI 构建）——
   两者都校验过再决定以哪个为准，别混用；
3. 预览候选 `../0.1.24-preview/` 不上传、不挂 Release，它只是本地摸手感用的。

## 6. 记录与升级

- 所有读数用 `record_experiment.py` 写回 `EXPERIMENT-PLAN.json`（见第 9 节），不要手改 `status`：手改不会带 `evidence` 和时间，也挡不住把跑过的条目改回 `not_run`。`holdout.dependent_entries` 里的条目（V01/V03/V04/V06/V07/V08，量的是留出集）在留出集冻结前记不进 `passed`。
- 改计划后先 `python3 dry_run_experiments.py --check`：`holdout.dependent_entries` 必须正好等于命令里读
  `<public-…>` 占位符的条目集，漏一个或多一个都会被点名拒绝，免得依赖集和条目正文各说各话。
- 只有真的跑过，才能把 `COMPATIBILITY.md` 对应行改成实测；五个指标的对外文案
  必须等读数回来再改。
- 出问题就把原始输出（退出码 + stderr 前 40 行）贴进当轮记录，不要只写结论。

## 7. 先干跑一遍实验计划（不联网、不执行）

```sh
python3 dry_run_experiments.py --check     # 期望 plan ok: 35 entries (not_run 35); holdout pending_sample
python3 dry_run_experiments.py --list
python3 dry_run_experiments.py --render V02  # 打印该条的确切命令与待填占位符
```

它只读 `EXPERIMENT-PLAN.json`，不跑任何条目；命令里的 `<url>` 之类占位符会单独列出来，避免把模板当命令粘贴执行。`--check` 还会拒掉不以可执行程序开头的「散文命令」，手改计划时别把步骤说明塞进 `command`（放 `notes`）。

## 8. 冻结留出集（跑指标前唯一允许的「取样本」动作）

`EXPERIMENT-PLAN.json` 的 H12 现在还是 `pending_sample`。先在公开站点上收集多于 12 条候选
（一行一个 URL，`#` 开头为注释）写成 `candidates.txt`，然后一次冻结：

```sh
cd /Users/apple/Desktop/2026/envoy-delivery/0.1.24
python3 freeze_holdout.py --input candidates.txt --size 12 --id H12-2026-10-28 \
  --out holdout.json --apply-plan EXPERIMENT-PLAN.json
python3 freeze_holdout.py --check holdout.json --plan EXPERIMENT-PLAN.json  # 期望 holdout ok: ... (12 rows, ...)
python3 dry_run_experiments.py --check              # 期望 ...; holdout frozen
```

`--apply-plan` 把 `sha256` 与 `rows` 直接写进计划的 `holdout` 并把状态改成 `frozen`，不要手抄；
计划里已有冻结留出集时它会拒写（`--force` 才覆盖）；行数与 `id` 跟计划写的不一致时同样拒写，
所以 `--size`/`--id` 改动只能是有意的（要改就得同时改计划并接受被追问），不会被一次手滑换掉 12 行样本。
跑过之后不得重抽，也不得删行。若发现要换样本，唯一正当理由是候选本身失效
（404/需要登录），换行要在报告里逐行说明原因，而不是 `--force` 重抽。

## 9. 把一条结果写回计划（唯一允许的写法）

```sh
cd /Users/apple/Desktop/2026/envoy-delivery/0.1.24
python3 record_experiment.py --json                       # 先看现在是 35 条 not_run、holdout 状态
python3 record_experiment.py --id V02 --status passed \
    --evidence "900s budget: 412/600 chunks, gap 188, 214s wall" \
    --observed '{"processed":412,"total":600,"wall_seconds":214}'        # 不带 --write = 干跑
python3 record_experiment.py --id V02 --status passed \
    --evidence "900s budget: 412/600 chunks, gap 188, 214s wall" \
    --observed '{"processed":412,"total":600,"wall_seconds":214}' --write
```

规则（工具会自己挡）：`passed/failed/blocked/skipped` 必须给 `evidence`（少于 8 个字符直接拒）；
写过的条目不能被改回 `running/not_run`，除非 `--force` 且你在当轮记录里说明了理由；
`observed_at` 由工具写，不要手填。计划级 `status` 会跟着条目自动重算
（全 `not_run` → `not_run`，有 `running` → `running`，有失败 → `failed`，全通过 → `passed`）。
