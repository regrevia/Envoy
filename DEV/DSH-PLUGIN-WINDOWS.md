# DeepSeek Harness 插件的 Windows 适配（记入 CHANGELOG 与交接）

## 结论

`envoy-dsh` 之前只能跑 macOS/Linux：`cordis.patch.yml` 用 `command: /bin/sh`
跑一段 POSIX 脚本，而 Windows 没有这个 shell，MCP 桥在 Windows 上从未挂载成功。
现在启动器是纯 Node（`launch.cjs`），一套代码覆盖三个平台，并已在本机 Windows
真机端到端验证。

## 改动

1. **新增 `integrations/dsh/envoy-dsh/launch.cjs`**：把原 sh 脚本的逻辑用 JS 重写一遍。
   - 运行时解析顺序不变：`ENVOY_BIN` → PATH 上的 `envoy` → 私有运行时。
   - 版本门槛不变，外部运行时过旧仍然报错而不是静默替换。
   - 私有运行时目录按平台：Windows 用 `%LOCALAPPDATA%\envoy-dsh\runtime`，
     其余用 `~/.local/share/envoy-dsh/runtime`；Windows 的可执行文件是
     `Scripts\envoy.exe` / `Scripts\python.exe`。
   - `uv` / `python` 通过 PATH + `PATHEXT` 解析（Windows 上是 `.cmd`/`.exe` shim）。
   - Windows 没有 `python3`，回退到 `python`。

2. **`cordis.patch.yml`**：`command: /bin/sh` → `command: node`，
   `-e` 参数是一段压缩（gzip+base64）的 `launch.cjs`。
   - 为什么内联而不是 `node launch.cjs`：MCP 客户端用宿主进程的 cwd spawn，
     包内相对路径不可靠。
   - 为什么压缩：Windows 上 `node` 常常是 `.cmd` shim，经 cmd.exe 时有 **8191
     字符命令行上限**，直接内联 8 KB 源码会报 `The command line is too long.`。
     stub 现在约 5.7 KB。

3. **`package.json`**：`files` 增加 `launch.cjs`。

4. **`README.md`**：Requirements 移除「Windows 不支持」，写明三平台与 Windows 路径。

5. **`tests/test_dsh_plugin.py`**：
   - 断言 `command: node` + `-e`，且可执行部分不再有 `/bin/sh`、`exec envoy mcp`、`$HOME`。
   - 断言 stub 解压后与 `launch.cjs` 逐字一致（单一事实源）。
   - 断言 stub 长度 < 8000，防止再次撞上 cmd.exe 上限。
   - 版本门槛测试：用 `ENVOY_BIN=sys.executable` + 极高的 required 版本，
     强制走「外部运行时过旧」分支，跨平台可跑。
   - 真实交接测试：用已安装的运行时完成 MCP `initialize` 握手，无运行时则 skip。
   - 测试侧的 `node` 解析改为可被 `subprocess` 直接 spawn 的真实可执行文件
     （Windows 上 `shutil.which("node")` 可能是 `node.cmd`，subprocess 无法执行）。

## 真机验证（Windows 11, DSH Desktop 0.12.1）

- 从零自动 provisioning：`launch.cjs` 在自己没有运行时的情况下，下载
  wheel 并建 venv 到 `%LOCALAPPDATA%\envoy-dsh\runtime`，耗时约 2 分钟，
  之后 `envoy --version` → `envoy-cli 0.1.24 (core 0.1.24)`。
- MCP 挂载：`initialize` 返回 `serverInfo {name: envoy, version: 0.1.24}`，
  `tools/list` 返回 core 工具集 5 个：
  `envoy_read, envoy_expand, envoy_search, envoy_observe, envoy_auth`。
- 端到端：`dsh --profile headless "用 envoy_read 读 https://example.com"` →
  返回 `material_id: mat-fcb4e3d411e0d7268d6c198c`、locator `blk-title`、
  status `ready`，且 `envoy` skill 被自动触发。

## 发现的第二个 Windows 问题（安装侧已修）

`pip install <github release URL>` 在 Windows 上返回 `HTTP error 403`
（release 资产走重定向，安装器的请求被拒）。`launch.cjs` 改为先用
`uv pip download` 或 `python -c urllib.request.urlretrieve` 下到临时文件，
再从本地文件安装；这与 `install.ps1` 里同样的修复一致。

## 已知限制

- `tools/list` 只给 core 工具集。`envoy_doctor`、`envoy_read_local_video`、
  `envoy_collection_checkpoint`、`envoy_replay`、`envoy_visual_history`
  需要 `ENVOY_MCP_TOOLSET=full`，这是设计如此，不是 Windows 特例。
- 本机把插件挂到 DSH 时，用了 profile 用户层 patch 覆盖 `command`
  指向 `DSH Desktop.exe` + `ELECTRON_RUN_AS_NODE=1`：本机 PATH 上的 `node`
  是围绕该二进制的 `.cmd` 包装，经 cmd.exe 会破坏 `-e` 参数。真实装了
  Node.js 的机器直接用 `command: node`，不需要这层覆盖。
- 覆盖 `config` 是整体替换而非合并，覆盖时必须写全 `serverName`/`transport`，
  否则 schema 校验失败（已在本机踩到并修正）。
