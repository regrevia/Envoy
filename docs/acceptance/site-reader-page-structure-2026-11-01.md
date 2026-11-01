# 静态页结构化读取（PP）验收方案

状态：自动项已执行，人工项未执行。本文件只描述对外的读取契约与验收方法，不描述实现细节。

日期：2026-11-01

范围：Envoy 静态 HTML 读取路径（`static_http` reader）。目标是把「普通链接读得又慢又不全」补到可核对：标题层级、可导航链接、表格行列、页头元数据成为结构化产物，同时不改变正文为主、不改变已有字段语义。

## Evidence levels

| 等级 | 含义 |
| --- | --- |
| `verified` | 本轮真实执行过（测试或真实链接），结果与预期一致 |
| `unit_checked` | 本轮只跑过离线单测，未在真实链接上观测 |
| `not_run` | 本轮没有执行 |
| `待验证` | 预期存在，但本轮无证据，需要下一轮补 |
| `target` | 验收目标值，不是观测值 |

## 1 验收目标与口径

### 1.1 对外契约变化

静态 HTML 读取成功后，响应在以下位置多了结构信息。字段不存在于旧版本，因此旧读取方不受影响。

1. `blocks` 增加标题块。`id` 为 `blk-heading-NNNN`（从 1 起，按页面出现顺序），`kind` 为 `heading`，正文为该标题文字，`locator.selector` 为 `h1`–`h6`（标题带 `id` 时追加 `#anchor`），`parent_id` 指向最近的上级标题块，页面首个标题指向 `blk-title`。`order` 与出现顺序一致。
2. `blocks` 增加表格块。`id` 为 `blk-table-NNNN`，`kind` 为 `table`，内容为 `columns` / `rows` / `caption`，`locator.selector` 为 `table`，`parent_id` 指向该表格之前最近的标题块，`order` 排在正文块之后继续递增。
3. `coverage.modalities.text.details` 增加九个键：

| 键 | 类型 | 上限 / 说明 |
| --- | --- | --- |
| `headings` | `list[object]` | 每项 `{block_id, level, text, anchor}`，最多 200 项 |
| `headings_truncated` | `bool` | 标题超过 200 条时为 true |
| `page_links` | `list[object]` | 每项 `{text, url, kind}`，最多 200 项 |
| `links_truncated` | `bool` | 链接超过 200 条时为 true |
| `tables` | `list[object]` | 每项 `{block_id, rows, columns, caption, truncated}`，最多 20 张表 |
| `tables_truncated` | `bool` | 表格超过 20 张时为 true |
| `canonical_url` | `string \| null` | 由 `<link rel="canonical">` 解析为绝对 URL |
| `description` | `string \| null` | `<meta name="description">`，空白归一 |
| `og_title` | `string \| null` | `<meta property="og:title">`，空白归一 |

4. 链接只收 `http` / `https`：`javascript:`、`mailto:`、`data:` 等其他协议不进列表；页面内 `#frag` 按页面最终地址解析为绝对地址，`kind` 记为 `anchor`；同源链接为 `internal`，跨源为 `external`；相对地址按最终地址解析；同一 `(text, url)` 只出现一次。列表项里的每个 URL 都经过与既有 locator 相同的敏感参数脱敏。
5. 标题文字不再作为普通段落出现在正文块里。

### 1.2 不变式

- 正文块（`blk-body-0001`）仍是主内容，仍是 Markdown 文本；不出现 `#` 标题标记，不出现原始或绝对 `href`。
- 标题下方的锚文本仍保留在正文中（例如目录列表里和标题同名的链接文字），这是既有行为，不是标题泄漏。
- `order` 唯一且递增；`result` 的 `ready` / `partial` 语义不变，不因为新增结构而把 `ready` 降级。
- 页面没有标题、链接、表格或页头元数据时，`details` 不出现上述九个键，响应体积与旧版本一致（这条由一条断言「序列化后小于 1200 字节」的既有测试守着）。
- 表格同时保留在正文文本里，正文不会因为新增表格块而变短。

### 1.3 判定口径

能由命令判定的，写成可复跑命令并给真实输出；需要人眼判断的（标题层级是否与页面一致、表格视觉对齐），列入人工验收表，不冒充自动结论。未观测的格子写「待验证」，不留空、不留推测值。

## 2 CLI 自动验收（envoy-cli）

目的：确认契约里的每一条都能被离线单测与端到端 CLI 测试判定，且不破坏既有静态页行为。

```bash
cd /Users/apple/Desktop/2026/Envoy
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_page_structure.py -q
```

本轮实测：

```
..........                                                               [100%]
10 passed in 0.74s
```

```bash
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_page_structure.py \
  tests/cli/test_routing_discipline.py tests/cli/test_mcp_bridge.py -q
```

本轮实测：

```
........................................................................ [ 88%]
.........                                                                [100%]
81 passed in 0.88s
```

覆盖到的判定：

| Check | Expected | 证据 |
| --- | --- | --- |
| 标题块 `kind` / `selector` / `parent_id` / `order` | `heading`、`h1`–`h6`、最近上级标题或 `blk-title`、连续递增 | `verified`（单测） |
| 标题文字不再出现在正文 | 正文块不含标题文本 | `verified`（单测；真实页面另见第 7 节） |
| 链接绝对化 / 去重 / 分类 / 仅 http(s) | 相对地址按 base 解析，重复只留一条，`javascript:` 被丢弃 | `verified`（单测） |
| 链接上限 200 与 `links_truncated` | 250 条链接的页面输出 200 条并置 true | `verified`（单测） |
| 标题上限 200 与 `headings_truncated` | 超限时置 true | `verified`（单测） |
| 表格行列 / caption / 父标题 / 行序 | 列名与单元格逐格一致 | `verified`（单测） |
| 表格 50 行 / 20 列截断标记 | 超出部分不出现，`quality.unresolved` 带截断说明 | `verified`（单测） |
| 元数据缺失时为 `null` | 不编造 title 之外的任何页头信息 | `verified`（单测） |
| 无结构页面不动响应形态 | `details` 不新增九个键 | `verified`（单测） |
| 端到端 CLI `read` 走假 HTTP 也能拿到三类结构 | 块类型、`selector`、表格列、coverage 九键齐全 | `verified`（离线 e2e，不联网） |

回归基线见第 7 节。

## 3 会话自动验收（hooks.py）

目的：真实会话里，会话钩子是否会把用户给出的静态链接指向 Envoy 读取，从而真的走到这条读取路径；以及钩子文案里是否还有「直接交给内置抓取」的误导。

```bash
cd /Users/apple/Desktop/2026/Envoy
packages/envoy-cli/.venv/bin/python - <<'PY'
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli import hooks

prompt = "帮我看一下 https://www.iana.org/domains/reserved 这页有哪些子页面"
routing = hooks.inspect_prompt(prompt)
print("urls:", routing.urls, "| opted_out:", routing.opted_out, "| should_route:", routing.should_route)
context = hooks.build_prompt_context(routing.urls, "claude-code", language="zh")
print("context lines:", len(context.splitlines()))
print(context)
PY
```

本轮实测输出（节选）：

```
urls: ('https://www.iana.org/domains/reserved',) | opted_out: False | should_route: True
context lines: 13
[envoy-link-routing]

本轮用户消息包含公网链接：

以下链接先试 envoy_read，失败再用宿主原生抓取：
- https://www.iana.org/domains/reserved

1. 先调用 Envoy MCP 工具 `envoy_read` …
```

| Check | Expected | 证据 |
| --- | --- | --- |
| 含公开静态链接的提问被识别 | `urls` 命中、`should_route` 为 true | `verified`（本轮实跑） |
| 注入文案先指向 `envoy_read` | 首条指令是调用 Envoy 读取，而非内置抓取 | `verified`（本轮实跑） |
| 用户明确不用 Envoy / 内网地址 | 不注入路由指令 | `unit_checked`（既有分支测试，未单独复跑） |
| 真实会话确实按注入指令调用 | 会话内出现 Envoy 读取工具调用且返回结构 | `待验证`（需要真实 Claude Code 会话与已配置的 MCP server） |

## 4 本地模型自动验收（envoy-core + MCP bridge）

目的：确认新增结构经 MCP 层原样到达宿主，宿主不需要额外渲染器；并确认小页面不会被结构键撑爆响应体积。

```bash
cd /Users/apple/Desktop/2026/Envoy
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_mcp_bridge.py -q
```

本轮实测：

```
................................................................         [100%]
64 passed in 0.94s
```

| Check | Expected | 证据 |
| --- | --- | --- |
| `structuredContent` 与 CLI 返回同构 | `result`、`protocol_version`、blocks 透传不改写 | `verified`（单测） |
| 标题 + 一段话的页面体积不膨胀 | 序列化后小于 1200 字节 | `verified`（单测） |
| 新块经 schema 校验 | `MaterialBlock` / `TableContent` / `Quality` 均能构造 | `verified`（单测） |
| 本地模型对 8 个静态链接的真实调用 | 每个链接都返回结构且正文完整 | `待验证`（需要真实本地模型会话；本轮未跑） |

### 4.1 Lead 真实链接复核（2026-11-02）

用 `read_public_page` 直接读真实公网页面（每次独立 `ENVOY_LOCAL_DATA_DIR`，不经宿主导航），确认结构产物不只存在于单测里：

| URL | 结果 | 标题结构 | 表格 / 链接 |
| --- | --- | --- | --- |
| https://www.iana.org/domains/reserved | `ready` 0.85 s，8 块 | `blk-heading-0001` h1，其下 4 个 h2，`parent_id` 均指 h1，`order` 2–5 | 1 张表，`blk-table-0001`，11 行 × 4 列，表头 `Domain / Domain (A-label) / Language / Script`；7 条链接（5 internal、2 external） |
| https://www.iana.org/help/example-domains | `ready` 0.63 s，4 块 | h1 `example-domains` → h2 `further-reading`，`locator.selector` 为 `h1#example-domains` / `h2#further-reading` | 无表；4 条 internal 链接 |
| https://example.com | `ready` 0.74 s，2 块 | 仅 `blk-title`（页面无 `h1`–`h6`） | 0 表 0 链接——服务端 HTML 本身没有 `<a>`，不是漏抓 |

证据等级：`verified`（本轮实跑真实链接，不是夹具）。仍未验证：分页表格、`colspan`/`rowspan` 还原、JS 渲染页与 iframe——见第 6 节。

## 5 人工验收

目的：机器判不了的两件事——标题层级是否与页面视觉层级一致、表格行列是否真的对齐——由人对着页面看一遍。同时给出可比对的硬数字。

对象：路由语料里的 8 个真实静态页（`tests/fixtures/routing/links.json` 的 `simple_static_03`–`simple_static_10`）。选它们是因为它们覆盖了纯文本页、文档页、规范页、带表格页，且都属于 `host_fetch` 路由。

| # | URL | 本轮自动读数（标题/链接/表） | 人工看什么 |
| --- | --- | --- | --- |
| 1 | https://www.iana.org/domains/reserved | 5 / 7 / 1 | 表格行列是否与页面一致；四条二级标题层级是否正确 |
| 2 | https://httpbin.org/html | 1 / 0 / 0 | 单标题页不应产生多余结构 |
| 3 | https://www.w3.org/Provider/Style/URI | 17 / 5 / 0 | 长文页标题层级连续性 |
| 4 | https://docs.python.org/3/glossary.html | 6 / 200（截断）/ 0 | 截断是否只截尾部，链接文字是否可读 |
| 5 | https://www.gnu.org/philosophy/free-sw.html | 21 / 141 / 0 | 目录里的标题同名链接仍在正文，不算重复标题 |
| 6 | https://www.sqlite.org/about.html | 2 / 48 / 0 | 少量标题页不误判段落为标题 |
| 7 | https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview | 19 / 58 / 0 | 多级标题父链是否合理 |
| 8 | https://www.rfc-editor.org/rfc/rfc2606.txt | 0 / 0 / 0 | 纯文本走非 HTML 路径，不应出现结构键 |

人工核对步骤：

1. 在浏览器打开与第 `#` 列同序的页面。
2. 用第 8 节的单条读取命令取回该页响应，展开 `blocks` 与 `coverage.modalities.text.details`。
3. 逐项对照：标题条数与层级、链接条数与文字、表格行列与页面上的一致；不一致就记下差异位置（块 id + 页面上的位置）。
4. 判定：一致记 `verified`，不一致记 `failed` 并写差异；没看的格子记 `not_run`。

人工结果表（本轮未执行，留给下一轮填写）：

| # | 标题层级 | 链接 | 表格 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | 待验证 | 待验证 | 待验证 | 待验证 |
| 2 | 待验证 | 待验证 | 待验证 | 待验证 |
| 3 | 待验证 | 待验证 | 待验证 | 待验证 |
| 4 | 待验证 | 待验证 | 待验证 | 待验证 |
| 5 | 待验证 | 待验证 | 待验证 | 待验证 |
| 6 | 待验证 | 待验证 | 待验证 | 待验证 |
| 7 | 待验证 | 待验证 | 待验证 | 待验证 |
| 8 | 待验证 | 待验证 | 待验证 | 待验证 |

## 6 已知缺口

以下都是本轮**未解决**的边界，读取方不应假定它们可用。

| 缺口 | 现状 | 影响 | 验证方式 |
| --- | --- | --- | --- |
| 客户端渲染（SPA） | 不执行 JavaScript，只读返回的 HTML | 页面靠 JS 填充时正文为空或只有骨架 | 打开一个 SPA 页对比；本轮 `not_run` |
| `iframe` 内内容 | 不递归读取 | 嵌在主页面里的文档缺失 | `not_run` |
| Shadow DOM | 不穿透 | Web Component 内的标题/正文缺失 | `not_run` |
| 长页分页 | 正文仍受字符预算约束，一次只给一段；结构信息只覆盖已解析的整页 | 超长页的后续正文需继续读取，但结构块不随分页重复 | 真实长页跑两次，比对 `next_offset` |
| PDF / 音视频 / GitHub 专用路径 | 不在本次改动内，行为未变 | 这些来源不产生本文的九个键 | `unit_checked`（相关既有测试仍绿） |
| 标题文字从存储正文中移出 | 标题不再出现在可检索的正文文本里 | 依赖 `envoy_search` 搜索标题原句时命中面变窄，可由标题块与 `details.headings` 补偿 | `unit_checked` |
| 表格同时保留在正文 | 有意为之，保证正文可读性 | 表格内容在正文与表格块中各出现一次，token 略增 | 对比同一页正文长度 |
| 链接只收 http(s) | 其余协议丢弃 | 邮件链接、脚本链接不进列表 | `unit_checked`（单测覆盖） |
| 同一会话重复读同一页面 | 既有行为：第二次返回空正文并标记 `partial` | 不是本次引入；验收时需隔离本地数据目录，否则会误判为正文丢失 | 第 8 节命令已带隔离环境变量 |
| 图片内容 | 只识别图片附带的文字 | 纯图片页仍无正文 | `not_run` |

## 7 基线记录

### 7.1 测试基线

环境：macOS，`packages/envoy-cli/.venv`，离线单测（e2e 用假 HTTP client，不联网）。

| 套件 | 改动前 | 改动后 | 结论 |
| --- | --- | --- | --- |
| `tests/cli/test_page_structure.py` | 不存在 | 10 passed | `verified` |
| `tests/cli/test_mcp_bridge.py` | 64 passed | 64 passed | `verified`（未回归） |
| `tests/cli` 全量 | 1102 passed | 1119 passed | `verified`（EXIT 0，61.33s） |

全量命令与实测尾部：

```bash
packages/envoy-cli/.venv/bin/python -m pytest tests/cli -q
```

```
1119 passed in 61.33s (0:01:01)
```

改动后的 1119 里包含本次新增的 10 条；其余增量来自同期其他改动，不属于本次范围。

注意：仓库根 `tests/` 全量收集阶段有两个既有失败（缺 `yaml`、import 文件重名），与本次无关，复跑时按第 8 节 `--ignore` 处理。

### 7.2 真实链接基线

第一次读取的真实结果（隔离本地数据目录，每个 URL 只读一次；`goal` 统一为「list the page structure: headings, links, tables」）。

| URL | result | reader | 块数（标题/表） | 标题 | 链接 | 表 | 元数据（canonical/desc/og） | 正文长度 | 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| https://www.iana.org/domains/reserved | ready | static_http | 8（6/1） | 5 | 7 | 1 | null / 无 / null | 1839 | 0.80 |
| https://httpbin.org/html | ready | static_http | 3（2/0） | 1 | 0 | 0 | null / 无 / null | 3566 | 0.72 |
| https://www.w3.org/Provider/Style/URI | partial | static_http | 21（18/0） | 17 | 5 | 0 | null / 无 / null | 11877 | 0.66 |
| https://docs.python.org/3/glossary.html | partial | static_http | 9（7/0） | 6 | 200（截断） | 0 | 有 / 有 / Glossary | 11923 | 2.39 |
| https://www.gnu.org/philosophy/free-sw.html | partial | static_http | 27（22/0） | 21 | 141 | 0 | null / 有 / null | 11878 | 9.46 |
| https://www.sqlite.org/about.html | ready | static_http | 5（3/0） | 2 | 48 | 0 | null / 无 / null | 4214 | 1.43 |
| https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview | partial | static_http | 26（20/0） | 19 | 58 | 0 | 有 / 有 / Overview of HTTP - HTTP \| MDN | 11462 | 3.03 |
| https://www.rfc-editor.org/rfc/rfc2606.txt | ready | 纯文本路径 | 2（1/0） | 0 | 0 | 0 | null / 无 / null | 7919 | 0.88 |

由上表得到的观测结论：

- 8 个页面全部 `exit 0`，没有出现 `blocked` 或异常；正文块长度 1839–11923 字符，与页面体量相符。
- 标题、链接、表格三类结构在 7 个 HTML 页上都按契约给出；纯文本页（第 8 行）正确地不产生任何结构键。
- `verified`：8 个页面的正文块都不含 `<a` 或 `href=`；7 个 HTML 页的正文块都不含 `#` 标题标记。
- 截断只在真实需要时出现（第 4 行，链接 200 条上限），其余为 `false`。
- 标题与正文同句的两处（第 5、7 行）经核对是页面自身的目录链接文字与正文句子，不是标题被重复写进正文；标题元素文本没有在正文里重复出现。
- `partial` 的四例都是正文超出单次字符预算，属既有分页语义，不是结构新增导致。

## 8 复跑方式

前置：在仓库根执行；Python 用 `packages/envoy-cli/.venv/bin/python`；不要给 pytest 传 `--timeout`（环境未装该插件）。

```bash
cd /Users/apple/Desktop/2026/Envoy

# 1. 本次契约的单测
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_page_structure.py -q

# 2. 一起跑：契约 + 路由纪律 + MCP 透传
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_page_structure.py \
  tests/cli/test_routing_discipline.py tests/cli/test_mcp_bridge.py -q

# 3. envoy-cli 全量回归
packages/envoy-cli/.venv/bin/python -m pytest tests/cli -q
```

真实链接读取（单条）：

```bash
cd /Users/apple/Desktop/2026/Envoy
ENVOY_LOCAL_DATA_DIR=/tmp/envoy-accept-$(date +%s) \
packages/envoy-cli/.venv/bin/python - <<'PY'
import contextlib, io, json, sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli import cli

url = "https://www.iana.org/domains/reserved"
request = {"protocol_version": "envoy.read/1", "request_id": "accept-1",
           "source": {"url": url}, "goal": "list the page structure: headings, links, tables"}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))
out = json.loads(buf.getvalue())
print("exit:", code, "result:", out.get("result"))
print("blocks:", [(b["id"], b["kind"], b["order"], b["locator"].get("selector")) for b in out["blocks"]])
print("details:", json.dumps(out["coverage"]["modalities"]["text"]["details"], ensure_ascii=False, indent=2))
PY
```

复跑注意：

- 必须给每次验收一个干净的 `ENVOY_LOCAL_DATA_DIR`。同一会话重复读同一页面会命中「已返回」逻辑，得到空正文 + `partial`，看起来像回归，实际是既有去重行为。
- 第 7.2 节的秒数是单次冷读的观测值，网络不同会变；只用于判断量级（1 秒级），不作为性能门槛。
- 仓库根 `tests/` 全量如需运行，先排除两个既有失败项：`--ignore=tests/test_dsh_plugin.py --ignore=tests/test_task_dispatch_flow.py`。
- 人工验收结果回来后，把第 5 节表里的「待验证」替换成实测结论，并补一行执行日期与执行人。
