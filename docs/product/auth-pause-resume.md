# 一次登录，只续被挡住的那几条

采集一批链接时，同一个站点要求在浏览器里登录或过验证。这个页面说明 Envoy 怎么把
"人要做的事" 压缩成一次，并且让 Agent 退出后还能接着做。

## 一句话

同一站点在一批任务里只暂停一次；人打开一次窗口验证通过后，只继续被挡住的那几条，
其余已完成的条目和已保存的转写继续复用。

## 验收场景

一个批次 20 条（17 条可读、3 条因同一站点认证暂停）：

1. 第一轮采集：17 条完成，第 18 条遇到登录墙时把该站点标记为暂停，
   第 19、20 条不再重复撞墙（同一站点只读一次就停）。
2. 用户按提示执行一次
   `envoy auth open --action-id <id>`，窗口里完成登录；Envoy 随后用无头浏览器
   重新检查原链接，只有真的能读到内容才把暂停标成 `ok`。
3. 再次采集时带上 `--retry-reason auth`，只读那 3 条，不重读已完成的 17 条。
4. 中途退出 Agent，重开后在任意会话里执行 `envoy collect --list`，
   仍能看到该任务的完成数、暂停数和最后更新时间。

脚本化验收见 `scripts/acceptance/auth_pause_resume.py`（10 项检查，全部通过）。

## 命令

| 目的 | 命令 |
| --- | --- |
| 看有哪些暂停 | `envoy auth status --json` |
| 打开一次窗口并验证 | `envoy auth open --action-id <id>` |
| 不开窗口，只重新检查 | `envoy auth verify --action-id <id>` |
| 用恢复令牌找回暂停 | `envoy auth resume --token <token> --json` |
| 只重试被认证挡住的条目 | `envoy collect --url <主页> --out <文件> --retry-reason auth` |
| 找回过半途的任务 | `envoy collect --list [--json]` |
| 让某站点回到未登录 | `envoy auth clear --host <域名>` |

## 边界

- 打开窗口是"人已经做过动作"的信号，不是"已经成功"的信号。判定只来自对原链接的
  无头复检（`headless_reusable`）。
- 暂停按站点记录：同一站点后续条目改写为 `blocked`，不消耗读取预算。
- 恢复令牌是纯十六进制串，避免 `--token` 把 `-` 开头的值当成选项。
- DRM（Widevine）、IP/设备绑定的会话即使在浏览器里登录也可能仍然失败；
  这种情况会保持 `challenge_required`，而不是假装成功。
- 撤销方式是删除 Envoy 本地保存的登录状态（`envoy auth clear`），
  不是让用户去平台"退出所有设备"。
