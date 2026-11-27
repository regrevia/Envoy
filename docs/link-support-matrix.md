# 链接路由支持矩阵

`classify_public_url()` 只做静态匹配，不联网解析短链。短链一律标记为页面读取，由隔离浏览器跟随重定向后落到真实页面。

| 平台 | 典型 URL | 改前 | 改后 | 走哪条读取路径 |
| --- | --- | --- | --- | --- |
| 抖音短链 | `https://v.douyin.com/xxxxxx/` | `generic` | `platform_page` | 隔离浏览器跟随重定向，按落地页读取 |
| 抖音视频 | `https://www.douyin.com/video/<id>` | `generic` | `video_page` | 先走媒体抽取（字幕/音轨），失败再回退页面读取 |
| 抖音图文 | `https://www.douyin.com/note/<id>` | `generic` | `platform_page` | 页面读取 + 图集抽取，不进入媒体优先，避免把图片笔记当视频 |
| 微博短链 | `https://t.cn/A6xxxxxx` | `generic` | `platform_page` | 隔离浏览器跟随重定向 |
| 微博视频 | `https://weibo.com/tv/show/<id>` | `generic` | `video_page` | 先走媒体抽取，失败再回退页面读取 |
| 微博博文 | `https://weibo.com/<uid>/<mid>` | `generic` | `platform_page` | 隔离浏览器读取正文 |
| TikTok 短链 | `https://vm.tiktok.com/xxxxxx/` | `generic` | `platform_page` | 隔离浏览器跟随重定向 |
| TikTok 视频 | `https://www.tiktok.com/@user/video/<id>` | `generic` | `video_page` | 先走媒体抽取，失败再回退页面读取 |
| 快手短链 | `https://k.kuaishou.com/short-video/<id>` | `generic` | `platform_page` | 隔离浏览器跟随重定向 |
| 快手视频 | `https://www.kuaishou.com/short-video/<id>` | `video_page` | `video_page` | 媒体抽取优先，失败回退页面读取 |
| 微信视频号 | `https://channels.weixin.qq.com/web/pages/feed?feedId=…` | `generic` | `platform_page` | 隔离浏览器读取；登录限制按需转交人类 |
| 小宇宙播客 | `https://www.xiaoyuzhoufm.com/episode/<id>` | `generic` | `platform_page` | 隔离浏览器读取节目页正文 |
| 播客 RSS | `https://example.org/feed.xml`、`*.rss`、`*.atom` | `generic` | `platform_page` | 读取订阅源文本；音频需由节目页或直链单独读取，不自动下载 |
| 闲鱼主页 | `https://www.goofish.com/personal?userId=<id>` | `generic` | `platform_page` | 隔离浏览器读取主页与在售列表；收藏夹走 `envoy_discover` |
| 闲鱼商品 | `https://www.goofish.com/item?id=<id>` | `generic` | `platform_page` | 页面读取商品详情；风险控制页判为 `source_blocked`，不再当薄内容 |
| 淘宝/天猫短链 | `https://m.tb.cn/h.xxxxxx?tk=…`、`e.tb.cn`、`tb.cn` | `generic` | `platform_page` | 隔离浏览器跟随重定向后按落地页分类 |
| 淘宝商品 | `https://item.taobao.com/item.htm?id=<id>` | `generic` | `platform_page` | 页面读取；落地到登录页转交登录 |
| 天猫商品 | `https://detail.tmall.com/item.htm?id=<id>` | `generic` | `platform_page` | 页面读取 |
| 淘宝收藏夹 | `https://favorites.taobao.com/favorite` | `generic` | `platform_page` | 收藏夹走 `envoy_discover`，未登录返回登录转交而不是空列表 |
| 京东短链 | `https://u.jd.com/xxxxxx`、`3.cn` | `generic` | `platform_page` | 隔离浏览器跟随重定向 |
| 京东商品 | `https://item.jd.com/<sku>.html`、`item.jd.hk` | `generic` | `platform_page` | 页面读取；`pc-frequent-pro` 403 页判为 `source_blocked` |
| 京东店铺/收藏 | `https://mall.jd.com/…`、`https://favorites.jd.com/` | `generic` | `platform_page` | 店铺与收藏夹走 `envoy_discover` |

已支持项（本轮未改）：小红书（含 `xhslink` 短链，本表刻意跳过）、B 站（含 `b23.tv` / `bili2233.cn`）、知乎、微信公众号文章、YouTube / `youtu.be`、Vimeo、Dailymotion、Instagram、X、`kuaishou.com` 主域。

边界：

- 抖音 `/note/` 保持页面读取，媒体优先只对 `/video/` 生效；`/user/` 等主页同样为页面读取。
- 短链不预解析、不探测，短链域名与长链域名可能落到不同类型的读取路径，以重定向后的 URL 重新分类为准。
- RSS 只按订阅源读取，不把源内音频当作请求目标；需要音频时读取节目页或音频直链。
- `platform_page` 与 `article_page` 都进入隔离浏览器路径，区别仅在语义标注。
- 购物站点的四种落地形态都按 `platform_page` 处理：个人主页、收藏夹、商品详情、分享短链。短链不预解析，落地后重新分类；主页与收藏夹再经 `is_collection_url()` 之外的判定进入 `envoy_discover`，商品详情不进。
- 购物站点给静态抓取的永远是应用外壳，正文由浏览器渲染。外壳里出现的风控措辞（`非法访问`、`网络不见了`、`暂时无法展示`、`pc-frequent-pro`）只在这四个域名上算拒绝；同一句 `请稍后重试` 在别处是普通文案。

## 判定与降级

`read_support_verdict(url)` 在 `classify_public_url()` 之上给出静态的三档判定，不联网、不解析短链，只回答「这一条现在交给 Envoy 值不值得」：

- `supported`：本机已实测能读到正文/媒体，或 `docs/acceptance/platform-coverage-matrix-2026-10-21.md` 里有 accepted 记录。包括 B 站（含短链）、抖音视频/图文（含 `v.douyin.com` 短链）、微博博文与 `tv/show`、TikTok（本机网络不可达也算平台支持）、快手、视频号、小宇宙、公众号文章、知乎、36kr、GitHub/gist、Wikipedia、MDN、arXiv，以及 PDF/音视频/RSS 直链。Hook 对这些链接写「以下链接用 envoy_read 读」。
- `unsupported`：已确认匿名访问拿不到内容，调用 Envoy 只是白跑一次。当前只有 Instagram 单条（`/p/`、`/reel/`、`/tv/`）、X/Twitter 单条 status、LinkedIn、抖音直播（`live.douyin.com`、`/live/`）、Telegram 私有频道（`t.me/+…`、`/joinchat/`、`/c/`）。Hook 对这些写「不要调用 envoy 工具」，让模型直接用宿主原生抓取或明说读不到。判定只收有实测或矩阵结论支撑的站点；只是本机网络超时（如 Pinterest、Amazon）不算 unsupported，保持 `unknown`。
- `unknown`：其余全部，含 YouTube、Vimeo、Dailymotion、小红书（本轮未动，不并入判定）以及未收录站点。Hook 维持原文案：先试 `envoy_read`，失败再回退原生抓取。

降级顺序固定：`unsupported` 直接走宿主原生；`unknown` 先 Envoy 后原生；`supported` 先 Envoy。无论哪档，Envoy 返回 `blocked` / `challenge_required`、明确需要登录或验证码时都回退原生并向用户说明原因。同一轮里既有 supported 又有 unsupported 时，Hook 按档分组注入，不把两类混成一句话。

## DSH 侧按 verdict 分组注入

DeepSeek Harness 插件的 `buildRoutingText()`（`integrations/dsh/envoy-dsh/index.js`）与 `hooks.py` 的 `build_prompt_context(host='dsh')` 同源：同一套 `supported` / `unsupported` / `unknown` 三档、同一段注入文案（`以下链接用 envoy_read 读` / `不要调用 envoy 工具` / `先试 envoy_read`），marker `[envoy-link-routing]` 只出现一次；没有公网链接时不产生任何注入。

规则表不写第二份：`integrations/dsh/envoy-dsh/link-verdicts.json` 是由 `scripts/export_link_verdicts.py` 从 `packages/envoy-cli/src/envoy_cli/platforms.py` 生成的静态文件（host 集合、路径正则、文件后缀，稳定排序），JS 只读它做 host/path 匹配。**改 `platforms.py` 的判定表后必须重新生成并校验**：

```bash
uv run --python 3.11 --with-editable packages/envoy-core \
    --with-editable packages/envoy-cli \
    python scripts/export_link_verdicts.py            # 重新生成
uv run --python 3.11 --with-editable packages/envoy-core \
    --with-editable packages/envoy-cli \
    python scripts/export_link_verdicts.py --check    # CI 用：过期则 exit 1 并打印 diff
```

CI 的 `dsh-plugin` job 跑 `--check` 与 `node --test integrations/dsh/envoy-dsh/index.test.js`。JS 测试用显式文件而非 `node --test <目录>`：Node ≥22 的目录形式不会发现 `*.test.js`。

## 实测（2026-11-27）

下表是本机隔离浏览器实读结果，不是推断。短链一列给的是重定向后的落地页。

| 输入 | 落地 | 结果 | 说明 |
| --- | --- | --- | --- |
| `https://www.goofish.com/personal?userId=3604975982` | 同左 | 读到主页正文 | 昵称、城市、粉丝/关注数、在售列表与价格都在正文里 |
| 闲鱼分享文案短链 `https://m.tb.cn/h.jnU2OZ6?tk=IejA66LwN2O` | 上面的主页 | 读到主页正文 | 分享 token 决定落地；裸 user-ID 分享链会落到 `personal?userId=undefined` |
| `https://www.goofish.com/item?id=<随机 id>` | 同左 | `source_blocked` | 外壳只有「网络不见了 / 暂时没有推荐」，没有任何商品字段 |
| `https://item.jd.com/<随机 sku>.html` | `pc-frequent-pro.pf.jd.com/?from=pc_item&reason=403` | `source_blocked` | 403 页写「暂时无法展示该商品的信息，请稍后重试」 |
| 淘宝/天猫商品 | `login.taobao.com/havanaone/login/login.htm` | `auth_required` | 未登录一律跳登录，转交人工登录后重读 |
| 淘宝收藏夹（未登录） | `https://www.taobao.com/` | 读到首页正文 | 未登录不给收藏列表，也不报空列表 |
| 京东短链 | `https://www.jd.com/` | 读到首页正文 | 短链落地后按落地页分类 |

风控页与「读到了空内容」是两件事：外壳有标题、有句子，长度判定不会拦它，所以判定必须落在措辞上，且必须发生在长度阈值之前。
