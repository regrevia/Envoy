# DeepSeek Harness 插件

本目录提供 Envoy 的 DeepSeek Harness（DSH）适配，位于
[`envoy-dsh/`](envoy-dsh/)。安装后由 DSH 插件管理器统一管理，既保留 Envoy
原有能力，也把 Envoy 注册为 Agent Skill。

当前插件版本：`0.1.30`（`dev` 分支上的开发版）；要求 CLI runtime `>= 0.1.30`，bundle 首次使用时自动装好。
版本、最低 runtime、协议版本、源码提交和产物校验值统一记录在
[`integrations/release-manifest.json`](../release-manifest.json)。

## 它做什么

`envoy-dsh` 是一个 DSH bundle，一次安装插入两个插件行：

- `envoy-mcp`：通过官方 `@deepseek-ai/dsh-mcp-client` 启动
  `envoy mcp`（默认 `core`），把 `envoy_read`、`envoy_expand`、
  `envoy_search`、`envoy_observe` 四个工具挂载为 `mcp__envoy__*`。
  `full` 工具集额外提供本地视频、主页发现、合集、回放、视觉历史和 doctor
  工具，需要时在启动 Harness 的环境里导出 `ENVOY_MCP_TOOLSET=full` 并重启。
- `envoy-skill`：通过公开的 `ctx.skills` 服务注册 Envoy Skill，内容由
  `integrations/skills/envoy/SKILL.md` 生成，工具名已适配 DSH 前缀。

不修改 Envoy 的读取协议、预算、来源定位、视觉交接或可选能力检测。

## 装与不装

同一模型、同一链接，差别在“一次读取拿回来什么”：

| 你要的 | 只用内置抓取 | 装 envoy-dsh |
| --- | --- | --- |
| 长文章 / 公众号 / 知乎 | 截断正文或只剩摘要 | 正文分段 + 来源定位 |
| PDF | 前几页，或只有截图 | 逐页文本 + 页码定位 |
| 抖音 / B站视频 | 标题、封面、简介、弹幕 | 绑定播放器文件；字幕或 ASR 带覆盖率；宿主声明能看画面时给帧 |
| 音频 | 拿不到 | 带时间戳转写；置信度低、无人声会明说 |
| 登录墙 | 403、空正文或验证码页 | `challenge_required` + 你要做的一步（按域名导入 cookie，或登录一次） |
| 读不到 | 编一个像样的答案 | `partial`，给出读到哪、缺什么 |

我们跑过的轮次级 A/B 只是探索性对比：两臂时限不同（900 s 与 2400 s），
不作为结论；文章级引用命中在每臂 60 轮下没有可区分的差异。这个 bundle
保证的是上表这种“单次读取的行为”，由测试兜住。

不装也能读链接。插件加的是一组边界：声明式视觉输入、可检索的已读材料
（不用重抓）、读不到时明说缺什么、需要人操作时给人话而不是可执行命令。

## 前置条件

1. 能跑起 DSH 插件管理器即可，runtime `>= 0.1.30` 由 bundle 首次使用时自动安装（不再需要 `envoy browser setup`）。参见
   [Envoy 安装指南](../skills/envoy/INSTALL.md)。没有 runtime 时，本插件会在
   `~/.local/share/envoy-dsh/runtime` 自备一份。
2. 使用 DSH Desktop 的插件管理器安装本目录。
3. macOS 或 Linux。补丁使用 `/bin/sh`，Windows 需改用 PowerShell 或
   `cmd.exe` 启动器。

## 可见窗口不由 agent 打开

Envoy 默认无头读取。页面需要用户登录或验证时，工具返回
`details.needs_user_action` 且带 `requires_human: true`：把问题与选项转述给
用户，停下等待，不要设置 `ENVOY_VISIBLE_BROWSER`，也不要执行任何开窗命令。
可见模式只对人类的交互式终端生效（stdin 与 stdout 都是 TTY 且显式 opt-in），
本插件的 stdio 传输下 CLI 一律拒绝；读到需要登录却看不到窗口属于预期行为。

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
  `mcp__envoy__envoy_observe`；高级工具需重启为 `full` 后才出现；
- 技能目录出现 `envoy`，加载后以
  `# Envoy Link Reader in DeepSeek Harness` 开头；
- `envoy doctor --json` 的 `cli_version` 不低于 `0.1.30`；使用 `full` 时也可调用
  `mcp__envoy__envoy_doctor`。

## 升级

启动器每次挂载前对比 `envoy --version` 与插件要求的最低 runtime：

- 自备 runtime 版本过低：用发行版 wheel 原地升级 venv，再挂载。venv 独立于
  `~/.envoy`，既有 ASR/OCR 配置和已下载模型不受影响，不会重复下载。
- `ENVOY_BIN` 或 PATH 上的 `envoy` 版本过低：直接失败并打印升级命令，不静默
  使用不兼容的 CLI。
- 更新插件或修改 `ENVOY_DSH_VERSION` 后，下一次挂载即按新版本重新校验。

校验清单与源码是否一致：

```bash
python3 scripts/check_release_manifest.py --check
```

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
