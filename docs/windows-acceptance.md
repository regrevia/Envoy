# Windows 验收 / Windows acceptance

PowerShell 安装器（`integrations/install.ps1`）在真实 Windows 上的验收。补的是 mac 上开发看不到的空白：Windows PowerShell 5.1 与 PowerShell 7 的解析差异、真实用户 PATH（`HKCU\Environment`）、旧版本残留、中文/含空格路径。

工作流：`.github/workflows/windows-acceptance.yml`，脚本：`scripts/windows/acceptance.ps1`，节点注册：不在本仓库，见 [`regrevia/win-runner`](https://github.com/regrevia/win-runner)。

## 两条腿

| 腿 | 机器 | 证明什么 | 触发 |
| --- | --- | --- | --- |
| `windows-hosted` | GitHub 托管 `windows-2022` | 干净机：无历史残留、每次全新 PATH | dispatch、发版后 |
| `windows-powershell-5-1` | 自宿主节点 | 真实用户环境 + PS 5.1 | dispatch、发版后 |
| `windows-powershell-7` | 自宿主节点 | 真实用户环境 + PS 7.4+ | dispatch、发版后 |

托管那条保「每次发版自动跑」；自宿主那条保「历史残留、企业策略、真实账号」。缺托管那条，节点一挂发版就失守；缺自宿主那条，升级路径永远是盲区。

## 安全检查（公开仓库，硬约束）

`regrevia/Envoy` 是公开仓库，self-hosted runner 等于「别人推的代码在我机器上执行」的通道，所以：

- 只允许 `workflow_dispatch`（需写权限）和 `workflow_run`（tag 发版后）。**永远不挂 `pull_request` / `pull_request_target`**，fork PR 会在本机执行任意代码。
- `permissions: contents: read`。回写证据用 `actions/upload-artifact`，不需要 `contents: write`；要回写仓库时才在那一步单独提权。
- 专用账号 + 专用虚拟机/沙箱，不用日常桌面。节点只跑这一个仓库。
- 每轮跑完清理：脚本把 `ENVOY_HOME` 指向 `%RUNNER_TEMP%` 下的临时目录，并把用户 PATH 的原值与 Kind（必须是 `REG_EXPAND_SZ`）快照、在 `finally` 与工作流 `always()` 两步还原；`_work` 不保留构建产物。
- 不用 `--ephemeral`：服务模式下一次注册长期在线，`--ephemeral` 会在跑完一个 job 后注销，而矩阵和两条腿都需要复用。用「清理 + PATH 还原」代替。

## 日志与证据的隐私

job 日志和 artifact 在公开仓库里对所有访客可见。所以：

- 工作流第一步把 `USERNAME`、`COMPUTERNAME`、`USERPROFILE`、`LOCALAPPDATA`、`RUNNER_TEMP` 送进 `::add-mask::`。
- `acceptance.ps1` 默认 `-Redact $true`：日志和证据 JSON 里账号、机器名、用户目录替换成 `<user>`、`<host>`、`<user-home>`；只有托管那条（`-Redact $false`）保留真实路径，因为那台机本身就是一次性的。
- 证据只写版本号、安装器 SHA256、PATH Kind、PS 版本、检查项 OK/FAIL，不写凭据、不写 token、不写主机网络信息。
- 注册 token 只走命令行/环境变量，一次性、一小时过期，不入库；节点的 `~/.dsh/windows-node.json` 一类连接信息永不进仓库。

## 节点前置条件

1. Windows 10/11 或 Server 2019+，x64；专用账号（交互登录或用该账号装服务）。
2. `Python 3.11+` 或 `uv` 在 PATH 上：`install.ps1` 需要其中之一建 venv。
3. PowerShell 7.4+（`winget install --id Microsoft.PowerShell -e --silent`）。缺失时 `windows-powershell-7` 会先尝试装，装不上直接报错，不会静默当通过。
4. 能访问 `github.com` / `raw.githubusercontent.com` / PyPI（或用 `ENVOY_RELEASES_BASE`、`ENVOY_MANIFEST_URL` 指向镜像做常规回归）。
5. 默认那轮关掉 Chromium 下载（`ENVOY_SETUP_BROWSER=0`），但保留转写预取——即默认
   `full` 档真正会做的事，顺带证明「装完不下载」在 Windows 上也成立。想单独验证某一
   档，用 `profile` 输入（见下），该档自己决定装什么；`minimal` 才是两样都不装。

## 注册节点

节点是 regrevia 的基础设施，脚本和说明在 [`regrevia/win-runner`](https://github.com/regrevia/win-runner)，本仓库不再放注册脚本。

```powershell
# 在有仓库管理员权限的机器上取一次性 token（不要贴进任何文件）
gh api -X POST repos/regrevia/Envoy/actions/runners/registration-token --jq .token

# 在 Windows 节点上，管理员 PowerShell
git clone https://github.com/regrevia/win-runner C:\win-runner
C:\win-runner\scripts\runner-setup.ps1 -Token <token> -Repo regrevia/Envoy -Labels windows,x64,regrevia-win
```

个人账号没有组织级 runner，注册是按仓库的：同一台机器要给多个仓库用，就按仓库各跑一次，脚本会把 root 派生到 `C:\actions-runner\<repo>`、work 目录各自独立，实例之间不冲突。

跑完节点以服务形式长期在线，仓库 Settings → Actions → Runners 里能看到 `Idle`。标签是**机器能力**（`regrevia-win` 表示「这台 regrevia 的 Windows 机器」），实例归属由注册时选定的仓库决定。标签必须与工作流一致：`runs-on: [self-hosted, windows, x64, regrevia-win]`，不要只写 `self-hosted`。

## 手动跑一轮

Actions → Windows 验收 → Run workflow：

- `version` 留空 = 取 `integrations/release-manifest.json` 的当前版本。
- `previous_version` 默认 `0.1.23`：升级场景先装它，再装目标版本。
- `scenario`：`fresh`（无历史）、`upgrade`（在已有安装上覆盖）、`both`（默认）。
- `profile`：留空 = 默认那轮（见「节点前置条件」第 5 条）；填 `minimal` / `standard` / `full` = 按该档装，并用 `envoy doctor --json` 断言该档到底装了什么（A8）。`standard` / `full` 会真的下 Chromium，耗时更长。

mac 上想直接驱动节点，可走 SSH 跑同一个脚本：

```bash
ssh <user>@<host> 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\scripts\windows\acceptance.ps1 -Scenario both'
```

SSH 只验证命令行行为，不验证窗口外观。

## 检查项

| id | 断言 |
| --- | --- |
| A1 | 当前 shell 是 PS 5.1 或 7+，与 job 声明的版本一致；Windows；控制台 UTF-8 |
| A2 | fresh：shim 生成、`envoy.cmd --version` 等于目标版本、`runtime-path` 存在 |
| A3 | 用户 PATH 里 shim 目录恰好一次；`REG_EXPAND_SZ` 不被降级；`%VAR%` 不被展开 |
| A4 | upgrade：旧版本 → 目标版本，版本真的动了 |
| A5 | 无重复 PATH 项、无旧版本残留、pin 指向存在的 runtime、连跑两次稳定、shim 里没有写死版本号 |
| A6 | 不写 HKLM（普通用户不需要提权） |
| A7 | 用户 PATH 还原到跑前值 |
| A8 | 指定 `profile` 时：`minimal` 无浏览器、无转写引擎；`standard` 浏览器就绪、无转写引擎；`full` 浏览器就绪且 Whisper `base` 可用 |

证据落在 `%RUNNER_TEMP%\envoy-windows-acceptance-ps5.json` / `-ps7.json`，artifact 保留 7 天。

## 已知边界

- 自宿主节点不是干净机：需要干净状态的断言由 `windows-hosted` 那条负责，不要在节点上做「首次安装」的结论。
- 节点上的中文用户名、含空格路径是**天然优势**，别为了「统一」改成 ASCII 路径。
- 验收不覆盖 Node/.cmd 包装、内网代理、企业策略这类环境差异；它们要单独加探针。