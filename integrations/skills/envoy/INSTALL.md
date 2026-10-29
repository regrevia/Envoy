# Envoy 安装 / Install

Envoy 由三部分组成：Skill 指令、本地 CLI runtime、宿主 MCP 连接。当前开发版
`0.1.29`，发行说明以 `CHANGELOG.md` 为准。

装哪个宿主都只需要一条命令：脚本先确认 `PATH` 上有够新的 `envoy`（已装且够新
就复用），再装该宿主的插件。**不需要先手动装 CLI**，也不需要先跑
`envoy browser setup`：runtime 在首次使用时自己补齐，浏览器在第一次要渲染
页面时自己装。

## 一条命令 / One command

```bash
# Claude Code
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code

# Codex（插件装 MCP，脚本同时写用户级 hook）
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex

# DeepSeek Harness
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh

# Claude Desktop / Cursor / Windsurf：只写一条 MCP 配置
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- mcp

# 只认 skill 目录的宿主：解到 ~/.envoy/skill/envoy
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- skill

# 机器上装了几个就都装
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- all
```

脚本只要求机器上有 `curl`：uv 和 Python 3.11 由它自己装（`ENVOY_SKIP_UV_INSTALL=1`
可关掉）。下载 runtime 时用 release 里的 `SHA256SUMS` 校验，装完自己跑一遍
`envoy doctor --json` 并打印版本；哪一步失败会停在那一步并给出原因。装完重启
宿主。想先看脚本会做什么：加 `ENVOY_DRY_RUN=1`，只打印命令不执行。

## Windows / PowerShell

```powershell
# 装它能认出来的所有宿主；也可以把 all 换成 claude-code / codex / dsh / mcp / skill
irm https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.ps1 | iex

# 显式给目标、或先看脚本会做什么
powershell -ExecutionPolicy Bypass -File install.ps1 claude-code
$env:ENVOY_DRY_RUN = "1"; powershell -ExecutionPolicy Bypass -File install.ps1 dsh
```

`install.ps1` 与 `install.sh` 同一套约定：装一个私有 runtime 并把它的绝对路径写进
`%USERPROFILE%\.envoy\runtime-path`，在 `%USERPROFILE%\.envoy\bin` 放一个 `envoy.cmd` 转发到
那份 runtime（不改系统级 PATH），再装宿主插件、准备一次浏览器。只装 MCP 条目的
宿主（Claude Desktop / Cursor / Windsurf）会写用户级配置，改动前自动备份。
Windows 路径本身还没在真机上跑过，结果以 `COMPATIBILITY.md` 的平台矩阵为准。

装完随时可跑 `envoy doctor` 自查：第一行 `runtime` 显示当前跑的是不是安装时
固定下来的那份 `envoy`；显示 `shadowed` 说明 PATH 里另有旧 `envoy`，按提示重跑
安装脚本即可。

## 手动安装 / Manual

### DeepSeek Harness

应用里 Plugin Manager → 搜 `Envoy` → Install。命令行等价：

```bash
dsh plugin --profile web add envoy-dsh
# 或装发行包，不经过 npm
dsh plugin --profile web add https://github.com/regrevia/Envoy/releases/download/v0.1.29/envoy-dsh-0.1.29.tgz
```

桌面版把 CLI 装在应用包内，用脚本时它会自己找到。

### Claude Code / Codex

```bash
# Claude Code
claude plugin marketplace add regrevia/Envoy
claude plugin install envoy@envoy

# Codex
codex plugin marketplace add https://github.com/regrevia/Envoy.git
codex plugin add envoy@envoy
envoy hook install --host codex
```

Codex 0.156 起不加载插件自带的生命周期 hook，所以 hook 要单独写一次用户级
`~/.codex/hooks.json`；该子命令自 `0.1.7` 起提供，`0.1.14` 已包含。首次运行
Codex 会要求确认 hook 信任。

### 没有插件的宿主：Skill + runtime

任何能读 skill 目录、能连 MCP 的宿主都可以只装这两样，一条命令分别装：

```bash
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- mcp
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- skill
```

`mcp` 会找 Claude Desktop、Cursor、Windsurf 的配置并只加 `mcpServers.envoy`
一条（先备份，不动其他 server）；`skill` 把 `envoy-skill.zip` 解到
`~/.envoy/skill/envoy`（`ENVOY_SKILL_DIR` 可改目标）。要手工来一遍：

```bash
gh release download v0.1.29 --repo regrevia/Envoy --dir envoy-v0.1.29
cd envoy-v0.1.29
uv tool install --python 3.11 ./envoy_cli-0.1.29-py3-none-any.whl
```

也可以在 [GitHub Release](https://github.com/regrevia/Envoy/releases/tag/v0.1.29)
页面上直接下载 wheel 与 `SHA256SUMS` 手工核对。下载慢或装依赖超时，给
`install.sh` 设 `ENVOY_PYPI_INDEX_URL` 指向可用镜像即可。

然后在宿主的 MCP 配置里加一条：

```json
{
  "mcpServers": {
    "envoy": { "command": "envoy", "args": ["mcp"] }
  }
}
```

`envoy` 不在 `PATH` 时把 `command` 换成绝对路径。默认只暴露 `core` 工具集；
需要本地视频、发现、合集、回放、视觉历史、doctor 这些时，启动宿主前设
`ENVOY_MCP_TOOLSET=full`。

## 验证首次读取 / Check the first read

```bash
printf '%s' '{"protocol_version":"envoy.read/1","request_id":"install-check","source":{"url":"https://example.org/"}}' | envoy read --request-json -
```

只验证公开网页读取，不代表视频或 ASR 已就绪。安装脚本结束时也会打印
`envoy doctor --json` 的 runtime 版本与协议版本，对不上就是没装成。

## 三档安装 / Three profiles

默认是完整档，装完没有「首次使用时还要下载」的能力：

| 档位 | 装什么 | 大致体积 | 适合 |
| --- | --- | --- | --- |
| `minimal` 简洁 | 只有 runtime：读普通网页、PDF、图片 | 178 MB | 磁盘吃紧的容器 / CI |
| `standard` 标准 | runtime + Chromium：再加必须渲染的页面、视频页 | 178 + 683 MB | 只用网页能力，不需要转写 |
| `full` 完整（默认，推荐） | 上一档 + Whisper `base`：字幕、语音、无字幕视频都能转写 | 上一档 + Whisper 依赖 + 权重约 140 MB | 默认选这个 |

```bash
# 不写 --profile 就是完整档
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh
# 只装 runtime
curl -fsSL .../install.sh | sh -s -- --profile minimal dsh
# Windows
powershell -ExecutionPolicy Bypass -File install.ps1 -Profile standard
```

`ENVOY_PROFILE=minimal` 与 `--profile` 等价；`ENVOY_SETUP_BROWSER=0`、
`ENVOY_SETUP_MEDIA=0` 覆盖所选档位里的某一步（CI 里既想要完整档的插件接线、又不想
下载时用）；`ENVOY_ASR_MODEL` 换 `full` 档预取的模型（默认 `base`）。

装完核对，以及升级后回收旧浏览器：

```bash
envoy doctor --json           # browser.ready、asr_model_available 应为 true
envoy browser prune --dry-run # 先看会回收多少，不删
```

Playwright 每升一次就换个浏览器版本、从不删旧的，所以升级几次后缓存会翻倍；
`envoy browser setup` 装完会自动清掉被替换掉的旧版本，`envoy browser prune` 可手动
重跑（`--dry-run` 只报不删）。

体积口径：runtime 与 Chromium 是在本机（macOS，`du`）实测；Whisper 依赖里 torch
占大头，各平台不同，本仓库没有实测数字，只保证 `full` 档装完即可离线转写。
后装的补法：`minimal`/`standard` 想升级能力，重跑安装脚本并带 `--profile full`
即可，两步都是幂等的。

English: the installer takes `--profile minimal|standard|full` (`ENVOY_PROFILE` is
the same knob) and defaults to `full`, so a fresh install already has Chromium and
the Whisper `base` model — nothing downloads on first use. Runtime 178 MB and
Chromium 683 MB are measured on macOS; the Whisper dependency set (torch is most
of it) varies by platform and is not measured here.

## 更新与卸载 / Update and remove

```bash
# 更新 runtime：重跑安装脚本，或在 DSH 里重装 bundle
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh

# 单独换 wheel
uv tool install --force --python 3.11 ./envoy_cli-0.1.29-py3-none-any.whl

# 卸载
claude plugin uninstall envoy
codex plugin remove envoy
dsh plugin --profile web remove envoy-dsh
uv tool uninstall envoy-cli
```

## 源码开发 / From a checkout

```bash
scripts/dev_install.sh        # .venv + editable envoy-core 与 envoy-cli
```

只用 `pip install -e packages/envoy-cli` 会失败：CLI 导入 `envoy-core`，而它不在
PyPI 上；且 CLI 要 Python >= 3.10，macOS 自带的 `python3` 是 3.9。脚本用 `uv`
取一个够新的解释器并两个包一起装。

## 可选能力 / Optional capabilities

- **浏览器渲染**：首次读到需要 JS 的页面时自动 `playwright install chromium`。
  想自己控制就在启动宿主前设 `ENVOY_DISABLE_AUTO_BROWSER_SETUP=1`，之后手动
  `envoy browser setup`。
- **ASR（本地视频转写）**：需要时按提示装 FFmpeg 与对应模型；没有它时视频页
  会退化成读页面正文并标 `partial`，不是整条失败。手工装可跑
  `envoy asr install --model base`。
- **OCR**：截图里的文字在缺 OCR 时不可用，装好后再读同一 `material_id`。

## Scope and license

见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。Envoy 只读取公开可访问的内容；
需要登录的站点走 `envoy_auth` 让用户自己扫码，不代持凭据。
