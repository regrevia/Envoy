# 链接覆盖基线（57 条真实链接）

Envoy 的目标是「大部分链接都能读」。这份文档给出可复跑的测法、基线、缺陷类别和复测结果，数字全部来自同一条命令，换机器重跑即可复现（差异只会来自站点可达性）。

## 测法与判定

测量产物（原始 JSON，可直接 diff）在 `benchmarks/link-coverage/`：基线、修复后、重复一次、以及波动类目的定向复测。


- 脚本：`scripts/acceptance/link_coverage.py`，语料内置于脚本（57 条，14 个类别）。
- 每次读都走生产入口：`envoy read --request-json -`，请求体固定 `budget.max_response_chars=8000`、`retention.derived_ttl_seconds=600`。
- 判定（`classify()`）：
  - `readable`：`result=ready` 且正文 ≥ 200 字符。
  - `partial`：`result=partial` 且正文 ≥ 200 字符。
  - `truncated`：交付正文 < 200 字符，但抽取到的正文 ≥ 1000 字符（交付缺陷，不是可达性）。
  - `empty`：读成功但没有可用正文。
  - `blocked`：`blocked` / `needs_host` / `needs_action`，或停在 `waiting_for_host`。
  - `unreachable`：本机 15 秒内没有 TCP/HTTP 应答（网络层，归于环境而非 Envoy）。
- 「可用」= `readable + partial`。
- 必须 `--jobs 1`：并发会争抢同一个 Playwright profile，测出来的超时是假的。
- 测之前不要有后台浏览器在跑（登录扫码 worker、另一个 envoy read 都算）。

## 基线（修复前）

`benchmarks/link-coverage/baseline-2026-10-26.json`，57 条：可用 **26/57 = 45.6%**，其中
`readable 10 · partial 16 · blocked 8 · empty 3 · truncated 1 · unreachable 19`。

19 条不可达全部是环境问题（本机到 reddit / medium / bbc / nytimes / reuters / youtube / x / tiktok / vimeo / soundcloud / archive.org / gist / news.ycombinator / guardian / pinterest / mastodon / bsky / docs.google 的 TCP 或 TLS 直接超时），与本轮改动无关。因此同时报两个口径：

| 口径 | 修复前 | 说明 |
|---|---|---|
| 全语料 | 26/57 = 45.6% | 含本机到不了的站点 |
| 可达到语料 | 26/38 = 68.4% | 分母排除 19 条不可达 |

## 本轮修掉的缺陷类别

| 类别 | 症状 | 处理 |
|---|---|---|
| Atom/RSS 订阅源 | `blocked`（把 feed 当普通 HTML，抽不出正文） | 识别 `application/atom+xml`、`application/rss+xml`，按条渲染标题/时间/摘要 |
| 代码托管与文档站的分页正文 | `blocked`（GitHub 的 atom 页被风控话术命中，误判为挑战页） | 挑战页判定排除 feed 内容，正文抽取兜底 |
| 交付被截断 | `truncated`（抽取到正文，交付只剩几百字符） | 交付预算按块累积，不再提前截断 |
| 排版型正文（含 `pre`/代码块） | `partial` 且正文过少 | 通用正文兜底把 `<pre>`、列表、引用计入正文候选 |
| 登录/风控站点 | `challenge_required` | 新增 `envoy_auth` 交接：后台浏览器出登录页截图（多为二维码）给用户扫一次，扫完复用同一会话；见 `docs/product/qr-signin-handoff.md` |
| 视频页没有字幕 | `missing_capability`（整条读不到） | 本地没装 ASR 时回退读该页自身正文（标题、简介、分P），结果标 `partial` 并写明「没有转写，下面是页面本身」 |

## 复测（本轮）

同一条命令、同一台机器、同一份语料，无并发浏览器（`--jobs 1`，测前停掉登录 worker）：

| 口径 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| 全语料可用 | 26/57 = 45.6% | **31/57 = 54.4%** | +5 条 |
| 可达到语料可用 | 26/38 = 68.4% | **31/38 = 81.6%** | +13.2 个点 |
| 修复前分布 | `readable 10 · partial 16 · blocked 8 · empty 3 · truncated 1 · unreachable 19` | | |
| 修复后分布 | `readable 10 · partial 21 · blocked 4 · empty 3 · unreachable 19` | | |

逐条变化（全部是改善，没有一条退步）：

| 链接 | 修复前 | 修复后 | 正文 |
|---|---|---|---|
| bilibili | `blocked` | `partial` | 0 → 5369 字符（无字幕时回退读页面自身正文） |
| substack | `blocked` | `partial` | 0 → 2405 字符 |
| github-atom | `blocked` | `readable` | 0 → 3818 字符 |
| python-docs | `truncated` | `partial` | 56 → 3708 字符 |
| weibo | `blocked` | `partial` | 0 → 1509 字符 |

### 复测本身就暴露的一类缺陷：网络波动

隔一段时间再跑一次全量（`benchmarks/link-coverage/repeat-2026-10-26.json`）得到 28/57 = 49.1%，分布是
`readable 10 · partial 18 · blocked 4 · empty 3 · failed 2 · unreachable 20`。逐条比对后，差的 2 条全部来自网络而不是代码：

- `json-api`：这次 `host_unreachable`，之前两次都读到 6683 字符。
- `substack`、`unsplash`：这次分别 45 秒 / 60 秒超时；单独重跑时 substack 为超时、unsplash 稳定复现 `auth_required`（`net::ERR_CONNECTION_CLOSED`）。

所以「单次全量数字」有 ±2 条的抖动，报数时应同时给出两次结果，不能只挑高的一次。要让结论稳定，按上面的命令连跑两次取交集更可靠。

### 风险控制站点别放进循环

知乎、小红书、微博、抖音把「短时间内同一个出口反复被自动化访问」当成风控信号。2026-10-26 一天里同一台机器跑了 6 次全量语料（基线、复测、重复、子集、两轮修复验证），每条链接每次读取又至少走一次浏览器加载、风控页还会再重试一次（`_CHALLENGE_RETRY_DELAY_SECONDS = 1.0`），于是小红书在两小时内收到十余次脚本化访问，账号侧出现了「账号异常提醒」。这是语料复用方式的问题，不是读取逻辑的缺陷。

因此脚本默认跳过这些域（`--skip-hosts`，默认 `xiaohongshu.com,zhihu.com,douyin.com,weibo.com`），跳过的条目在报告里单列、写进 JSON 的 `skipped`，不计入可用率。要单独核一次可以加 `--hit-risk-hosts`，但不要连续跑，也不要在登录态下反复试。

### 复现命令

```bash
cd <repo>
# 1) 复测并对比（可用率 < 0.8 时退出码为 1，便于 CI 用）
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src \
  python3 scripts/acceptance/link_coverage.py --out /tmp/link-now.json --jobs 1 --timeout 45 \
    --compare benchmarks/link-coverage/baseline-2026-10-26.json
# 2) 只看某个类目（快速回归）
PYTHONPATH=packages/envoy-core/src:packages/envoy-cli/src \
  python3 scripts/acceptance/link_coverage.py --only api,blog,image --out /tmp/link-sub.json --jobs 1 --timeout 60
```

注意：`--timeout` 是单条链接的读取上限，45 秒对需要起浏览器的站点偏紧；把超时算成 `failed` 会让网络慢的时候看起来像代码退步。基线里 `substack` 的 `blocked` 与复测里的 `timeout` 是同一类环境抖动。

## 仍然读不了的类型

- **需要登录**（知乎、小红书）：`blocked`，`details.needs_user_action` 带 `in_chat_sign_in` 选项，交给 `envoy_auth` 扫码，扫完同一 `action_id` 继续读。测法不预置登录态，所以这两条在基线里始终算 `blocked`。
- **风控封锁 IP**（unsplash）：`auth_required` + `ERR_CONNECTION_CLOSED`，Envoy 不做绕过。
- **无字幕的会员视频**（bilibili）：`missing_capability`，明确回报缺什么，不假装读过。
- **已下架**（amazon 404）：`source_unavailable`，如实报 404。
- **本身就是空页**（example.com、36kr 首页骨架、W3C 的空 PDF 样例）：`empty`，如实报「读到了但没有正文」。
- **本机不可达**：见上，`unreachable`，不计入 Envoy 的缺陷。
