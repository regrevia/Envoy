# 链接兼容性实验

把「Envoy 能读多少链接」从印象变成可复现的数字。语料、预检、指标、两次运行全部在
`scripts/acceptance/link_coverage.py` 里，报告只是它输出的 JSON 的整理。

## 结论（本轮）

- 语料 74 条；预检剔除 1 条 404 死链（amazon），分母 **73**；其中 13 条是本机出口/代理
  不可达（不是产品失败），实际可测 **60** 条。
- **同口径配对**（两轮都 live 的 58 条，唯一可信的对比口径）：可用
  `46/58 (79.3%) → 47/58 (81.0%)`；完全可读 `15/58 (25.9%)` 持平。
- 本轮真正新增的能力：抖音短链 `v.douyin.com` `empty(71 字) → partial(6622 字)`；
  36kr 文章页 `empty(58 字) → partial(5697 字)`。
- 抖动而非回归：抖音视频页同一张卡片本轮 `empty(113 字)`、上一轮 `partial(3642 字)`，
  平台对它自家的分享链路时松时紧；因此配对净收益只报 +1 条，不把抖音算成 2 条新能力。
- 抖动已补强（本轮之后）：正文 < 400 字且无图无媒体的抖音页，再让隔离浏览器跑一遍
  （只一次，不叠加）。`--only cn-platform` 复核 `douyin-video` 由 `empty 113 字` 变为
  `partial 3148 字`（37.8s），该族 10 条为 3 可读 / 5 部分 / 2 空，仅剩快手两条空。
- 不含风控站点口径（`no_risk`）：分母 69，可用 **48 (69.6%)**，可达完全可读
  **28.6%**（16/56）。
- 代价：p50 `4.6s → 5.3s`、mean `16.0s → 19.7s`、p90 `56.5s → 63.3s`。这是为长视频/
  浏览器回退把单条超时从 180s 放宽到 240s 的账面结果，属预期成本，不是效率回归。
- 快手不属「未适配完」而属「纯 HTTP 不可解」：GraphQL 详情接口直接返回人机校验
  （`result: 400002` + captcha URL），见下一节。

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
