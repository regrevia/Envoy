# 小红书（Xiaohongshu）来源可读性实测与排除说明

本文记录为什么 `xiaohongshu` 层不进入固定确认集（`fixed_confirmatory_20`），以及三份可复核的实测证据。
结论先行：**登录态可建立、笔记正文在真机上存在，但 Envoy 的页面文本抽取拿不到笔记正文**。
因此该层没有「可逐字校验的引用」基础，按「不得编造来源与事实」的规则排除，而不是用标题去凑事实。

## 三次实测（2026-10-25）

| # | 输入 | 结果 | 文本 | 快照 sha256 |
|---|---|---|---|---|
| 1 | 分享短链 `https://xhslink.cn/o/1UgQx1OmZ08` | `blocked` | 0 字符 | 无 |
| 2 | 解析后的笔记链接 `.../explore/6acf76b3000000001c02c56d?xsec_token=…&xsec_source=app_share`（无登录态） | `partial` | 1368 字符 | `475372a86fd73a46b5a361985130c43f1c452f957ff14a7ce8049135163ea2ad` |
| 3 | 同一条笔记链接（`ENVOY_BROWSER_PROFILE_DIR=/tmp/envoy-xhs-profile`，已扫码登录） | `partial` | 9668 字符 | `b81908541403a9d0eef1c42dda8c5c5c9d8553b39df86823f50ab470742fec16` |

快照 2、3 的截断点与站点结构一致：标题行 + 顶部导航 + 备案页脚（两段重复）+ 推荐流标题列表。
快照 2 顶部导航出现「登录」项，说明是游客渲染；快照 3 不再出现「登录」，但抽取到的是首页推荐流，标题为「01和银行男友同居vlog｜一天要花多少钱？？」，**不是请求的那篇笔记**。

## 正文确实存在（证明不是内容不存在）

用同一份已登录 Chromium profile 由本机 Playwright 打开快照 2 的那条链接，`#detail-desc` 返回：

```
环境风景无可挑剔，人均GDP亮眼，作为特区，珠海美得无可挑剔。
但好看的皮囊之下，却很难留住普通年轻人。
高端产业多，但岗位供给有限，薪资和房价物价错位，很多年轻人来了又离开。
宜居不等于宜业，这座城市的矛盾点到底在哪？
#广东 #珠海 #城市解读 #大湾区 #城市观察 #知识分享
```

标题：`宜居天花板的珠海，为何留不住年轻人？ - 小红书`；作者：`W学长聊求职`。

这段文本**没有**被用作基准证据：基准的 B 臂必须通过 Envoy 拿到来源，若用旁路抓来的正文做 fact，就变成「评测自身能力的旁路泄漏」。

## 排除规则落在哪里

- `benchmarks/fixed-set-v1/freeze_manifest.py`：`STRATA = ("bilibili", "douyin", "wechat", "cross_platform")`，`DESIGN_PACKAGES = 20`，`DESIGN_UNIQUE_URLS = 40`。
- `benchmarks/paired_experiment_plan.py`：`--confirmatory-fixed` 要求恰好 20 包、每层 5 包、40 个唯一来源；`xiaohongshu` 不在 `_REQUIRED_STRATA`。
- `source-intake.csv` 中不再有 `xiaohongshu` 行；12 份历史抓取（`/tmp/envoy-fixed-set/xhs/final/`）与本次 3 份仅作能力证据留存，不进入 `manifest.json`。

## 要恢复该层，需要满足其一

1. Envoy 能对小红书使用带登录态的持久浏览器 profile，并且抓取目标落在笔记详情浮层（而不是首页推荐流）；或
2. Envoy 支持传入会话 Cookie / 由调用方指定抽取容器（例如笔记正文节点），使正文进入 `heading`/`paragraph` 块。
