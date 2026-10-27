# 链接兼容性实验

把「Envoy 能读多少链接」从印象变成可复现的数字。语料、预检、指标、两次运行全部在
`scripts/acceptance/link_coverage.py` 里，报告只是它输出的 JSON 的整理。

## 结论（最新一轮：第十五轮）

数字全部来自 `scripts/acceptance/link_coverage.py` 的一次真实运行（`/tmp/accept/round9.json`，
`envoy 0.1.17`）。语料已扩到 93 条，预检剔 1 条死链，分母 **92**。

- **可用率**：全语料 `69/92 = 75.0%`（可读 20 + 部分 49）；同口径旧 89 行子集
  `66/89 = 74.2%`，且这 89 行与上一轮**逐行零翻转**。
- **完全可读 20 条、部分可读 49 条、空 2 条、拦截 8 条、本机出口不可达 13 条、失败 0 条。**
- **零超时**：p90 77.2s、max 127.5s，p90 由 5 条本机出口不可达的 77s 主导（不是产品慢）。
- 口径纪律：`unsupported` 的链接根本不会调用工具（判定见 `platforms.read_support_verdict`），
  因此语料里的 `unreachable` 全部是「支持但本机出口不通」，和「没适配」是两件事，两个分母不合并。

| 轮次 | 语料 | 分母 | 可用 | 可用率 | 完全可读 | p50 | 备注 |
|---|---|---|---|---|---|---|---|
| 4 | 71 | 70 | 46 | 65.7% | 15 | — | 登录墙改判前 |
| 5 | 73 | 72 | 48 | 66.7% | 15 | 10.8s | 两个中文站转正 |
| 6 | 73 | 72 | 47 | 65.3% | 15 | 7.29s | 薄页/DNS 判据变严，主动改判 |
| 7 | 83 | 82 | 59 | 72.0% | 18 | 6.19s | 10 条新行 + JSON 桩修复 |
| 8 | 90 | 89 | 66 | 74.2% | 18 | 4.43s | 7 条新行全可用 |
| 9 | 93 | 92 | 69 | 75.0% | 20 | 4.46s | 3 条新行全可用，零翻转 |
| 10 | 96 | 95 | 74 | 77.9% | 20 | 4.50s | 4 条新行全可用；YouTube 字幕显形 |
| 11 | 97 | 96 | 75 | 78.1% | 20 | 4.52s | MDN 补齐；wikipedia 回到 partial |
| 12 | 100 | 99 | 76 | 76.8% | 21 | 3.55s | 补 gitlab/douban-group/ximalaya；social、audio 首次有可用行 |
| 13 | 102 | 101 | 79 | 78.2% | 20 | 4.34s | 真 RFC PDF 替掉玩具 PDF；news 补 oschina/pingwest 到 6 可用 |
| 14 | 104 | 103 | 79 | 76.7% | 23 | 4.44s | 补 arstechnica/verge feed（均 readable）；guardian/youtube/platformer 升 supported |
| 15 | 107 | 106 | 83 | 78.3% | 25 | 4.24s | 补 4 条 feed/forum 行全部可用；dev.to/discourse.mozilla/arduino 升 supported |

旧 82 行子集（第 7、8 轮同分母同组成）：`59/82 = 72.0%` 持平；第 6 轮同子集为 65.3%，
即这两轮为真实提升 `+6.7pp`，其中第 7 轮 `+2.8pp`、第 8 轮 `0`（本轮只有加法）。

## 方法

### 语料

`CORPUS` 按页面族分组（`family`），这样一次回归能落到「哪类页面变差」，而不是只落到
「某个域名」。两类语料分开统计：

| family | 内容 |
| --- | --- |
| `reference` / `news` / `blog` / `code` / `forum` / `social` / `video` / `audio` / `image` / `document` / `spa` / `feed` / `api` / `web` | 通用网页语料，覆盖富文本、代码托管、论坛、音视频、PDF、SPA、RSS、API |
| `platforms` | 用户实际会粘进对话的平台链接：YouTube、Bilibili、b23.tv、TikTok、微博、知乎、视频号、小宇宙、X、Instagram、豆瓣、Reddit、GitHub、Medium |
| `cn-platform` | 本轮新适配对象单独成族：抖音视频页、v.douyin.com 短链、快手分享页、小宇宙单集、RSS 三源、豆瓣电影 |

`cn-platform` 不并进通用语料，否则「新适配到底有没有生效」会被平均掉。

微信公众号的具体文章、视频号的具体内容只以微信客户端内复制的链接存在，没有公开稳定的
可长期引用的地址；语料里放平台公开页（`https://mp.weixin.qq.com/`、
`https://channels.weixin.qq.com/`），标注为「平台公开页」，不编造文章 URL。
语料里没有小红书链接（本轮明确不测）。

### 死链预检：必须先与产品同出口

「curl 连不上」不等于「Envoy 读不了」。这台机器有系统代理（`127.0.0.1:7897`），而
`packages/envoy-cli/src/envoy_cli/netpolicy.py` 的 `proxy_plan()` 是「直连优先，失败再走
探测到的代理」。因此预检不自己发明连通性判断，而是：

1. 用 `python -c "from envoy_cli.netpolicy import detected_proxy; print(detected_proxy())"`
   拿到产品自己会用的代理（模块不可用时才退回 `getproxies()`）；
2. 按同样的顺序用 httpx 探：先直连，失败再走该代理；
3. 只有 **404 / 410** 算 dead，并从分母里剔除；DNS 解析失败先重试一次，再交给代理那一路（代理端解析，本机 resolver 抖动不算证据），两路都不解析记 `unreachable`，不判死；
4. 403 / 429 / 人机校验页算「站点有响应」，链接留在分母里，读不出来记 `blocked`
   （产品问题），不记死链；
5. 两条路都不通才算 `unreachable`，单独成行，不混进产品失败。

### 指标（固定口径）

- `total`：语料条数；`dead`：预检判死、剔除出分母的条数；`denominator = total - dead`。
- 每条的 verdict：`readable` / `partial` / `empty` / `truncated` / `blocked` /
  `unreachable` / `failed`。判定基于响应里的 `result`（状态）与
  `blocks[].content.text`（实际交付正文）——`result` 是状态字符串，不是正文长度。
- `usable = readable + partial`，`usable_rate = usable / denominator`。
- `reachable_denominator = denominator - unreachable`，另给
  `reachable_readable_rate = readable / reachable_denominator`。分母里去掉环境不可达，
  才是在衡量产品。
- 延迟给 `mean` / `p50` / `p90` / `max`（秒），按每条第一次运行的耗时统计。
- 抖动：`--repeats 2` 跑两遍，报 verdict 翻转数、status 翻转数、两次耗时差。

### 两个分母：含 / 不含风控站点

知乎、微博、抖音、小红书对重复自动化访问会触发风控，默认跳过（`RISK_HOSTS`）。
`--include-risk-hosts` 会一本正经地读它们一次，用于填「含风控」分母。报告同时给
`all`（含风控）与 `no_risk`（不含风控）两个口径，两个数不会互相冒充。

### 运行

```bash
# 只预检死链/不可达
uv run --python 3.11 --with-editable packages/envoy-core --with-editable packages/envoy-cli python scripts/acceptance/link_coverage.py --precheck-only

# 普通测量（不碰风控站点）
uv run --python 3.11 --with-editable packages/envoy-core --with-editable packages/envoy-cli python scripts/acceptance/link_coverage.py \
  --jobs 2 --timeout 180 --out /tmp/accept/run.json

# 需要两个分母时，只跑一次、显式带上风控站点
uv run --python 3.11 --with-editable packages/envoy-core --with-editable packages/envoy-cli python scripts/acceptance/link_coverage.py \
  --include-risk-hosts --jobs 2 --timeout 180 --repeats 2 --out /tmp/accept/run.json

# 与上一轮对比
uv run --python 3.11 --with-editable packages/envoy-core --with-editable packages/envoy-cli python scripts/acceptance/link_coverage.py \
  --include-risk-hosts --jobs 2 --timeout 180 --out /tmp/accept/round2.json \
  --compare /tmp/accept/baseline-round1.json
```

`--only` 按 **family** 过滤（不是按单条 label），例如 `--only cn-platform` 只跑本轮新适配对象。

约束：默认每次读 `timeout 180s`，测长视频时按需提到 `240s`（本轮即 240s）；请求内
`max_work_seconds 120`、并发 `--jobs ≤ 3`
（默认 2；Playwright 会话在同一 profile 上并发会互相拖慢，所以默认不拉满）。

## 结果

命令（原始 JSON 在 `/tmp/accept/round3-fixed.json`）：

```bash
python3 scripts/acceptance/link_coverage.py --include-risk-hosts --jobs 3 --timeout 240 \
  --out /tmp/accept/round3-fixed.json --compare /tmp/accept/round2-current.json --envoy /tmp/envoy-live
```

环境：`envoy-cli 0.1.14 (core 0.1.14)`，预检代理 `http://127.0.0.1:7897`，语料 74 条。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | usable | usable_rate | reachable-readable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `all`（含风控） | 73 | 16 | 34 | 5 | 5 | 13 | 50 | 68.5% | 26.7% |
| `no_risk`（不含风控） | 69 | 16 | 32 | 4 | 4 | 13 | 48 | 69.6% | 28.6% |

按页面族（`all` 口径，readable / partial / empty / blocked / unreachable）：

| family | 结果 | family | 结果 |
| --- | --- | --- | --- |
| `reference` | 1 / 5 / 1 / 0 / 0 | `document` | 2 / 2 / 1 / 0 / 0 |
| `news` | 0 / 0 / 0 / 0 / 4 | `spa` | 2 / 2 / 0 / 0 / 0 |
| `blog` | 0 / 3 / 0 / 0 / 1 | `feed` | 1 / 0 / 0 / 0 / 1 |
| `code` | 2 / 3 / 0 / 0 / 1 | `api` | 1 / 0 / 0 / 0 / 0 |
| `forum` | 1 / 2 / 0 / 0 / 2 | `web` | 0 / 4 / 0 / 0 / 1 |
| `social` | 0 / 1 / 0 / 2 / 2 | `platforms` | 2 / 6 / 0 / 0 / 0 |
| `video` | 0 / 2 / 0 / 2 / 0 | `cn-platform` | 3 / 4 / 3 / 0 / 0 |
| `audio` / `image` | 0 / 0 / 0 / 0 / 1；1 / 0 / 0 / 1 / 0 | | |

延迟（配对 58 条，两轮都 live）：`mean 16.0s → 19.7s`、`p50 4.6s → 5.3s`、
`p90 56.5s → 63.3s`、`max 82.6s → 124.6s`。

配对得失（58 条）：

| 变化 | 链接 | 证据 |
| --- | --- | --- |
| 新增可用 | `douyin-short` | `empty 71 字 → partial 6622 字` |
| 新增可用 | `36kr`（文章） | `empty 58 字 → partial 5697 字` |
| 退为不可用 | `douyin-video` | `partial 3642 字 → empty 113 字`（风控抖动） |

13 条 `unreachable` 与产品无关，是这台机器的出口问题（同一 URL 用 `curl` 直连/代理都是
`000`，`SSL: UNEXPECTED_EOF_WHILE_READING` 或 DNS 间歇失败）：bbc、reuters、nytimes、
guardian、medium、gist、hackernews、reddit、mastodon、bluesky、soundcloud、hn-rss、
archive-org。它们不进 `reachable_*` 口径，也不记成 `failed`。

预检口径这轮修过一次：原先本机 resolver 失败就判 `dead` 并删出分母（第二轮因此出现 6 条
假死链），现在只有 **404 / 410** 判死；DNS 失败先重试一次，再让代理那一路解析，两路都不
解析记 `unreachable`。回归测试见 `tests/test_link_coverage_script.py`。

## 仍然不支持的链接

「不支持」= 纯 HTTP / 浏览器回退都拿不到可引用正文，且原因在站点侧（需登录、人机校验、
DRM），不是这台机器的网络问题。清单同时是 `read_support_verdict()` 里 `unsupported`
判定的证据来源。

| 链接族 | 现象 | 证据 |
| --- | --- | --- |
| 快手 视频 / 分享页（`kuaishou.com`、`k.kuaishou.com`） | 纯 HTTP 只剩 63 字节 | 静态 HTML 返回 `{"result":2,...}`；带 `did` cookie 拿到 76KB 但只是 SPA 外壳（`<title>短视频-快手</title>`，无正文数据）；GraphQL `visionVideoDetail` 返回 `result: 400002` + `captcha.zt.kuaishou.com` 校验地址 |
| YouTube（`youtube.com/watch`） | `challenge_required` | `yt-dlp` 直接报 `Sign in to confirm you're not a bot`；本机也没有 deno，无 JS runtime 可解玩家脚本 |
| Vimeo | 本机出口不通（`000`），产品侧已接 oEmbed | `vimeo.com/api/oembed.json?url=` 直接给出标题/作者/时长，`backend=vimeo`；本机 `curl` 与 `envoy read` 都是连接超时，所以只算单测通过、没算进语料可用率 |
| X 个人主页（`x.com/<user>`） | `blocked / empty_content` | 隔离浏览器同样为空，时间线需登录 |
| 知乎问题页 | `blocked / challenge_required` | 直连与代理都是 403 + 安全验证页 |
| Instagram 帖子/Reels | `blocked / auth_required` | 页面只有登录墙外壳（「Post isn't available / Log in / Sign up」）。旧版把 189 字墙文当 `partial` 计成可用，已修：短页命中登录墙标记即报 `blocked`，不再充作正文 |
| Unsplash | `blocked` | 人机校验页，隔离浏览器也过不去 |
| LinkedIn | 规则层 `unsupported` | `_UNSUPPORTED_HOSTS`，离线就判死，不调工具 |
| 微信文章 / 视频号具体内容 | 语料只放平台公开页 | 真实文章链接只存在于微信客户端内，不做伪造 URL |

据此把「已验证可读」的主机从 30 条扩到 78 条（`packages/envoy-cli/src/envoy_cli/platforms.py`
的 `_SUPPORTED_HOSTS`，每条都能在上面的语料里找到一次 ≥200 字正文的读取记录），
DSH 插件的 `integrations/dsh/envoy-dsh/link-verdicts.json` 同步再生成，宿主侧据此判断
「该不该调工具」。

## 后续两处补正（同版本内）

1. **登录墙不再算「部分可读」**。Instagram Reels 旧读数 189 字 `partial`（正文是
   「Post isn't available / Log in / Sign up for Instagram」），其实一行来源内容都没有。现在
   `< 600` 字且命中登录墙标记的页面统一返回 `blocked / auth_required` 并带
   `action_required`，避免用墙文抬高可用率。回归测试 `tests/cli/test_login_wall.py`。
2. **站点后端与判定表对齐**。`fallbacks.SITE_BACKENDS` 里每个公开端点都配一条样例 URL，
   `tests/test_fallback_site_parity.py` 断言这条样例既匹配后端正则、又在
   `read_support_verdict()` 里是 `supported`，防止「后端能读但路由说 unknown/unsupported」。
   本轮据此新增 Vimeo（oEmbed）、Hacker News（Firebase item API，live 验证：`reader=public_api:hackernews`，
   标题 + 正文 + 5 条评论共 2481 字），并把 Pinterest、Figma Community、`api.github.com`
   加进 `_SUPPORTED_HOSTS`（依据是 round-3 语料里它们已经 `ready`，判定却还是 `unknown`）。
3. **公开端点路径上的标题被拆成单字符**：`_clean_text()` 收的是 `list[str]`，fallback 标题却
   按字符串传入，`"\n".join(str)` 于是把标题变成「M\ny\n…」。已修并加回归断言（同一路径
   也影响 RSS/feed 备选标题），JSON 里 `blocks[0].text` 现在是完整一行。

## 第四轮（登录墙改判 + 快速失败）

命令（原始 JSON `/tmp/accept/round4.json`）：

```bash
python3 scripts/acceptance/link_coverage.py --jobs 3 --timeout 240 \
  --out /tmp/accept/round4.json --envoy /tmp/envoy-live
```

这轮没带 `--include-risk-hosts`，所以抖音两条风控链接不在分母里，分母 70 条、无死链。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | usable | usable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `no_risk` | 70 | 15 | 31 | 4 | 6 | 14 | 46 | 65.7% |

延迟：`n=70 mean 33.4s p50 14.7s p90 80.7s max 109.2s`（比第三轮慢，原因是
`--include-risk-hosts` 少了抖音两次长跑，但 tiktok/unsplash/wechat 这类超时页仍在分母里）。

配对（两轮都 live 且都可达，n=56）：`usable 46 → 45（82.1% → 80.4%）`，
`readable 15 → 14（26.8% → 25.0%）`。两处变化都不是能力退化，而是**把原先冒充正文的登录墙改判**：

| 链接 | 第三轮 | 第四轮 | 说明 |
| --- | --- | --- | --- |
| `wechat-mp-platform`（`mp.weixin.qq.com/`） | `readable 566 字` | `blocked / auth_required` | 566 字其实是扫码登录表单 |
| `instagram-reel` | `empty 189 字`（旧版曾计 `partial`） | `blocked / auth_required` | 189 字是「Post isn't available」墙 |

也就是说本轮可用率下降 1 条、正好等于两条被纠正的假正文之差（第二个在第三轮已从 `partial`
降级成 `empty`），**没有真实的读取回退**。

顺带把「静态 HTML 里已经是登录墙」的路径改成不启浏览器：命中的短页直接
`blocked / auth_required`（`mp.weixin.qq.com/` 实测 85.9s → 29s）。挑战页
（`安全验证，请完成登录后继续`）不适用这条快路，仍然先让浏览器解一次人机校验，
回归测试 `tests/cli/test_login_wall.py::test_static_login_wall_skips_the_browser`。

本轮其它读数变化（都是环境抖动，不是代码）：`kuaishou-share/video` 预检 200、静态只剩
78 字节空壳（`{"result":2,...}`），`unsplash` 仍 58s 后被 Anubis 挡下，`tiktok` 675 字
`partial`（第三方嵌入壳），`figma-community` 556 字 `readable`、`pinterest` 267 字
`readable`（与第三轮一致，这也是把它们写进 `_SUPPORTED_HOSTS` 的依据）。

## 第五轮（两个中文站转正 + 两条诚实话）

命令（原始 JSON `/tmp/accept/round5.json`，73 条语料）：

```bash
python3 scripts/acceptance/link_coverage.py --jobs 3 --timeout 240 \
  --out /tmp/accept/round5.json --envoy /tmp/envoy-live
```

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | usable | usable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `no_risk`（=`all`） | 72 | 15 | 33 | 5 | 5 | 14 | 48 | 66.7% |

死链 1 条（`amazon` 预检 404，按规则移出分母）；`--include-risk-hosts` 未带，抖音两条不在分母里。
延迟 `n=72 mean 28.8s p50 10.8s p90 77.5s max 100.8s`，比第四轮快 4.6s（跨轮环境抖动，
不作为能力指标）。

**新增三条中文站语料全部可用**（家族 `cn-platform` 由 8 条增到 11 条）：

| 新增 | 结果 | 字数 | 耗时 | reader |
| --- | --- | --- | --- | --- |
| `douban-book`（`book.douban.com/subject/1084336/`） | `partial` | 6794 | 2.7s | `static_http` |
| `cnblogs-article`（`cnblogs.com/rossiXYZ/p/15553994.html`） | `partial` | 7983 | 1.4s | `static_http` |
| `juejin-post`（`juejin.cn/post/6844903967407734797`） | `readable` | 3258 | 0.8s | `public_api:juejin` |

单跑（独立预算）时 `cnblogs` 两篇分别是 13260 / 13153 字，批跑预算收紧后落在 7983 字，
两者都是 `partial` 而不是空壳。依据这两篇与豆瓣读书页，`book.douban.com`、`cnblogs.com`
进了 `_SUPPORTED_HOSTS`（路由器不再对它们说 `unknown`），`link-verdicts.json` 同步重生成
（`python3 scripts/export_link_verdicts.py`，`--check` 通过）。

配对（第四/五轮都 live 且可达，n=54）：`usable 45 → 45（83.3% → 83.3%）`，
`readable 14 → 14（25.9% → 25.9%）`——**零净变化**，三处互换都是环境抖动而非能力变化：

| 链接 | 第四轮 | 第五轮 | 说明 |
| --- | --- | --- | --- |
| `youtube` | `blocked 0` | `partial 427` | 字幕端仍拒（无 deno），这次拿到 427 字页面描述 |
| `tiktok` | `partial 675` | `blocked 0` | 第三方嵌入壳时有时无 |
| `instagram-reel` | `blocked 0` | `empty 9` | 9 字是 `ready` + `<200` 字的验收假象，非真读取 |

### 本轮同时修掉的两条“不诚实”

1. **薄平台页曾经报 `ready`**：平台页正文短于 300 字（`_THIN_PLATFORM_PAGE_CHARS`）时只写
   `details.thin_platform_page` 但结果仍是 `ready`，于是「只有标题 + 空段落」的微信壳页
   以 76 字被算成完整。现在 `thin_platform_page` 参与 `result` 判定 → `partial`，
   回归测试 `tests/cli/test_platforms.py::test_platform_page_browser_fallback_reads_content_without_assuming_video`
   与 `tests/cli/test_login_wall.py::test_thin_platform_page_is_partial_not_ready`。
2. **DNS 解析失败曾被当成「不是公网 URL」**：`_is_public_http_url` 对 loopback/私网和
   DNS 失败都返回 `False`，于是 `gist.github.com` 那次解析抖动被报成
   `source_unavailable / Only public HTTP(S) URLs are allowed`（策略拒绝），而实际是网络不可达。
   现在先单独判断解析：失败报 `network_error` + “DNS lookup failed”，策略拒绝保持原文案；
   验收脚本按 `dns` 标记把它归到 `unreachable`，不再污染可读性分母。

回归：`pytest tests/cli` → **667 passed / 4 failed（已知 `reportlab` PDF 环境缺依赖）/ 1 deselected**；
`tests/test_platform_classification.py tests/test_link_routing_hooks.py tests/test_link_coverage_script.py`
137 passed；`export_link_verdicts.py --check` 干净。

## 第六轮（薄页判据与 DNS 区分的实测）

命令同第五轮，语料 73 条（本轮尚未含 4 条 `cn-media` 新行）：

```bash
python3 scripts/acceptance/link_coverage.py --jobs 3 --timeout 240 \
  --out /tmp/accept/round6.json --envoy /tmp/envoy-live
```

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | usable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `no_risk` | 72 | 15 | 32 | 4 | 6 | 14 | 1 | 47 | 65.3% |

配对（第五/六轮都 live 且可达，n=56）：`usable 47 → 46（83.9% → 82.1%）`，
`readable 15 → 14（26.8% → 25.0%）`。四处翻转：

| 链接 | 第五轮 | 第六轮 | 说明 |
| --- | --- | --- | --- |
| `youtube-youtu.be` | `readable 2949` | `failed` 240.0s | 撞 `--timeout 240` 上限，纯环境/预算 |
| `youtube` | `partial 427` | `blocked 0` | 字幕端拒绝，与第五轮相反 |
| `tiktok` | `blocked 0` | `partial 517` | 与第五轮相反，再次说明第三方嵌入壳不稳 |
| `instagram-reel` | `empty 9` | `blocked 0` | 两者都不是可用读取 |

净变化 -1 全部来自那条 240s 超时，不是能力回退；把超时行排除后配对 `n=55 usable 46 → 46`。

薄页判据这一轮**没有**改变任何行的 verdict：语料里唯一的薄平台页是 kuaishou
（`empty`，78 字），它走的是分享 API 的 JSON 分支，没进薄页逻辑——这条在下面单独修掉。
`empty_content` 与 `network_error` 的区分在本轮生效：`gist` 稳定报 `network_error`
（`DNS lookup failed`），不再被当成策略拒绝。

### 再补一条：JSON 状态桩不再算完成

`kuaishou-share` / `kuaishou-video` 的 78 字是分享接口的
`{"result":2,"error_msg":null,"request_id":"…"}`，此前以 `result=ready`、
`mime_type=application/json` 交付，等于把「接口说失败」当成正文。现在 `_parse_text_source`
对 JSON 载荷做内容判定：没有任何 ≥40 字字符串的文档按 `empty_content` 处理，
报「The endpoint returned a JSON status stub instead of readable content」。
真实 API JSON（GitHub、掘金等）不受影响，回归
`tests/cli/test_login_wall.py::test_json_status_stub_is_not_a_complete_read`。

回归：`pytest tests/cli` → **668 passed / 4 failed（已知 `reportlab` 环境缺依赖）/ 1 deselected**。

## 第七轮（新增 10 条可用行，同语料子集 65.3% → 68.1%）

本轮语料从 77 行扩展到 83 行：4 条 `cn-media`（虎嗅式国内媒体首页）+ 6 条静态 HTML
开发/技术媒体。命令同前，`--jobs 3 --timeout 240`。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | usable_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 全语料 | 82（1 条 dead） | 18 | 41 | 2 | 0 | 9 | 12 | 0 | 59 | 72.0% |
| 旧 72 行子集 | 72 | 17 | 32 | 2 | 0 | 9 | 12 | 0 | 49 | **68.1%** |

旧子集与第六轮**同分母同组成**，可直接比：`usable 47 → 49（65.3% → 68.1%，+2.8pp）`，
`readable 15 → 17`（旧子集）。新增 10 行全部可用（3 种 `partial` + 1 `readable` 的国内媒体，
6 条 `partial` 技术媒体），因此全语料 72.0% 里有一部分来自语料构成变化，必须两组数字一起看。

延迟同时明显变好（旧子集 n=72）：mean `32.9 → 24.9s`，p50 `7.29 → 6.19s`，
p90 `77.48 → 77.25s`，max `240.0 → 109.0s`——第六轮那条 240s 超时消失，本轮零超时。

新增行实测：

| 行 | 家族 | 结果 | 字数 | 耗时 | 通道 |
| --- | --- | --- | --- | --- | --- |
| `ithome-home` | cn-media | partial | 7645 | 1.1s | static_http |
| `ifanr-home` | cn-media | partial | 6189 | 1.1s | static_http |
| `pingwest-home` | cn-media | readable | 792 | 3.3s | static_http |
| `qbitai-home` | cn-media | partial | 2134 | 1.3s | static_http |
| `devto-home` | blog | partial | 6441 | 20.6s | static_http |
| `zenn-home` | blog | partial | 567 | 3.0s | static_http |
| `qiita-home` | blog | partial | 3275 | 2.7s | static_http |
| `brendangregg-blog` | blog | partial | 7947 | 3.0s | static_http |
| `lwn-home` | news | partial | 7981 | 1.8s | static_http |
| `techcrunch-home` | news | partial | 7133 | 4.6s | static_http |

非可用行（22 条）现在全部落在三类可解释原因上：
12 条 `network_error`（本机到 BBC/Reuters/NYT/Guardian/Medium/HN/Reddit/Mastodon/Bluesky/
SoundCloud/archive.org 出口不通，不是策略拒绝）、
9 条 `blocked`（YouTube 字幕端 `challenge_required`、Vimeo 与 TikTok `missing_capability`、
X/Unsplash `challenge_required`、微信与 Instagram `auth_required`、kuaishou 两条 `empty_content`）、
2 条 `empty`（`example.com` 170 字、`w3c-pdf` 23 字，都是页面本身没有正文）。

第六轮记录的两处翻转已经回落：`youtube-youtu.be` 不再撞超时（本轮可用），
`tiktok` 又回到 `blocked`。第三方嵌入壳在多次测量间不稳定，单轮翻转不作为能力结论。

回归：`pytest tests/cli` → **669 passed / 4 failed（已知 `reportlab` 缺依赖）/ 1 deselected**；
分类+路由+语料脚本 **143 passed**；DSH 插件 `npm test` **9 passed**。

## 第八轮（新增 7 条全部可用，全语料 74.2%）

本轮只做加法：把上一轮探到、但还没进语料的 7 个静态 HTML 站点补进支持清单并跑验收。
语料 83 → 90 行（新增 `cn-media` 5 条财经媒体、`blog` 1 条、`image` 1 条），死链仍是
1 条（`amazon`），分母 89。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 89 | 18 | 48 | 2 | 0 | 8 | 13 | 0 | 66 | **74.2%** |
| 旧 82 行子集（与第 7 轮同分母同组成） | 82 | 17 | 42 | 2 | 0 | 8 | 13 | 0 | 59 | **72.0%** |

- 7 条新增行**全部可用**，没有一条落到 `empty`/`blocked`：`jiemian-home` 6044 字 / 1.3s、
  `cls-home` 6158 / 1.4s、`yicai-home` 7866 / 4.6s、`caixin-home` 5227 / 4.4s、
  `wallstreetcn-home` 1414 / 1.3s、`ruanyf-blog` 591 / 3.9s（`readable`）、
  `wikimedia-commons` 5017 / 3.8s，全部走 `static_http`，没有一个启浏览器、没有一个超时。
- 旧 82 行子集与第 7 轮完全同口径，可用率 `59/82 = 72.0%` 持平：这轮没有真实的读取回退，
  增长全部来自新增行；全语料从 72.0% 升到 74.2% 是分母变大后新行零损失的结果。
- 三处单轮翻转，都不当能力结论：`tiktok` `blocked → partial(450 字)`（第三方 embed 时有时无，
  同一张卡片第 6 轮也翻过）；`gist` `partial(7812 字) → unreachable`（本机出口，两次都是
  `network_error`）；`pypi` `readable(227 字) → partial(7762 字)`——字数反而涨了 33 倍，
  只是结果从 `ready` 降成 `partial`，属判定口径差异不是内容变少。
- 延迟无明显变化：旧子集 mean 22.5s、p50 5.36s、p90 77.20s、max 127.5s，仍然零超时。
  p90 由 5 条本机出口不可达的 77s 主导，和产品端无关。
- 家族分布：`cn-media` 9 条 1 可读 / 8 部分（唯一可读仍是 `qbitai`），`blog` 9 条
  1 可读 / 7 部分 / 1 不可达，`image` 3 条 1 可读 / 1 部分 / 1 拦截（`unsplash` 仍是挑战页）。
- 回归：`pytest tests/cli` → 685 passed / 4 failed（已知 `reportlab` 缺依赖）/ 1 deselected；
  `pytest tests/test_platform_classification.py tests/test_link_routing_hooks.py tests/test_link_coverage_script.py`
  → 156 passed；插件 `npm test` → 9 passed。

## 第十五轮（107 行 / 可用 78.3%，4 条新行全部可用，forum 与 audio 两族同时改善）

本轮加 4 条行：feed 系 `simplecast-podcast`（audio 族）、`devto`、`discourse-mozilla`、
`arduino-forum`（forum 族）。语料 104 → 107，死链 1，分母 106。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 106 | 25 | 58 | 1 | 8 | 14 | 0 | 83 | **78.3%** |
| 可达口径（剔 unreachable） | 92 | 25 | 58 | 1 | 8 | 0 | 0 | 83 | **90.2%** |
| 同组成 102 行子集（= 第 14 轮分母） | 102 | 24 | 55 | 1 | 7 | 14 | 0 | 79 | **77.5%** |

- 4 条新行全部可用，是历轮里新行命中率最高的一轮：
  `discourse-mozilla` readable 2706 字 / 1.7s、`arduino-forum` readable 354 字 / 1.6s、
  `devto` partial 6652 字 / 1.4s、`simplecast-podcast` partial 7284 字 / 12.8s。
  forum 族 8 行变成 3 readable + 3 partial + 2 unreachable（可用率 75%），
  audio 族 3 行 2 partial + 1 unreachable（可用率 67%，soundcloud 仍是出口问题）。
- 三个 host 提升为 `supported`：`dev.to`、`discourse.mozilla.org`、`forum.arduino.cc`。
  本轮做完交叉验证：**语料里已没有任何「实测可读但判为 unknown」的行**，
  剩下的 unknown 行全部是 unreachable / blocked / empty，也就是说
  「先试 Envoy 再回退」的无谓开销在语料范围内已经清零。
- 抖动：`wikipedia` 与 `guardian` 掉到 unreachable（出口），`wikipedia-zh` 从
  unreachable 回到 partial 7936 字，`gitlab-readme` 从 empty 回到 partial 7950 字，
  `tiktok` 从 blocked 变 partial 517 字，`unsplash` 从 unreachable 变 challenge。
  净效果：same-scope 子集 +1 可用行（78 → 79），全语料 +4（79 → 83）。
- 时延：mean 22.6s / p50 4.24s / p90 77.31s / max 108.9s。

## 第十四轮（104 行 / 可用 76.7%，feed 族从 2 可用升到 5 可用）

本轮加 2 条 feed 行（`arstechnica-rss`、`verge-rss`），语料 102 → 104，死链 1，
分母 103。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 103 | 23 | 56 | 2 | 0 | 9 | 13 | 0 | 79 | **76.7%** |
| 可达口径（剔 unreachable） | 90 | 23 | 56 | 2 | 0 | 9 | 0 | 0 | 79 | **87.8%** |
| 同组成 101 行子集（= 第 13 轮分母） | 101 | 22 | 55 | 1 | 0 | 8 | 13 | 0 | 77 | **76.2%** |

- 两条新 feed 行一次通过：`arstechnica-rss` `readable` 5993 字 / 2.7s，
  `verge-rss` `readable` 5315 字 / 2.5s（`public_api:feed`）。feed 族 6 行里
  4 readable + 1 partial，只剩 `hn-rss` 是本机出口阻断。news 的
  `theguardian.com`、blog 的 `platformer.news` 上一轮已实测出正文，本轮把三个
  host 从 `unknown` 提升到 `supported`（`youtube.com` 同类）：语料实测交叉验证
  —— `supported` 口径可用率 89.4%（76/85），`unknown` 口径只有 20%（3/15），
  提升这 3 个等于少 3 次「先试 Envoy 再回退」的无谓开销。
- 可用绝对数没变（79），分母 +2，所以全语料可用率从 78.2% 回落到 76.7%：
  加进来的 2 行都可用，同时 3 行掉了（`oschina` 从 `partial` 5637 变
  `blocked challenge_required`、`gitlab-readme` 从 7904 字变 `empty` 167 字、
  `wikipedia-zh` 从 7936 字变 `unreachable`）。这 3 条都单独复跑过：
  `oschina` 复现 challenge（真防护收紧），`wikipedia-zh` 复现 network_error
  （出口问题），`gitlab-readme` 复跑 `partial` 11242 字 → 是批量并发下的瞬时失败，
  不是能力回退。同组成子集 77/101 = 76.2%，-2.0pt 全部由这 3 次抖动造成。
- 时延：mean 19.2s / p50 4.44s / p90 71.09s / max 112.2s。

## 第十三轮（102 行 / 可用 78.2%，news 族首次有 6 条可用）

语料 100 → 102 行：把 23 字的玩具 PDF（`w3c-pdf`，永远 `empty`）换成真实
`https://www.rfc-editor.org/rfc/rfc9110.pdf`，新增 `news/oschina`、`news/pingwest`。
死链 1，分母 101。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 101 | 20 | 59 | 1 | 0 | 7 | 13 | 1 | 79 | **78.2%** |
| 可达口径（剔 unreachable） | 88 | 20 | 59 | 1 | 0 | 7 | 0 | 1 | 79 | **89.8%** |
| 旧 98 行子集（与第 12 轮同组成） | 98 | 19 | 57 | 1 | 0 | 7 | 13 | 1 | 76 | **77.6%** |

- 三条新行全部可用：`rfc9110-pdf` `partial` 7389 字 / 5.1s（真 PDF，替换掉
  23 字的 dummy 玩具行）；`oschina` `partial` 5637 字 / 1.9s；`pingwest`
  `partial` 2542 字 / 1.3s。news 族从 3 可用升到 6 可用（9 行里 3 条是本机出口阻断的
  bbc/reuters/nytimes）。
- 相比第 12 轮（76/99 = 76.8%），全语料可用率 +1.4pt 到 78.2%；同 98 行子集
  77.6% vs 第 12 轮同组成 78.1%，差 0.5pt，完全在单轮出口抖动范围内
  （guardian 本轮连上 `partial` 5786、unsplash 本轮退回 `unreachable`）。
- `pingwest-home` 本轮批量 `failed`（0 字，4.3s，无 error_code），单跑立刻
  `ready` 764 字——是并发下的瞬时失败，不是能力缺口，下一轮若复现再查。
- 时延：mean 21.2s / p50 4.34s / p90 72.81s / max 113.0s。

## 第十二轮（100 行 / 可用 76.8%，补 social 与 audio 两个零可用族）

语料 97 → 100 行（新增 `gitlab-readme`、`douban-group`、`ximalaya-album`），死链 1，分母 99。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 99 | 21 | 55 | 2 | 0 | 8 | 13 | 0 | 76 | **76.8%** |
| 可达口径（剔 unreachable） | 86 | 21 | 55 | 2 | 0 | 8 | 0 | 0 | 76 | **88.4%** |
| 旧 96 行子集（与第 11 轮同组成） | 96 | 21 | 54 | 2 | 0 | 7 | 12 | 0 | 75 | **78.1%** |

- 新增三行全部可用：`douban-group` `readable` 1253 字 / 1.3s（`static_http`，**social 族
  第一次有可用行**）；`ximalaya-album` `partial` 504 字 / 22.5s（`isolated_chromium`，
  声量偏薄但真实；**audio 族第一次有可用行**）；`gitlab-readme` `partial` 7961 字 / 25.9s。
- 语料扩到 100 行后全语料率从 78.1% 掉到 76.8%，纯粹是分母多了 3 行里含 2 个新族的历史
  弱项（social 另有 3 行不可用、audio 的 soundcloud 仍不可达），不是能力退步——同 96 行
  子集仍是 78.1%，与第 11 轮完全一致。
- 三条翻转全是出口抖动：`guardian` `partial`(5960) → `unreachable`、`gist`
  `partial`(7829) → `unreachable`、`unsplash` `unreachable` → `blocked`。这三条在本机
  出口稳定时能达到 `partial`，本轮只是没连上，不作能力结论。
- 时延：mean 22.0s / p50 3.55s / p90 77.28s / max 108.9s；p90 与 max 全部由
  `unreachable` 行的 77s 出口超时定义，与历史各轮一致。

## 第十一轮（97 行 / 可用 78.1%，MDN 补齐，YouTube 字幕稳定）

补齐 MDN 那行，语料 96 → 97 行，死链 1，分母 96。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 96 | 20 | 55 | 2 | 0 | 7 | 12 | 0 | 75 | **78.1%** |
| 旧 95 行子集（与第 10 轮同组成） | 95 | 20 | 54 | 2 | 0 | 7 | 12 | 0 | 74 | **77.9%** |

- `mdn-http` 7540 字 / 1.9s（`partial`，`static_http`）：MDN 是文档站里最难的一类（侧栏+导航
  噪声多），能读但截断，属正常结果。
- 三条翻转继续证明「翻转=出口」：`wikipedia` 回到 `partial`(7992)，说明第 10 轮那次
  `unreachable` 是抖动；`unsplash` `blocked` → `unreachable`；`tiktok` `partial`(517) → `blocked`。
  短视频站两轮之间在「拿到 500 字摘要」和「被拦」之间摆动，不作能力结论。
- YouTube 本轮仍 `partial`，与第 10 轮一致：字幕通道稳定，之前的 `blocked` 是通路问题。

## 第十轮（YouTube 字幕通道打通，全语料 77.9%）

再补 4 条新行（`solidot`、`guancha`、`thepaper`、`docs.python.org`），语料 93 → 96 行，
死链 1 条，分母 95。MDN 那行因为锚点没匹配上没写进去，留到第十一轮补齐。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 95 | 20 | 54 | 2 | 0 | 7 | 12 | 0 | 74 | **77.9%** |
| 旧 91 行子集（与第 9 轮同组成） | 91 | 18 | 52 | 2 | 0 | 7 | 12 | 0 | 70 | **76.9%** |

- 4 条新增全部可用：`solidot` 7942 字 / 1.9s、`guancha` 7938 字 / 1.5s、
  `thepaper` 785 字 / 0.9s、`python-docs` 3708 字 / 2.0s，全部 `static_http`。
- **本轮最大变化：YouTube 从 `blocked` 翻成 `partial`（7273 字 / 46.5s → 11.8s）**，而且
  单独复测第二次仍然 `partial`（11336 字 / 11.8s）。响应里 `coverage.details.source` 为
  `video_subtitles`、`transcript_source` 为 `manual_subtitles`，说明 yt-dlp 拿到了公开人工字幕，
  不是抽帧兜底。这条能力一直存在，之前失败是本机出口到 YouTube 的通路问题（`challenge_required`），
  所以**不能算本轮新增能力**，只能算同一能力在通路恢复后显形；报告的可用率里它计的是真实读数。
- 其它三条翻转都是一次性的出口抖动，不能当结论：`wikipedia` `partial`(7992) → `unreachable`、
  `guardian` `unreachable` → `partial`(5735)、`gist` `unreachable` → `partial`(7829)。
  同一批 13 条 `unreachable` 基本集中在被墙或被本机出口拦下的站点（bbc/reuters/nytimes/medium/
  reddit/mastodon/bluesky/soundcloud/hn-rss/archive-org），它们的判定取决于出口，不取决于解析能力。
- 延迟：mean 25.1s、p50 4.5s 量级，零超时。

## 第九轮（新增 3 条全部可用，全语料 75.0%，零翻转）

第八轮之后再补 3 条：`ted.com` 演讲页（新转正站点），以及两个真正能读的订阅源
（`ruanyifeng.com/blog/atom.xml`、`ithome.com/rss/`）。语料 90 → 93 行，死链仍 1 条，分母 92。

| 口径 | 分母 | readable | partial | empty | truncated | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 92 | 20 | 49 | 2 | 0 | 8 | 13 | 0 | 69 | **75.0%** |
| 旧 89 行子集（与第 8 轮同组成） | 89 | 18 | 48 | 2 | 0 | 8 | 13 | 0 | 66 | **74.2%** |

- 3 条新增行全部可用：`ted-talk` 502 字 / 1.5s（`readable`，`static_http`）、
  `ruanyf-atom` 436 字 / 2.7s（`readable`，`public_api:feed`）、
  `ithome-rss` 8004 字 / 0.7s（`partial`，`public_api:feed`）。RSS/Atom 通道再次验证可用，
  更新频率天然短，所以停在 `partial` 是正确的，不是读取失败。
- **旧 89 行零翻转**：与上一轮逐行比对，`readable/partial/empty/blocked/unreachable` 五类
  完全一致，说明第 8 轮 74.2% 不是抖动。这也是目前最稳的一轮——没有一条行在两次运行之间换类。
- 本轮同时修掉一个错误归类（不改善判定但影响模型行为）：浏览器导航失败此前落到
  `auth_required`，会让模型对根本没登录框的站点（如 `reddit` 的 `ERR_CONNECTION_CLOSED`）
  去跑 `envoy_auth`；现在报 `network_error`。
- 延迟：mean 23.7s、p50 4.46s、p90 77.23s、max 115.2s，仍然零超时。
- 回归：`pytest tests/cli` → 670 passed / 4 failed（已知 reportlab 缺依赖）/ 1 deselected；
  分类+路由+语料 157 passed；`tests/cli/test_login_wall.py` 13 passed（含新增的导航错误归类）。

