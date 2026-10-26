# Envoy 安装 / Install

Envoy 由三部分组成：Skill 指令、本地 CLI runtime、宿主 MCP 连接。当前开发版
`0.1.14`，发行说明以 `CHANGELOG.md` 为准。

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

脚本下载 runtime 时用 release 里的 `SHA256SUMS` 校验，装完自己跑一遍
`envoy doctor --json` 并打印版本；哪一步失败会停在那一步并给出原因。装完重启
宿主。想先看脚本会做什么：加 `ENVOY_DRY_RUN=1`，只打印命令不执行。

## 手动安装 / Manual

### DeepSeek Harness

应用里 Plugin Manager → 搜 `Envoy` → Install。命令行等价：

```bash
dsh plugin --profile web add envoy-dsh
# 或装发行包，不经过 npm
dsh plugin --profile web add https://github.com/regrevia/Envoy/releases/download/v0.1.14/envoy-dsh-0.1.14.tgz
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
gh release download v0.1.14 --repo regrevia/Envoy --dir envoy-v0.1.14
cd envoy-v0.1.14
uv tool install --python 3.11 ./envoy_cli-0.1.14-py3-none-any.whl
```

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

## 更新与卸载 / Update and remove

```bash
# 更新 runtime：重跑安装脚本，或在 DSH 里重装 bundle
curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh

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
  会退化成读页面正文并标 `partial`，不是整条失败。
- **OCR**：截图里的文字在缺 OCR 时不可用，装好后再读同一 `material_id`。

## Scope and license

见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。Envoy 只读取公开可访问的内容；
需要登录的站点走 `envoy_auth` 让用户自己扫码，不代持凭据。
