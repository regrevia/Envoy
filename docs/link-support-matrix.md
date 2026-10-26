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

已支持项（本轮未改）：小红书（含 `xhslink` 短链，本表刻意跳过）、B 站（含 `b23.tv` / `bili2233.cn`）、知乎、微信公众号文章、YouTube / `youtu.be`、Vimeo、Dailymotion、Instagram、X、`kuaishou.com` 主域。

边界：

- 抖音 `/note/` 保持页面读取，媒体优先只对 `/video/` 生效；`/user/` 等主页同样为页面读取。
- 短链不预解析、不探测，短链域名与长链域名可能落到不同类型的读取路径，以重定向后的 URL 重新分类为准。
- RSS 只按订阅源读取，不把源内音频当作请求目标；需要音频时读取节目页或音频直链。
- `platform_page` 与 `article_page` 都进入隔离浏览器路径，区别仅在语义标注。
