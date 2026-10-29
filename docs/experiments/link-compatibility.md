# 链接兼容性实验

把「Envoy 能读多少链接」从印象变成可复现的数字。语料、预检、指标、两次运行全部在
`scripts/acceptance/link_coverage.py` 里，报告只是它输出的 JSON 的整理。

## 结论（最新一轮：第十七轮）

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
| 16 | 109 | 108 | 84 | 77.8% | 25 | 3.45s | 补 youku/peertube 视频行（均可用）；youku 进媒体预算表 |
| 17 | 111 | 110 | 87 | 79.1% | 26 | 3.45s | 补 npr/cnbc 新闻行（均 partial）；apnews 标 unsupported |
| 18 | 111 | 110 | 86 | 78.2% | 26 | 3.74s | 连接级失败不再重试（p90 77.25s→25.81s）；substack 出口抖动掉出可用 |
| 19 | 128 | 113 | 92 | 81.4% | 27 | 4.26s | 非 http(s)/x.com/微信首页判 unsupported（宿主直接走原生）；新增 apple-podcast、spotify-episode、agent-docs 三个可用行 |
| 20 | 133 | 118 | 96 | 81.4% | 29 | 4.65s | 新增 infoq/engadget/theverge/php-manual/sspai-post 五行全部可用；gutenberg、guardian 出口抖动掉出 |
| 21 | 138 | 123 | 99 | 80.5% | 29 | 6.52s | 新增 huxiu/leiphone/geekpark/python-zh/runoob 五行全部可用；mdn、mdn-http、verge-rss 出口抖动掉出（同组成 94/118 = 79.7%，两轮稳定口径 92/118 = 78.0%） |

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
   后续收紧：判定改为一次解析给出 public / policy / dns 三值并缓存，读路径不再解析第二次，
   否则两次解析可能不一致、报错码仍会翻；回归
   `tests/cli/test_url_guard_determinism.py`（5 用例，全程不打真 DNS）。

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

### 口径说明：语料声明数与报告行数不一致的原因

`scripts/acceptance/link_coverage.py` 的语料用 `(family, label)` 声明，报告按 URL 去重
（`select_entries()` 里 `seen_urls`，先出现者胜）。因此语料声明 123 行时，报告行数少于声明数是
**结构性**的，不是丢数据：截至第十五轮有 9 组同 URL 重复（如 `blog/medium` 与
`platforms/medium-post`、`video/youtube` 与 `platforms/youtube-watch`），每组只保留先出现的一条，
另加 risk host 跳过与死链剔除。写数字时一律用 **报告里的行数**作分母，从不用声明数，
两者差额必须能被「重复 + 跳过 + 死链」解释完，否则视为报告损坏。

## 第二十八轮（长 PDF：抓取链路级的分页读取）

之前只测过 `_parse_pdf_source` 这一层的页窗口，没证明「一个 PDF 链接走完整读取链路
时真的按窗口取页」。本轮用 `httpx.MockTransport` 让真实 fetch 路径拿到一份 20 页
合成 PDF（文字层，离线可重建）：

- 第一次读 `scope.page_start=1/page_end=5` → `partial`，只出 1–5 页块，
  `page_count=20`，`not_processed_ranges=[[6,20]]`。
- 第二次读同一链接 `16–20` → `partial`，只出 16–20 页块，
  `not_processed_ranges=[[1,15]]`；两窗口互不重取正文，续读偏移符合预期
  （`unit_checked`，`tests/cli/test_pdf_reader.py`）。
- 同一层再加一条：`content-length` 声明超过 `MAX_PDF_BYTES`（64 MiB）时，
  不读正文直接按体积上限拒绝。
- 说明：`partial` 是「整篇没覆盖完」的诚实标注，不是失败；窗口读完整篇后
  才可能变 `ready`。真实大 PDF 的联网复测仍按既定约束不做。

## 第二十七轮（`application/octet-stream` 里的纯文本）

- 服务器把文本当二进制附件发（`application/octet-stream`，路径又没后缀或后缀不认识）
  时，不再一律拒绝：读最多 1 MiB，按 UTF-8 / GB18030 严格解码，控制字符超过 5%
  或含 NUL 字节就仍判为二进制。命中则按 `text/plain` 走文本块。
- 实测（抓取级）：`drive.google.com/file/d/0B9P1L--7Wd2vU3VUVlFnbTgtS2c/view`
  —— 第二十五轮记的那条「5 字节 `spam\n` + octet-stream、被嗅探拒绝」的样本，
  现在返回 `ready`，10 字正文 `spam`，`mime_type=text/plain`。
  同轮再测 `.tsv` 样本仍 `ready`，无回归。
- `unit_checked`：`tests/cli/test_share_links.py` 两条断言——
  中文 octet-stream 文本读成正文；`\x00\x01\x02binary` 仍是 `unsupported_source`。
- 边界：超过 1 MiB 的二进制附件仍拒绝（错误信息说明「太大，无法按文本读」），
  不做截断后猜测；这条没有放宽安全边界。

## 第二十六轮（制表符表格 .tsv；OneDrive 分享链接）

- `.tsv` 和 `text/tab-separated-values` 归入文本源，按原样保留制表符分列。
  实测：`raw.githubusercontent.com/Giammaria/PublicFiles/…/data/data.tsv`
  → `ready`，1490 字，标题取文件名 `data.tsv`，首行表头与数据行的 `\t` 完整保留。
- OneDrive 分享链接（`1drv.ms/…`、`onedrive.live.com/?resid=…`）补 `download=1`，
  其余参数原样保留（`resid=ABC!123` 不会被重新编码）。`unit_checked`：
  `1drv.ms/u/s!AgsM…?e=abc` → `…?e=abc&download=1`，
  `onedrive.live.com/?resid=ABC!123&cid=ABC&download=0` → `…&download=1`。
- OneDrive 抓取级实测没做成：本机到 `onedrive.live.com` 连接超时
  （`1drv.ms` 的 301 能拿到，跳转目标连不通），属网络环境限制，不是读取缺陷。
- 顺带把 Dropbox 的「补 `dl=1`」改成同样的参数替换逻辑，`dl=0` 会被替换而不是叠加。

## 第二十五轮（共享链接：Drive / Docs / Dropbox 的分享地址）

分享链接（`drive.google.com/file/d/<id>/view`、`docs.google.com/.../d/<id>/edit`）
返回的是 JS 外壳，按原样抓只会读到空页面。这一轮在抓取前把地址换成导出地址：

| 原始链接 | 抓取时用的地址 |
|---|---|
| `drive.google.com/file/d/<id>/view?usp=sharing` | `drive.google.com/uc?export=download&id=<id>` |
| `docs.google.com/document/d/<id>/edit` | `docs.google.com/document/d/<id>/export?format=docx` |
| `docs.google.com/spreadsheets/d/<id>/edit` | `…/export?format=xlsx` |
| `docs.google.com/presentation/d/<id>/edit` | `…/export?format=pptx` |
| `www.dropbox.com/s/…?raw=1` | 同地址改 `dl=1` |

- 单测 5 条：四条地址改写（含「普通链接不动」），一条用 `httpx.MockTransport`
  证明读的是导出地址、docx 正文进了文本块，并且响应里的 `locator.source_url`
  仍是用户给的那条分享链接（`unit_checked`）。
- 分享链接没有文件名，标题不再取路径最后一段（那是 `edit`），回落成域名。
- 抓取级证据只到「公开文件不可得」：试过的公开 Drive 样本
  （`0B9P1L--7Wd2vU3VUVlFnbTgtS2c`）实际是 5 字节 `spam\n` + `application/octet-stream`，
  被内容嗅探按设计拒绝；没有找到一个公开的 Docs/Dropbox 文档链接可用。
- 已知限制：Drive 大文件会先给「病毒扫描确认」HTML 页，那种情况下仍读不到正文；
  私有文件继续走隔离浏览器（带用户自己的登录态）。

## 第二十四轮（表格文档与 RTF：.xlsx / .xlsm / .ods / .rtf）

上一轮把 ZIP 容器文档读成正文，这一轮补两块同样是「点开链接就该有字」的形态：
表格和富文本。仍然只用标准库，安装体积不变。

| 链接 | 实测 |
|---|---|
| `https://go.microsoft.com/fwlink/?LinkID=521962`（Financial Sample.xlsx） | `partial` / 返回 11351 字（全篇 96109 字）/ `document_format: xlsx`，15 列真实财务数据 |
| `https://raw.githubusercontent.com/frictionlessdata/testsuite-data/main/files/other/sample.ods` | `ready` / 123 字 / `document_format: ods`，Sheet1 与 Sheet2 各自成段 |
| `https://raw.githubusercontent.com/bitfocus/rtf2text/master/sample.rtf` | `ready` / 1863 字（全篇 1852 字）/ `reader: document:rtf`，正文可读 |

- `.xlsx/.xlsm`：`xl/sharedStrings.xml` 取字符串池，`workbook.xml` + `workbook.xml.rels`
  按工作簿顺序定位每个 sheet，逐行 `<row>`/`<c>` 还原成「A | B | C」文本；`t="inlineStr"`
  与数值单元格同样处理，找不到 rels 时退回 `xl/worksheets/sheetN.xml` 字典序。
- `.ods`：`content.xml` 里按 `<table:table>`/`<table:table-row>`/`<table:table-cell>`
  取文本，`office:value` 补空单元格，`table:number-columns-repeated` 最多展开 50 列。
- `.rtf`：自带一个控制字解析器（跳过 `fonttbl/colortbl/stylesheet/info/pict` 等目的地组，
  认 `\par \line \tab \cell`、`\'hh`、`\uN` 与转义括号），中文按 `\u` 还原；
  `coverage.details` 标 `reader: document:rtf`。
- 旧的二进制 `.doc/.xls/.ppt` 不再给一句「只认 HTML」：检测到 `application/msword`
  等类型或这些后缀时，直接说明「请另存为 .docx/.xlsx 再读」（`unsupported_source`）。
- 单测 12 条（原 8 条 + xlsx 行、ods 行、rtf 抓取与解析、旧格式提示），全部离线。
  三种格式都有真实公开链接的抓取级证据；RTF 样本里的样式组会残留少量标点噪声
  （`*`、`;;`），正文段落完整，属于该样本源文件本身的写法，不影响阅读。

## 第二十三轮（文档链接：.docx / .pptx / .epub / .odt 直接读成正文）

这一轮补的是链接另一类常见形态：不少站点（学校、政府、出版社、GitHub raw）直接发
ZIP 容器文档，以前 reader 只认 HTML / PDF / 文本，这类链接要么被判 `unsupported_source`，
要么把二进制当文本倒出来。解析只用标准库（`zipfile` + 正则取 XML 文本），安装体积零增加。

| 链接 | 实测 |
|---|---|
| `https://calibre-ebook.com/downloads/demos/demo.docx` | `ready` / 8952 字 / 63.2s（1.3 MB 下载，本机出口偏慢） |
| `https://www.gutenberg.org/ebooks/1342.epub.noimages` | `partial` / 11412 字 / 34.5s，`continuation_available` 可续读 |

- 检测放在 `_fetch_html` 的 content-type 分支里：声明了 Office/EPUB 类型，或 URL 后缀是
  `.docx/.docm/.pptx/.pptm/.epub/.odt` 且实际返回 `application/zip`、`octet-stream`，
  才下载并校验 `PK\x03\x04`；普通 `.zip` 包不会被当成文档。
- 解包有上限：条数 ≤ 4000、解压后总量 ≤ 64 MiB，避免压缩炸弹；没有可提取文本时
  报 `empty_content` 而不是空响应。
- 返回的 `coverage.details` 带 `reader: document:docx|pptx|epub|odt` 与 `document_format`，
  后续可分格式统计；正文仍走原有文本管线，`MAX_TEXT_CHARS` 与分段续读照常生效。
- 语料 `document` 族新增两行（`docx-calibre` / `epub-gutenberg`），整族复跑 7/7 可用：
  docx `partial` 7276 字 78.2s、epub `partial` 7414 字 26.1s（都是本机下载慢、预算内截断，
  非解析失败）。单测 8 条覆盖 docx/pptx/epub/odt、纯 zip 拒绝、无正文报错与抓取层检测。

## 第二十二轮（新增 12 行 6 条可用，GitHub Release 页的误判修掉）

本轮 12 行先实测再进语料，覆盖订阅归档、论坛条目、文章、中文媒体、商店、影视条目、
社区首页、GitHub Release、arXiv PDF 八类页面：

| 行 | 族 | 结果 |
|---|---|---|
| `substack-archive` | blog | 1234 字 / 1.6s（partial） |
| `thepaper-home` | news | 767 字 / 3.7s（partial） |
| `bilibili-read` | video | 4166 字 / 16.0s（partial） |
| `steam-app` | web | 7212 字 / 50.4s（partial） |
| `github-release` | code | 7979 字 / 6.4s（partial） |
| `arxiv-pdf` | document | 7387 字 / 29.0s（partial） |
| `reddit-sub` | forum | unreachable / 25.5s（`network_error`） |
| `hackernews-item` | forum | unreachable / 28.9s（`network_error`，本轮修后见下条） |
| `medium-tag` | blog | unreachable / 25.4s（`network_error`） |
| `quora-question` | forum | blocked / 30.4s（precheck 403） |
| `imdb-title` | web | blocked / 11.4s（precheck 202） |
| `producthunt-home` | web | blocked / 29.1s（precheck 403） |

- **`github-release` 是本轮真正修掉的缺陷**：上一轮这条被判 `blocked / challenge_required`。
  在本机进程内复现后定位到 `_looks_like_access_challenge`——它在整页正文里扫
  「验证码 / 安全验证 / captcha」，而 Release 页的 changelog 正好写了这些词，于是正常页面
  被判成风控页。现在标题命中直接判风控，正文超过 400 字则不看标记；同一 URL 读回
  56153 字符、标题 `Release v0.1.26 · regrevia/Envoy · GitHub`，语料侧从 0 字变成 7979 字。
- 3 条 `blocked` 是站点侧的传输层拦截，不是解析缺陷：precheck 对 imdb 返回 202
  （反爬壳页）、对 producthunt 与 quora 返回 403，reader 报 `challenge_required` 与
  precheck 一致。3 条 `unreachable` 是本机出口问题：同一时刻 `curl` 对
  reddit / hackernews / medium 全部失败（连接被重置），与 reader 的分类一致，不记为缺陷。
- 延迟：mean 21.6s、p50 25.5s、max 50.4s，零超时。
- 不作过度声称：`bilibili-read` 从上一轮 165 字变成 4166 字，原因未定（页面结构或出口
  变化都可能），不计入能力变化。
- 全量复跑（同一语料 133 行，4 并发）：1 行判死（`amazon` 404）后分母 132，可用 105
  （79.5%：`readable` 30 + `partial` 75），`blocked` 11、`unreachable` 16、`failed` 0；
  只算 precheck 可达的分母则是 116 行里 105 行可用（90.5%）。16 条 `unreachable` 全部
  在 precheck 阶段就连接失败（BBC/Reuters/NYT/Guardian/Medium/gist/HN/Reddit/
  Mastodon/Bluesky/TED/SoundCloud/HN RSS/archive.org），是本机出口被拦，不是解析缺陷。
- 全量跑暴露了两处可省的成本，本轮已修并复测：
  - 死链连接预算从「直连 20s + 代理 20s」降到「10s + 6s」：`_fetch_html` 里直连超时
    曾实测整 20.0s 才失败，加代理再 5.3s，一条死链要 30–48s 才报错。
  - 公共端点只回很短的正文时（HN 薄条目约 187 字，低于替换页面的 200 字门槛），
    以前页面不可达就整条 `blocked`；现在这答案留作最后兜底，页面确认不可达时改用
    它并在 `coverage.details` 标 `page_unreachable`。`news.ycombinator.com/item?id=1`
    从 `blocked / network_error` 45.5s 变成 `ready` 17.9s、187 字；同批 `arxiv.org`
    1.5s / 4728 字、`sspai.com` 0.8s / 154 字，未受影响。

## 第十九轮（113 行 / 可用 81.4%，判定前置挡掉注定读不到的链接，audio/web 各补一行）

两件事：

1. 静态判定补严，让宿主根本不调用工具。非 http(s) scheme（`mailto:` / `tel:` /
   `file:` / `ftp:` / `data:` / app intent）判 unsupported：之前 `mailto:` 无 host
   走 `unknown`，`ftp://host/…` 更会掉进文件后缀分支判 supported，两条都是错的。
   `x.com` / `twitter.com` 整体进 `_UNSUPPORTED_HOSTS`（连续五轮 `empty_content`
   0 字，登录态外壳；status 页此前已判 unsupported）。`mp.weixin.qq.com` 首页
   （path 空或 `/`）判 unsupported，`/s/<id>` 文章继续 supported，导出表用 `^/?$`
   路径规则表达。JS 侧补 `parsed.protocol` 守卫，其余全部走导出表。
2. 语料补 3 行，选中原因都是「实测先过、再进语料」：`audio/apple-podcast`
   （11946 字 / 2.7s）、`audio/spotify-episode`（1124 字 / 5.0s）、
   `web/agent-docs`（docs.anthropic.com，3239 字 / 5.0s）；三行本轮全部可用，
   三个 host 也进了 `_SUPPORTED_HOSTS`。

本轮：语料 128 行，实跑 113 行（跳过 12 行风控 host、1 行 dead、2 行 URL 去重），
可用 **92 = 81.4%**（第十八轮 86/110 = 78.2%）；延迟 mean 11.7s / p50 4.26s /
p90 32.98s / max 57.5s（第十八轮 10.6 / 3.74 / 25.81 / 70.7；p90 回升是
unsplash、hn-rss 这一批出口抖动撞上浏览器两次尝试，不是新代码回退）。

另修一处度量缺陷：`classify()` 原先用 200 字硬下限判 usable，`example.com`
（整页 170 字、reader 全量交付）因此被记成 `empty`。现在只要「交付 ≥40 字且
`total_chars` 不超过交付量的 2 倍」（即整页就这么短），就按短页计 usable；
仍保留 `total_chars >= 1000` 判 `truncated` 的分支。本轮据此从 91 修正到 92。

验证：`tests/test_platform_classification.py` 163 个断言、`index.test.js` 9 个断言全过；
`scripts/export_link_verdicts.py --check` 干净；`scripts/dev_test.sh` 仅剩既有的
douyin 相册顺序用例失败（HEAD 上同样失败）。

## 第二十一轮（123 行 / 可用 99 = 80.5%，绝对可用行 +3，比例被出口抖动压了 1.7 个点）

再把一批「本机出口能过、reader 真出正文」的行补进 cn-media / reference 两族：

| 行 | 族 | 探测结果 |
|---|---|---|
| `huxiu-home` | cn-media | 381 字 / 10.7s（static_http，短页整页交付） |
| `leiphone-home` | cn-media | 4646 字 / 3.1s |
| `geekpark-home` | cn-media | 5268 字 / 45.8s（isolated_chromium） |
| `python-zh-asyncio` | reference | 2061 字 / 3.5s（docs.python.org 中文文档） |
| `runoob-python3` | reference | 6224 字 / 1.9s |

`huxiu.com`、`leiphone.com`、`runoob.com` 进 `_SUPPORTED_HOSTS`（`geekpark.net`、
`docs.python.org` 本就在表内），Python 196 断言、JS 9 断言全过。

本轮数字要分三个口径看，因为出口抖动比代码变化大：

| 口径 | 分母 | usable | 可用率 |
|---|---|---|---|
| 全语料（本轮实跑） | 123 | 99 | **80.5%** |
| 可达口径（剔 unreachable） | 107 | 99 | **92.5%** |
| 同组成 118 行子集（= 第二十轮口径） | 118 | 94 | 79.7% |
| 两轮稳定口径（第 20、21 轮都可用） | 118 | 92 | 78.0% |
| 两轮并集口径（任一轮可用） | 118 | 98 | 83.1% |

- 新增 5 行全部可用（+5）；`mdn`、`mdn-http`、`verge-rss` 三行由可用转
  `network_error`（-3）；`substack`、`gutenberg` 由不可达转 partial（+2）。
  净 +4 可用行、分母 +5，于是比例 81.4% → 80.5% —— 这是出口，不是回退。
- 时延 mean **14.5s** / p50 **6.52s** / p90 **30.15s** / max **104.2s**；p50 从 4.65s
  涨到 6.52s 主要是 cn-media 三行浏览器渲染（geekpark 45.8s）与 mdn/verge 的
  连接超时重试摊进来的。
- 两轮稳定口径 92/118 = 78.0% 才是「代码层面确实读到正文」的下界；并集 83.1%
  是上界。两者都记下来，避免用单轮数字讲趋势。

验证：`scripts/export_link_verdicts.py --check` 干净；`scripts/dev_test.sh` 仅剩既有的
douyin 相册顺序用例失败。

## 第二十轮（118 行 / 可用 96 = 81.4%，news 与 web 两族各补实测可读行）

本轮只做一件正事：从「出口能过、reader 真出正文」的候选里补族。先单条探测再入语料，
五行全部可用；`news` 族长期 0 readable，这轮第一次有了 readable 行。

| 行 | 族 | 探测结果 |
|---|---|---|
| `infoq-home` | cn-media | 10047 字 / 4.8s（static_http） |
| `engadget-home` | news | 557 字 / 11.9s（isolated_chromium，短页整页交付） |
| `theverge-home` | news | 10552 字 / 2.2s（static_http） |
| `php-manual` | reference | 11987 字 / 2.0s（static_http） |
| `sspai-post` | cn-platform | 1870 字 / 0.7s（public_api:sspai，ready） |

同时 `infoq.cn`、`engadget.com`、`php.net`、`theverge.com` 进 `_SUPPORTED_HOSTS`
（`sspai.com` 本就在表内），Python 163 断言与 JS 9 断言各补 4 条，导出表重新导出后
`--check` 干净。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 118 | 29 | 67 | 0 | 7 | 15 | 0 | 96 | **81.4%** |
| 可达口径（剔 unreachable） | 103 | 29 | 67 | 0 | 7 | 0 | 0 | 96 | **93.2%** |
| 同组成 113 行子集（= 第十九轮口径） | 113 | – | – | – | – | 15 | 0 | 91 | **80.5%** |

- 时延：mean **17.97s** / p50 **4.65s** / p90 **41.4s** / max **144.87s**。p90 与 max
  都被这一轮的出口抖动推高：`unsplash` 单行 141.6s 才报 `network_error`，
  `hn-rss`、`36kr`、`wechat-mp-platform` 也在 40–81s。这不是代码回退
  （第十九轮同批 host 的 p50 仍是 4s 级）。
- 组成变化：新增 5 行全部可用（+5）；`gutenberg`、`guardian` 由 partial 掉成
  unreachable（-2）；`tiktok` 由 blocked 转 partial（+1）；`x-profile` 由 blocked
  转 unreachable、`archive-org` 由 unreachable 转 blocked（都仍不可用，只是错误分类
  随出口变化）。净 +4 可用行。
- 族分布：reference 9（2 readable）/ news 13（1 readable，另 4 行本机出口不可达）/
  blog 8（1）/ code 7（2）/ forum 8（3）/ social 4（1）/ video 7（2）/ audio 5（0）/
  image 3（1）/ document 5（2）/ spa 4（2）/ feed 6（4）/ api 1（1）/ web 6（0
  readable，1 blocked）/ platforms 8（1）/ cn-platform 12（5 readable）/ cn-media
  12（1 readable，11 partial —— 这一族几乎全是首页，整页交付但被截断）。
- 口径说明：`reachable_usable_rate` 为 93.2%，与第十九轮可达口径同一算法；两轮
  分母不同（119 → 133 行声明），所以主指标同时给「全语料」与「同组成子集」两列，
  不拿比例变化冒充提升。

验证：`scripts/dev_test.sh` 809 passed / 1 failed（既有 douyin 相册顺序用例，HEAD 上
同样失败）；`scripts/export_link_verdicts.py --check` 干净。

## 第十八轮（110 行 / 可用 78.2%，失败路径快速失败把 p90 从 77.25s 压到 25.81s）

本轮不加语料，只改失败路径：连接级失败（拒绝连接 / 连接超时 / 代理不可用）不再
在同一个出口上重试三次。原先 `_fetch_html_via` 对 `httpx.TransportError` 一视同仁，
3 次尝试 × 20s 连接超时 = 60s 花在同一个不通的出口上，`proxy_plan()` 的第二个出口
再各付一次超时，于是单行 77s。现在这类错误立刻上抛，出口仍按计划逐个尝试。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 110 | 26 | 60 | 1 | 8 | 15 | 0 | 86 | **78.2%** |
| 可达口径（剔 unreachable） | 95 | 26 | 60 | 1 | 8 | 0 | 0 | 86 | **90.5%** |
| 同组成 108 行子集（= 第十七轮口径） | 108 | 26 | 58 | 1 | 8 | 15 | 0 | 84 | **77.8%** |

- 时延：mean **10.6s**（上轮 15.8s）/ p50 3.74s / p90 **25.81s**（上轮 77.25s）/
  max **70.7s**（上轮 108.9s）。单点复测 `reuters.com/technology/`：77.4s → 25.9s，
  错误码与结论不变（`network_error`）。
- 可用行 87 → 86 记作出口抖动，不是本轮改动的效果：`substack` 上轮 partial
  2405 字（3.0s），本轮 blocked/network_error（44.3s），是本轮唯一一条
  可用 → 不可用的翻转；`unsplash` 仅在 blocked 内部换错误码
  （challenge_required → network_error，67.1s → 52.0s）。`substack` 那 44.3s 里
  静态抓取先失败、再走浏览器，与本轮「连接级失败不重试」路径不同，是出口本身
  这一小时不可用。
- 语义边界（写进测试 `tests/cli/test_fetch_fast_fail.py`）：只有「一个字节都没拿到」
  的连接级错误跳过重试；`ReadTimeout` 这类读到一半的失败仍保留 3 次尝试；
  `proxy_plan()` 里的每个出口各试一次，不会因为第一个出口失败就放弃第二个。

## 第十七轮（110 行 / 可用 79.1%，新闻族补 npr 与 cnbc）

本轮加 2 条新闻行（此前 news 族 0 readable）：`npr` 与 `cnbc`；同时把
`apnews.com` 标为 unsupported（实测 challenge_required，宿主侧应直接走原生而不是调工具）。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 110 | 26 | 61 | 1 | 9 | 13 | 0 | 87 | **79.1%** |
| 可达口径（剔 unreachable） | 97 | 26 | 61 | 1 | 9 | 0 | 0 | 87 | **89.7%** |
| 同组成 108 行子集（= 第十六轮口径） | 108 | 26 | 59 | 1 | 7 | 13 | 0 | 85 | **78.7%** |

- 两条新行均 partial：`npr` 5253 字 / 20.3s、`cnbc` 4034 字 / 3.2s。
  news 族 11 行从「0 readable + 5 partial + 1 blocked + 3 unreachable」变成
  「0 readable + 7 partial + 1 blocked + 3 unreachable」，可用行 4 → 6。
- `apnews.com` 进入 `_UNSUPPORTED_HOSTS`（与 `linkedin.com` 同列）：
  实测每次都是 challenge_required，把它留在 supported 之外能让宿主侧
  「先判 verdict 再决定要不要调工具」，省掉一次注定失败的调用。
- 抖动：`ted-talk` unreachable → readable 502 字（前一轮是出口问题）。
- 时延：mean 15.8s / p50 3.45s / p90 77.25s / max 108.9s。

## 第十六轮（108 行 / 可用 77.8%，视频族补 youku 与 peertube）

本轮加 2 条视频行：`youku`（国内长视频）与 `peertube`（联邦视频，走 `public_api:feed_alternate`）。
报告 109 行声明、死链 1、分母 108。

| 口径 | 分母 | readable | partial | empty | blocked | unreachable | failed | usable | 可用率 |
|---|---|---|---|---|---|---|---|---|---|
| 全语料 | 108 | 25 | 59 | 1 | 9 | 14 | 0 | 84 | **77.8%** |
| 可达口径（剔 unreachable） | 94 | 25 | 59 | 1 | 9 | 0 | 0 | 84 | **89.4%** |
| 同组成 106 行子集（= 第十五轮口径） | 106 | 24 | 58 | 1 | 8 | 14 | 0 | 82 | **77.4%** |

- 2 条新行全部可用：`peertube` readable 2918 字 / 2.6s，`youku` partial 1065 字 / 2.2s。
  视频族 5 行 → 7 行，且 `youku.com` / `v.youku.com` 同时进入媒体预算表
  （`_MEDIA_SOURCE_HOSTS`），长视频走 300s 预算而不是通用 180s。
- 抖动：`wikipedia` 回到 partial 7992 字（上一轮是出口问题），
  `ted-talk` readable 502 → unreachable、`tiktok` partial 517 → blocked，
  两条都是同一出口/反爬波动，复跑口径下不算能力变化。
- 时延：mean 19.6s / p50 3.45s / p90 77.22s / max 102.9s。

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


## 第二十九轮（凭据转交：当轮实测记录）

目标里把「凭据/登录快速转交」列为核心项，本轮在真实进程里重测一遍，确认四条性质：

- 默认关闭：`tools_for_toolset("core")` = `[envoy_read, envoy_expand, envoy_search, envoy_observe]`，
  不含 `envoy_auth`；`ENVOY_CREDENTIAL_HANDOFF=1` 后同一调用多出 `envoy_auth`；
  写进 `$ENVOY_CONFIG_DIR/config.json` 的 `credential_handoff: true` 同样生效。
- 存储只在本机：写入后文件 `/tmp/…/.envoy/imported-cookies.json` 权限 `0600`，
  所在目录 `0700`（`mkstemp` + `os.replace` 原子替换）。
- 回报不含值：`_save_cookies` 的返回值只有 `{source, imported, domains, path, cookies: [名字]}`，
  实测把 `SECRET-VALUE-123` 放进 jar，序列化后的回报里搜不到该串。
- 读取链路真的用上：同一 URL、同一份存储，开关关闭时请求头无 `Cookie`，
  开关打开时请求头出现 `sid=SECRET-VALUE-123`；cookie 不进入读结果的 blocks/coverage。

- 探针隔离注意：`cookies.cookie_store_path()` 走 `envoy_home()` = `~/.envoy`，
  **不受 `ENVOY_CONFIG_DIR` 影响**；离线探针要覆盖 `HOME`（在导入 envoy 之前设），
  否则会写进真实用户目录。
- `unit_checked`：`tests/cli/test_credential_handoff.py` 已有 14 条覆盖上述四点（含
  「禁用时不附带 cookie」「启用时静态抓取带头」「值不进读结果」）。

## 第三十轮（Apple iWork 与 .xlsb：拒得明白）

`.pages` / `.numbers` / `.key` 是 ZIP 容器但不是 OOXML，之前会掉进「声明了 ZIP 文档但
内容不符」的泛化报错；`.xlsb` 是二进制工作簿，之前只得到「只接受 HTML」的含糊回答。
现在两者都在读正文前给出可执行提示：

- `.pages/.numbers/.key` → 提示导出成 `.docx/.xlsx/.pptx` 或 PDF 再读。
  实测（`unit_checked`）：octet-stream + `PK\x03\x04…` 响应指向 `deck.pages`，
  错误信息含 `iWork` 与 `.pptx`。
- `.xlsb` → 提示另存为 `.xlsx`。实测（`unit_checked`）：响应指向 `book.xlsb`，
  错误信息含 `.xlsb` 与 `.xlsx`。
- 未取得真实样本：想用 SheetJS 的公开测试集做联网复测，`test_files` 是 submodule
  指针、`SheetJS/test_files` 递归树里没有 `.xlsb`/`.numbers` 条目，本轮就此停止；
  这两条停留在单测级，不改写文案里的「已实测」措辞。
- 两条测试在 `tests/cli/test_office_documents.py`，与既有 12 条同文件同构
  （`httpx.MockTransport` 驱动 `_fetch_html`）。

## 第三十一轮（B站多P：分P 不再被合并成一个候选）

`canonical_candidate_url` 以前只保留 `/video/BV…` 路径、丢掉所有 query，于是
`?p=2`、`?p=3` 与第一集归一到同一个 URL，`candidate_id` 一样，采集时被当成重复项
去重，一轮下来只能拿到其中一集。

现在：`p` 为正整数且 > 1 时保留为 `?p=N`，其余分享参数（`share_source`、
`spm_id_from` 等）继续丢弃，`p=1`/无 `p`/非法 `p` 都回到不带 query 的第一集。
`candidate_id` 基于归一化 URL，因此同一视频的不同分P得到不同 id、不再互相去重。

- `unit_checked`：`tests/cli/test_collection_urls.py` 新增 7 条（分P保留、p=1 等价、
  非法 p 忽略、两P两id、分享参数不影响 id、手机域与主站同P一致、非视频路径仍拒绝）。
- 未跑真实采集：分P 采集会真的走媒体链路，用户不在场时按既定约束不做真下载。

## 第三十二轮（B站合集/系列链接：直接当集合发现）

以前 `space.bilibili.com/<mid>/channel/collectiondetail?sid=…` 与
`seriesdetail?sid=…` 只得到「A public Bilibili space URL is required」——因为
`canonical_profile_url` 只认 `/<mid>` 与 `/<mid>/video`。yt-dlp 2026.10.26 的
bilibili 提取器本身支持这两种 URL（测试样例 sid 57445 / 547718 就来自它），
所以放开即可，集合身份由 `sid` 决定；缺 `sid` 时给出具体提示而不是泛化错误。

实测（真实链接，元数据枚举，`skip_download`，未下载媒体）：

- `collectiondetail?sid=57445` → canonical 保留 `sid=57445`，
  `cursor_mode=yt_dlp_flat_metadata`，`stop_reason=page_limit`，`has_more=true`，
  取回 5 条 BV 链接（如 `https://www.bilibili.com/video/BV1Jp411R7SV`）。
- `seriesdetail?sid=547718&ctype=0` → 去掉 `ctype` 只留 `sid`，同样走
  `yt_dlp_flat_metadata`，取回 3 条。
- `unit_checked`：`tests/cli/test_collection_urls.py` 4 条（合集保留 sid、系列保留 sid、
  缺 sid 的提示、裸 space 链接仍归一为 `/video`）。

## 第三十三轮（模板与宏格式：`.dotx/.dotm/.xltx/.xltm/.potx/.potm/.ppsx/.ppsm`）

这些后缀和内容类型与 `.docx/.xlsx/.pptx` 同一个 ZIP + OOXML 结构，只是多了宏或
模板标记，之前不在 `OFFICE_SUFFIXES`/`OFFICE_CONTENT_TYPES`/后缀映射里，会落到
「不是 Envoy 能读的 ZIP 容器」。现在补进三处并复用同一解析器；宏本身不执行、
`vbaProject.bin` 只当普通条目跳过。

- `unit_checked`（`tests/cli/test_office_documents.py`，新增 9 条）：
  - `.docm/.dotx/.dotm` 含 `vbaProject.bin` → `ready`，正文可见，`document_format=docx`；
  - `.xlsm/.xltm` → `ready`，单元格文本可见，`document_format=xlsx`；
  - `.ppsm/.potx` → `ready`，幻灯片文本可见，`document_format=pptx`；
  - `.dotm` 配 `application/octet-stream` 时仍按后缀选中解析器（不依赖内容类型）。
- 同时补上「超大文档按声明长度拒绝」：`content-length` 超过
  `MAX_TEXT_SOURCE_BYTES` 时直接报 `size limit`，不拉正文（本次为 `unit_checked`）。

## 第三十四轮（集合/播放列表链接：读取前就给出下一步，不渲染整页）

`youtube.com/playlist?list=…`、`space.bilibili.com/<mid>/channel/collectiondetail?sid=…`、
`seriesdetail?sid=…`、`bilibili.com/list/…`、`bilibili.com/medialist/play/…` 以前被当成
普通平台页交给浏览器渲染：YouTube 播放列表只回 JS 外壳（几乎无正文），B站合集页
则白花一次浏览器渲染。现在 `is_collection_url()` 在 DNS/网络之前就判定为「一组」
链接，`envoy_read` 直接返回 `unsupported_source` + 下一步：给单条链接，或用
`envoy_discover` 枚举作者主页/合集。

- `unit_checked`：`tests/cli/test_collection_read_guard.py` 11 条——5 种集合形态识别、
  4 种单条链接不误判、BLOCKED 文案含 `envoy_discover`、`details.reader=collection_guard`、
  且守卫在 fallback 之前（未触发网络回退）。
- 真实链接实测：`envoy read` 读
  `https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI`
  → `result=blocked`、`code=unsupported_source`、`details.reader=collection_guard`，
  立即返回（未渲染页面、未抓取）。
- 边界：`watch?v=…&list=…` 仍按单条视频读（`noplaylist` 已保证只取该条），
  `space.bilibili.com/<mid>/video` 仍是可发现的主页，不误伤。

## 第三十五轮（YouTube 播放列表：从「一组」变成可枚举、只取元数据）

第三十四轮把播放列表链接挡在渲染之前，答案里让用户改用 `envoy_discover`；
但 `discover` 之前只认抖音主页与 B站，YouTube 播放列表并不真的能枚举。
现在 `canonical_profile_url()` 接受 `youtube.com/playlist?list=…`（`si=`/`index=`
等分享与位置参数不影响身份），`canonical_candidate_url()` 认 `watch?v=…`（11 位
id）与 `youtu.be/<id>`，扁平元数据里的条目按其 `ie_key` 还原成 watch 链接。

- `unit_checked`：`tests/cli/test_collect.py` 新增 4 条——播放列表规范化（丢弃 `si`）、
  缺 `list` id 报错、`watch?v=` 不算集合、以及用假 yt-dlp 断言
  `skip_download=True`、`cursor_mode=yt_dlp_flat_metadata`、候选 URL/平台正确。
- 真实链接实测（仅元数据，`skip_download`，未下载任何媒体）：
  `https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI`
  → `platform=youtube`、`cursor_mode=yt_dlp_flat_metadata`、`has_more=true`、
  `stop_reason=page_limit`，前三条为 `watch?v=fOT0BUpITw8` / `NFvDHYMzj9U` / `8c7KOGxeY3w`。
- 全套件：`tests/cli` 962 passed。

## 第三十六轮（Windows 干净机完整安装：没有 Python 也要一次装好）

用户要求「完整安装不依赖外部东西」，于是把 `uv` 也纳入自举：机器上既没 Python 也没
uv 时，`install.ps1` 自己下载 uv 0.13.6、按官方 `.sha256` 校验、解压后用它建 venv。
这条路径在 GitHub 托管干净机（无 Python 的 Windows）上连挂两次，两次都是真实用户会
踩的路径，不是夹具问题：

- `failed|blocked`（0.1.27，run 40800453718，`干净机 / Hosted clean machine`，PowerShell 5.1，
  `scenario=fresh profile=full`）：`FAIL A2 … install.ps1 exited 1`，根错为
  `The term 'D:\a\_temp\envoy-acceptance\tools\uv\0.13.6\uv.exe' is not recognized`。
  日志时序：`01:27:02.364` 开装 → `01:27:02.574` `fetching uv 0.13.6` → 约 21.8 秒后
  （`01:27:24.335`）报找不到该文件。原因是验收把 `ENVOY_HOME` 指到 `D:\a\_temp\envoy-acceptance`，
  而 `$RuntimeHome` 与 `$EnvoyHome` 是同一个目录：uv 缓存刚写进 `tools\uv\`，
  「清掉半成品运行目录」那一步就把整个目录（含刚下好的 uv.exe）删了。
  修法：把清理挪到下载之前（`unit_checked`，`tests/cli/test_installer_ps1_static.py`
  新增断言「清理出现在 `Ensure-Uv` 之前，且只有一处清理」）。
- `failed|blocked`（0.1.28，run 40803073176，同 job 同 profile）：这次 uv 找到了，仍失败：
  `envoy install: failed: …\tools\uv\0.13.6\uv.exe venv --quiet --python 3.11 D:\a\_temp\envoy-acceptance`。
  原因同上一条的反面——uv 缓存放在 venv 目标目录里面，`uv venv` 拒绝在非空目录建环境。
  修法：缓存移出运行目录，新增 `ENVOY_UV_HOME`（默认 `%LOCALAPPDATA%\envoy-dsh\tools\uv`；
  设了 `ENVOY_HOME` 时默认取它的同级 `envoy-uv`，自托管 runner 不会写进用户配置目录），
  验收脚本显式把 `ENVOY_UV_HOME` 指到 `$env:RUNNER_TEMP\envoy-uv`。
  `unit_checked`：`tests/cli/test_installer_ps1_static.py` 19 passed，断言缓存路径来自
  `$Script:UvHome`、`Ensure-Uv` 里不再出现 `$Script:EnvoyHome`。
- 两次都确认了 `install.ps1` 的其它 A 项（A1/A3/A7）正常：PATH 可读、用户 PATH 被正确还原，
  说明问题只在这条自举链上，不是验收夹具。
- `verified`（0.1.29，run 40810394272，同一台托管干净机、`profile=full scenario=fresh`，
  `include_self_hosted=false`）：`干净机 / Hosted clean machine` 成功，PowerShell 5.1 与 7 两段都过。
  日志实测：`03:08:41` 开装 → `03:08:42` `fetching uv 0.13.6 (no Python on this machine)`
  → `03:10:44` `install: runtime 0.1.29, protocol envoy.read/1, browser ready, asr ready`
  （PS 5.1 首装约 123 秒，含 uv 下载）；同一 runner 上 PS 7 复跑 `03:10:48` → `03:11:12`
  只用 24 秒，说明 `ENVOY_UV_HOME`（`$RUNNER_TEMP\envoy-uv`）的缓存被复用、没有二次下载。
  A8 断言全过：`profile=full`、`browser.ready=True`、`asr_model_available=True`。
- 排队事故（同轮修掉）：旧 run 40803073176 的托管作业已失败，但两个自托管作业一直等不到
  runner，整个 run 停在 `queued`；`concurrency.cancel-in-progress` 原为 `false`，
  于是新派发的 40806377769 被压了 20 多分钟。修法：`cancel-in-progress: true`，
  并给 `workflow_dispatch` 加 `include_self_hosted`（默认 `true`，只验托管机时传 `false`），
  自托管作业的 `if` 读这个输入；回归断言见
  `tests/test_windows_acceptance_workflow.py::test_a_dispatch_can_skip_the_self_hosted_jobs`
  （12 passed）。

## 第三十七轮（公共 .docx / .pptx 在线读取：真实文件，不是夹具）

以前 Office 支持只有单测覆盖率（本地造的 zip），没有一条真实公网文件的读数。
这一轮用两个公开的小样本各读一次（均 < 40 KB，下载的是被读文件本身，不是语料集）：

- `https://raw.githubusercontent.com/python-openxml/python-docx/master/tests/test_files/test.docx`
  → `execution_state=COMPLETED`、`result=READY`、2 个块、58 字符，正文为
  `test.docx python-docx was here!` / `python-docx was here too!`。
- `https://raw.githubusercontent.com/scanny/python-pptx/master/tests/test_files/test.pptx`
  → `execution_state=COMPLETED`、`result=READY`、2 个块、55 字符，正文为
  `test.pptx Slide 1` / `Presentation Title TextSubtitle Text`。
- 复现方式：临时 `HOME` + `ENVOY_CONFIG_DIR`（不碰真实 `~/.envoy`），
  `packages/envoy-cli/.venv/bin/python` 直接调 `envoy_cli.reader.read_public_page(ReadRequest(...))`。
- 边界：样本本身很小，只证明「公网 Office 下载 → 解析 → 有正文」这条链通，
  不证明 64 MiB 解压上限、4000 条目上限的在线拒绝路径；那两个上限仍只有
  `unit_checked`（本地夹具），标记 `not_run`。

## 第三十八轮（凭据转交开关：真 CLI + 真 MCP 进程的端到端门禁）

开关本身以前只有单测和 `features.py` 层的断言，没有一条「用户按文档操作 → 宿主派发目录真的变」
的证据。这一轮全部用真进程跑（临时 `HOME` + `ENVOY_CONFIG_DIR`，不碰真实 `~/.envoy`，
全程没有任何真实凭据）：

| 步骤 | 命令 | 实测 |
| --- | --- | --- |
| 默认 | `envoy auth handoff --json` | `enabled=False`、`source=default`、`config_path=<tmp>/config.json` |
| 打开 | `envoy auth handoff on --json` | `enabled=True`、`source=config`、`changed=True` |
| 宿主目录 | `envoy mcp` 读 stdin 的 `tools/list` | 5 个工具，末位 `envoy_auth` |
| 关闭 | `envoy auth handoff off` | `Credential handoff: off (source: config)` |
| 宿主目录 | `envoy mcp` 再读 `tools/list` | 4 个工具，`envoy_auth` 消失 |

- 补充：环境变量覆盖也验了 —— `ENVOY_CREDENTIAL_HANDOFF=1`（不写配置）直接让 `tools/list`
  变成 5 个；不设该变量时为 4 个。默认关闭、两种开启方式、关闭后立刻回收，都由真进程证明。
- 落盘权限：开关写出的 `config.json` 是 `-rw-------`（0600），目录在临时 HOME 下。
- 仍未证明的部分（保持 `not_run`）：用真凭据完成一次「认证页交给用户 → 会话被读取链路复用」的
  全链路；这需要一个真实的登录站点和用户本人操作，自动化里只能用夹具，不能算实测。

## 第三十九轮（集合发现与 B 站：真实公网结果，含两条失败路径）

- `verified`：YouTube 播放列表仍是可用的基线 ——
  `envoy discover --url https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI --limit 3`
  返回 3 条候选（`platform=youtube`、`duration_seconds` 有值、`has_more=true`、
  `next_cursor=4e5ec93734ff4a83a3a3:3`、`stop_reason=page_limit`、`discovered_at=2026-10-29T03:15:02Z`），
  出现在本轮新 cursor 分页语义下。
- `failed|blocked`（真实缺陷暴露面）：B 站 UP 主合集 `https://space.bilibili.com/2` 在无 Cookie、
  未装隔离浏览器的机器上被服务端 412 拦掉：
  `yt-dlp space enumeration unavailable (DownloadError: Request is blocked by server (412))` +
  `isolated-browser discovery failed (BrowserRuntimeUnavailable)`，整体 `result=blocked`、
  `code=source_unavailable`，并明确写了补救动作（`envoy browser setup` 一次）。
  结论（限定范围）：这条失败只证明「没有隔离浏览器运行时」时 B 站合集读不了；
  README 写明正式安装器会一并装好浏览器（`envoy browser setup` 只用于补装/修），
  所以安装版用户的真实行为本轮没有验证到，标记 `not_run`，不能据此说 B 站合集开箱可用
  或不可用。能确定的补救动作只有一个：缺浏览器时先 `envoy browser setup`。
- `failed|blocked`：同站单视频 `https://www.bilibili.com/video/BV1GJ411x7h7` 在未装
  `envoy-cli[media]` 的环境里 `result=blocked`、`errors[0].code=missing_capability`、
  `action_required=true`，消息指明 `install envoy-cli[media] to enable local ASR`。
  即没有字幕轨时它会告诉用户装什么，而不是静默返回空正文——这条是可接受的失败形态。
- 复现环境：临时 `HOME` + `ENVOY_CONFIG_DIR`，`packages/envoy-cli/.venv/bin/envoy`（无 media extra、
  无浏览器运行时），故上述两条失败都是「最小安装」视角下的真实行为。

## 第四十轮（读取耗时基线 + 全量套件）

同一台机器、临时 `HOME`、`packages/envoy-cli/.venv`，每条 URL 连读两次（第二次不是缓存命中，
Envoy 每次都是真抓；两次的差异只能反映网络抖动）：

| 目标 | 第一次 | 第二次 | 正文 |
| --- | --- | --- | --- |
| GitHub raw TSV | 1.24 s `READY` | 3.39 s `READY` | 1498 字符 |
| GitHub raw docx | 4.88 s `READY` | 0.94 s `READY` | 57 字符 |
| Google Drive 公开文本 | 17.02 s `READY` | 13.12 s `READY` | 20 字符 |

- 结论 1（效率）：raw.githubusercontent 这类直连目标在 1–5 秒内出正文，Office 与纯文本同量级；
  同一目标两次差 4 倍（docx 4.88 → 0.94），说明当前方差主要来自网络而不是解析。
- 结论 2（Drive 是慢路径）：`drive.google.com` 一次读取要 13–17 秒，比直连目标慢一个数量级。
  对照实验：在**这台机器**上用 `curl -L` 直接打 Envoy 改写出的同一条
  `https://drive.google.com/uc?export=download&id=…`，两次都在 45 秒超时（`http_code=000`，
  `curl: (28) Connection timed out`）。所以这段延迟主要是上游对这台机器的可达性问题，
  Envoy 反而在 13–17 秒内拿到了 20 字符正文 —— 它是走了自己的取回路径，不是简单直连。
  归因到 Envoy 的解析开销没有依据，标记为「上游慢」。
- 全量回归：`pytest -q tests/cli tests/test_windows_acceptance_workflow.py` = **985 passed in 77.47s**。

## 第四十一轮（能力自述是否诚实：ASR / 模型的真实状态）

目标里「读取效率与稳定性」之外还有一条隐含要求：**不能谎报能力**。这轮在本机（`~/.local/bin/envoy`，
source-checkout 入口，未装 media extra）核对 `envoy doctor --json` 的自述：

- `runtime.asr_installed=false`、`asr_engine=null`、`asr_operational="not_checked"`、
  `asr_next_step="rerun envoy asr install --model <model>; runtime updates may replace injected media dependencies"`
  —— 引擎缺失时它没有声称能转录，还给了下一步动作。`capabilities.inputs.conditional` 里也写清
  `local_video_asr` 的 requires（yt-dlp、FFmpeg/FFprobe、openai-whisper、已备模型）。
- `asr_model_available=false` 而 `asr_model_file_present=true` 看着矛盾，实测解释得通：
  `asr_model_cache_dir()=/Users/apple/.cache/whisper`，`base.pt` 确实在（145262807 字节，
  sha256 `ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e`，即官方 base 权重）；
  而 `~/.envoy/asr.json` 里记的 `sha256=eca4af68…` 与它对不上，所以 `asr_model_verified=false`。
  代码里 `asr_installed` 为假时 `available` 恒假，这个状态本身是**正确**的（引擎都没装，模型可用无意义）；
  一旦装上引擎，`_model_expected_sha256()` 会用 whisper 自带的官方摘要复核，与现有文件匹配后
  `verified` 会自愈，不需要用户删缓存。
- 因此本机**没有**做真实 ASR 转录（装 media extra 属于额外依赖下载，用户离开期间不做），
  该项对 macOS 本机保持 `not_run`；Windows 托管干净机的 `asr_model_available=True` 已由
  run 40810394272 证明（第四十轮前的记录）。

## 第四十二轮（B 站空间：能拿到候选，但标题曾经是错的）

本机（装有 Chromium 的 `~/.local/bin/envoy`）对 `https://space.bilibili.com/2` 连跑两次，结果不同 ——
这说明上一轮记的 `source_unavailable` 是**上游限流**，不是永久不支持：

- 第一次：`yt-dlp` 侧 `[BilibiliSpaceVideo] 2: Request is blocked by server (412)`，
  隔离浏览器快照也没拿到卡片 → `{"result":"blocked", code:"source_unavailable"}`。
- 第二次：同样的命令直接成功，返回 `has_more=true`、`next_cursor=0eda1a9350fe44e98769:5`、
  `stop_reason=page_limit`，候选是真实视频（`BV1ujNV6qEXg`、`BV172421f7Km`、`BV1Sh411N7pH`…）。

**发现并修掉的缺陷**：成功那次候选的 `title` 是 `"167.5万 3992 00:19"` 这种统计条文字，`creator` 空、
`duration_seconds` 为 null。原因是空间卡片对同一个视频有两个 `<a>`（封面一个、标题一个），旧代码
按 id 去重时只留了第一个，把封面那块 `div` 的文字拼成了标题。现在的解析（`_bilibili_candidates`）：
先用封面 `img[alt]`，否则取第一个「数字占比 < 40%」的标签（挡掉统计条/时长），时长从卡片文字里
按 `mm:ss` / `hh:mm:ss` 解析。

真实 HTML（`/tmp/bili_space.html`，408327 字节，现场抓的）复验：

| URL | title | duration_seconds |
| --- | --- | --- |
| `BV1ujNV6qEXg` | 帮不帮？ | 22 |
| `BV172421f7Km` | 极限竞速:地平线 新疆DLC | 299 |
| `BV1Sh411N7pH` | 给B站第一个UP主改造房间是什么体验？「桌面改造计划第二季Vol.1」 | 849 |

**顺带把「需要登录」说清楚**：被 412/352 挡住时，报错不再只说「没发现候选」，而是补一句下一步 ——
开关关着时 `… run \`envoy auth handoff on\` and retry, or import existing cookies with \`envoy auth import\`.`，
开关开着时改成「让用户完成登录页（envoy_auth）后重试」。实测输出：

```
{"result": "blocked", "error": {"code": "source_unavailable", "message":
 "No public profile candidates were discovered in the Bilibili space. This space only answers a
  signed-in session: run `envoy auth handoff on` and retry, or import existing cookies with `envoy auth import`."}}
```

回归：`pytest -q tests/cli`（带 `reportlab`/`yt-dlp`/`pyyaml`）= **979 passed**，
新增 `tests/cli/test_bilibili_candidates.py` 6 个用例（封面 alt 优先、时长解析、无封面兜底、
412 提示的两种开关状态、普通空页面不加提示）。

## 第四十三轮（长 PDF：一次读全文，续读按窗口而不是按页）

本轮的靶子是「长文档」这条链路：500 页的 PDF 既不该让调用方一轮一轮翻，也不该因为预算参数写不进去而
直接失败。用本地生成的 500 页文本 PDF（`/tmp/long2.pdf`，cupsfilter 生成，52890 字符）走真实 MCP 通道实测。

**缺陷 1：调用方把 `max_response_chars` 调到 48k 以上会直接校验失败。**
MCP 的 read 预算里 `max_response_chars` 允许到 1_000_000，但 `max_total_response_chars` 从未被推导，
一直是默认 48_000，于是 `max_total_response_chars must be >= max_response_chars` 报 `-32602`。
修在 `mcp.py::_default_budget_for`：只在调用方**没有**给总预算时，把总预算抬到响应预算同值；
显式给了总预算的不动，小于 48k 的响应预算仍保持 48_000 的旧行为。

**缺陷 2：PDF 续读一次只交一页。** `envoy_expand` 的 PDF 分支原来只取「第一个还没交的页」，
而 `max_expansion_rounds` 默认 3 —— 500 页文档读不到 1/4 就被轮次限制挡住。
现在一次把「还能装进本次响应」的页全部交出（每页仍是独立 `pdf_page` 定位块），
`expansion_rounds` 按**一次回答**计 1，不按页累加。

**缺陷 3：复用已存材料的 PDF 报 `continuation_available=true` 却是 `next_offset=null`**，
调用方无从续读。`_reuse_pdf_material` 现在给出下一未交付页的起始字符偏移。

实测（`~/.local/bin/envoy mcp`，本机，逐条记录）：

| 路径 | 结果 | 页 | 字符 | 用时 |
| --- | --- | --- | --- | --- |
| 一次读全文（只给 `max_response_chars=200000`） | `ready` | 500 / 500（1–500） | 51892 / 52890 | 0.26 s |
| 默认预算读 | `partial` | 1–111 | 11338 | 0.08 s |
| 续读 1 | `partial` | 111–299（189 块） | 15300，`remaining_chars=21142` | 同次 |
| 续读 2 | `partial` | 299–488（190 块） | — | 同次 |
| 续读 3 | `ready` | 488–500（13 块） | — | 同次 |

默认预算下整条链 = 1 次 read + 3 次 expand，共 **0.52 s** 交完 500 页（`expansion_rounds` 用满 3 次，
第 3 次已 `ready`）。旧行为是每次 expand 1 页，同样 500 页要 389 次 expand，且远早于读完就会被轮次挡住。

回归：`pytest -q tests/cli`（带 `reportlab`/`yt-dlp`/`pyyaml`）= **982 passed in 52.43s**，
`ruff check packages/envoy-core/src packages/envoy-cli/src tests/cli` 干净。新增：
`tests/cli/test_pdf_retention_continuation.py`（复用材料给出真实 `next_offset`；一次 expand 交出 20 页窗口）
与 `tests/cli/test_mcp_bridge.py::test_mcp_bridge_lifts_the_response_total_when_a_caller_raises_the_response_budget`。

诚实边界：以上是**本机生成的文本型 PDF**，不代表真实扫描件（无 OCR）；真实长新闻/论文 PDF 的在线抓取
仍是 `not_run`（用户离开期间不做大文件下载）。ASR 在 macOS 本机依旧是 `not_run`。

**发行（0.1.30）**：上述修改随 `c1e1d08` / 发布提交 `4732a52` 打标签 `v0.1.30`，
`Envoy Release` run 40815016762 成功，7 个资产齐全（`envoy_cli-0.1.30-py3-none-any.whl`、
`envoy-dsh-0.1.30.tgz`、`envoy-skill.zip`、`install.ps1`、`install.sh`、`release-manifest.json`、
`SHA256SUMS`）；下载 whl 后用 `SHA256SUMS` 校验为 `OK`。Windows 验收 run 40815166638：
托管干净机 job 成功，两个自托管 job（PowerShell 7 / 5.1，`both` 场景 = 全新装 + 从 0.1.23 升级各一遍
全量档）在 12:30 仍在执行中，结果待定 —— 这一项本轮记为 `not_run`（进行中），不写作成功。

**第四十四轮：语音模型下载卡 60 分钟**。Windows 自托管验收 40815166638 的 PowerShell 7 job
在 03:59:13 启动、04:59:41 被 60 分钟超时杀掉，日志（job 123254416344）显示卡在
`envoy asr install` → `prepare_asr_model` → `_download_whisper_model`：
`Whisper model download checkpoint` 每约 2 分钟前进 16 MiB（16→33→50→67→84 MiB，≈130 KB/s），
145 MB 的 `base` 需要约 18 分钟；下载 04:47 左右才开始（前面装 uv/torch/Playwright 用掉约 47 分钟），
到 60 分钟 job 上限只剩 12 分钟，旧代码没有单次下载时长上限，只能被超时杀掉。
改法：单次下载默认 10 分钟封顶（`ENVOY_ASR_DOWNLOAD_SECONDS` 可调），到点保留 `.part`
并以「重跑即可续传」报错；慢链路每约 8 秒打一行「已下/总量（速率，eta）」；
读块从 1 MiB 降到 256 KiB 让封顶更准。`tests/cli/test_asr_model_download_budget.py`
用本地 HTTP 服务覆盖完成/续传/超时保留断点/默认值/进度输出 5 例；`tests/cli` 全量
987 passed。托管干净机同一运行 03:59:12→04:01:09 成功，说明模型源本身可用，
慢的是那台自托管机的出网。

**发行（0.1.31）**：tag `v0.1.31`，Auto Release 40821504926 success → Envoy Release 40821742445 success，
`gh release view v0.1.31` = 7 个资产（`envoy_cli-0.1.31-py3-none-any.whl` 等）；Windows 托管干净机验收
40821893675 success（05:10:58Z→05:13:34Z，PowerShell 5.1 + 7 两档 fresh 全过，含 A8 完整档 ASR 模型就绪），
自托管双 job 本次按 `include_self_hosted=false` 跳过。
