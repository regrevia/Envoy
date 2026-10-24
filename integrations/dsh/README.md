# DeepSeek Harness 插件

本目录提供 Envoy 的 DeepSeek Harness（DSH）适配，位于
[`envoy-dsh/`](envoy-dsh/)。安装后由 DSH 插件管理器统一管理，既保留 Envoy
原有能力，也把 Envoy 注册为 Agent Skill。

当前插件版本：`0.1.8`（`dev` 分支上的开发版）。

## 它做什么

`envoy-dsh` 是一个 DSH bundle，一次安装插入两个插件行：

- `envoy-mcp`：通过官方 `@deepseek-ai/dsh-mcp-client` 启动
  `envoy mcp`（默认 `core`），把 `envoy_read`、`envoy_expand`、
  `envoy_search`、`envoy_observe_visual` 四个工具挂载为 `mcp__envoy__*`。
  `full` 工具集额外提供本地视频、主页发现、合集、回放、视觉历史和 doctor
  工具，需要时在启动 Harness 的环境里导出 `ENVOY_MCP_TOOLSET=full` 并重启。
- `envoy-skill`：通过公开的 `ctx.skills` 服务注册 Envoy Skill，内容由
  `integrations/skills/envoy/SKILL.md` 生成，工具名已适配 DSH 前缀。

不修改 Envoy 的读取协议、预算、来源定位、视觉交接或可选能力检测。

## 前置条件

1. 已安装 Envoy CLI runtime（`0.1.6` 或兼容的后续开发版本），并完成
   `envoy browser setup`。参见
   [Envoy 安装指南](../skills/envoy/INSTALL.md)。
2. 使用 DSH Desktop 的插件管理器安装本目录。
3. macOS 或 Linux。补丁使用 `/bin/sh`，Windows 需改用 PowerShell 或
   `cmd.exe` 启动器。

## 安装

在 DSH 插件管理器中选择“安装 bundle”，指向本仓库的绝对路径：

```text
/absolute/path/to/Envoy/integrations/dsh/envoy-dsh
```

在 Agent 会话中，等价操作是 `plugin_manager` 的 `install_bundle`，`target`
传同一绝对目录。不要手工修改 profile 的 `package.json` 或
`cordis.patch.yml`，也不要自行在 profile 中运行 pnpm。

安装后检查：

- 插件管理器显示 **Envoy 链接阅读器**，带 Envoy 小鸟图标；
- 工具列表出现 `mcp__envoy__envoy_read`、
  `mcp__envoy__envoy_expand`、`mcp__envoy__envoy_search` 和
  `mcp__envoy__envoy_observe_visual`；高级工具需重启为 `full` 后才出现；
- 技能目录出现 `envoy`，加载后以
  `# Envoy Link Reader in DeepSeek Harness` 开头；
- `envoy doctor --json` 返回 `0.1.6`；使用 `full` 时也可调用
  `mcp__envoy__envoy_doctor`。

## PATH 与超时

DSH GUI 可能不继承登录 shell 的 PATH，因此补丁显式加入
`~/.local/bin`、`/opt/homebrew/bin` 和 `/usr/local/bin`。若 `envoy`
安装在别处，把 `cordis.patch.yml` 中 `args` 的 `envoy` 改成绝对路径后重装。
MCP 工具调用超时为 300 秒，高于 Envoy 默认的 180 秒工作预算。

## 维护

```bash
python3 scripts/build_envoy_dsh_plugin.py
python3 scripts/build_envoy_dsh_plugin.py --check
pytest -q tests/test_dsh_plugin.py
```

生成器会同步 CLI 版本、给工具名加 `mcp__envoy__` 前缀、保留 CLI 命令不变，
并把通用的 MCP 启动说明替换为 DSH 已挂载桥接的说明。不要手改
`envoy-dsh/skill/` 下的生成文件。

## English

See [envoy-dsh/README.md](envoy-dsh/README.md) for the package-level English
documentation, layout, launcher configuration, and verification checklist.
