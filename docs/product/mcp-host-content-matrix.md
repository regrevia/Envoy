# MCP 工具结果：三个宿主实际把什么交给模型

结论先行：**宿主之间不一致，而且方向相反。**

- Claude Code、Codex CLI 在结果里存在 `structuredContent` 时，**丢掉 `content` 的文本块**，只把 `structuredContent` 的 JSON 交给模型。
- DeepSeek Harness（下称 DSH）只把 `content` 的文本交给模型，`structuredContent` **完全不出现**在模型看到的结果里。

所以「正文只放在 `structuredContent`、`content` 只放一行回执」在三个宿主上**不等价**：DSH 上等于模型看不到正文（就是本次要修的那个 bug）；Codex / Claude Code 上恰好相反，模型看得到正文，看不到回执。

## 矩阵

| 宿主 | 版本 | 协商的 MCP 协议版本 | `content` 到模型？ | `structuredContent` 到模型？ | 证据 |
| --- | --- | --- | --- | --- | --- |
| Claude Code | 2.1.197 | `2025-11-25` | 否（有 `structuredContent` 时文本块被丢弃） | 是，序列化成 JSON 文本 | 静态证据：CLI 二进制里的结果转换函数；**未做端到端**（见下方限制） |
| Codex CLI | 0.156.1 | `2025-06-18` | 否 | 是，`{"secret":"STRUCT_MARKER_BETA"}` | 端到端 + rollout 记录 |
| DSH | 桌面版 0.2.1-rc.3（`dsh-mcp-client` 0.0.1） | `2025-11-25` | 是，`CONTENT_MARKER_ALPHA` | 否 | 端到端 headless + 源码 |

## 测试怎么做的

同一个最小 MCP 服务（python 标准库、stdio、单文件），工具名 `probe`，无参数，声明 `outputSchema` 要求 `{secret: string}`，`tools/call` 固定返回：

```json
{"content":[{"type":"text","text":"CONTENT_MARKER_ALPHA"}],
 "structuredContent":{"secret":"STRUCT_MARKER_BETA"},
 "isError":false}
```

服务把收到的每个 JSON-RPC 方法写进自己的日志（不写 stdout）。每个宿主按自己的真实配置格式接入这个服务，然后跑一次 headless 任务：「调用 probe 一次，然后把收到的每个字段原样说出来」。判断标准是模型的回答里出现哪个 marker，以及该宿主自己的会话记录里留下了什么。

## 每个宿主的证据

### DSH —— `structuredContent` 丢失

模型回答（headless 运行的标准输出，节选）：

```
Called `mcp__mockprobe__probe` exactly once, with no arguments.
**(a) `content[0].text`** — exactly:  CONTENT_MARKER_ALPHA
**(b) `structuredContent`** — not present. The result contained only the text above.
```

服务端日志证明同一次调用确实返回了两个字段（`CALL {"name": "probe", "arguments": {}}` 之后按上面的固定 JSON 回复），并且协商信息为：

```
REQ {"method":"initialize","id":0,"protocolVersion":"2025-11-25","clientInfo":{"name":"dsh-mcp-client","version":"0.0.1"}}
```

源码侧对应关系（`@deepseek-ai/dsh-mcp-client` 的 `lib/index.js`）：工具输出 schema 声明了 `{content, structuredContent}`，执行器把 `result.structuredContent` 原样留下，但交给模型的渲染只取文本：

```js
render(_args, value) { return [{ type: "text", text: extractText(value.content, rawName) }] }
```

即 `structuredContent` 只活在 JSON-RPC 结果和工具返回值里，从不进入模型可见的消息。

### Codex CLI —— `content` 文本丢失

模型回答（`codex exec`，节选）：

```
`content[0].text`: not received.
`structuredContent` was present: `{"secret":"STRUCT_MARKER_BETA"}`.
```

Codex 自己的 rollout 记录里，同一调用的**原始结果两个字段都在**，说明丢文本发生在「结果 → 模型」这一步，不是服务端或传输层：

```json
{"type":"McpToolCall","server":"mockprobe","tool":"probe",
 "result":{"content":[{"type":"text","text":"CONTENT_MARKER_ALPHA"}],
           "structuredContent":{"secret":"STRUCT_MARKER_BETA"},"isError":false}}
```

协商信息：

```
REQ {"method":"initialize","id":0,"protocolVersion":"2025-06-18","clientInfo":{"name":"codex-mcp-client","version":"0.156.1"}}
```

### Claude Code —— 静态证据，未端到端

**限制先说清**：这台机器上 Claude Code 连的是内网网关（`ANTHROPIC_BASE_URL` 指向 LiteLLM 风格代理），每一次模型请求都被网关以同一个 400 拒绝，与提示词长短无关（最小的 `say OK` 也一样）：

```
API Error: 400 ... "This model's maximum context length is 262144 tokens.
However, you requested 0 output tokens and your prompt contains at least 262145 input tokens"
```

换成网关自带的另一条路由同样 400，因此**无法观测 Claude Code 的模型回答**。但它与 mock 服务的 MCP 握手是成功的，协商结果为：

```
REQ {"method":"initialize","id":0,"protocolVersion":"2025-11-25","clientInfo":{"name":"claude-code","version":"2.1.197"}}
```

于是改取本机安装的 CLI 二进制（`/opt/homebrew/Caskroom/claude-code/2.1.197/claude`）里的结果转换逻辑。MCP 结果转成模型可见内容时，分支顺序是：先看 `toolResult`，再看 `structuredContent`，最后才是 `content` 数组；命中 `structuredContent` 时，`content` 里的**文本块被丢弃**：

```js
if ("structuredContent" in e && e.structuredContent !== void 0) {
  let s = Pe(e.structuredContent), i = M6t(e.structuredContent);
  // 只有当 content 里存在非 text 块（图片等）时，才把那些块 + structuredContent 的 JSON 一起返回
  return { content: s, type: "structuredContent", schema: i };
}
```

同一二进制里另一处也印证这种「择一表示」：`getFormatDescription` 对 `toolResult` / `structuredContent` / `contentArray` 分别给出 `Plain text` / `JSON` / `JSON array`，并把超大结果落盘。这是**已发布产物里的真实代码**，但严格说仍是静态推断，未在本机跑通端到端。

## 这对 Envoy 意味着什么

1. 「正文只在 `structuredContent`」不满足 MCP 规范对向后兼容的要求。规范原文：*"For backwards compatibility, a tool that returns structured content SHOULD also return the serialized JSON in a TextContent block."*（[MCP 2025-06-18 · Tools · Structured Content](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)）。Envoy 目前只在 `content` 放一行回执，正好踩在这条 SHOULD 的反面。
2. **不能只做「把正文挪进 `content`」这一半**。Codex 和 Claude Code 在 `structuredContent` 存在时会优先用它、丢弃文本块；如果 Envoy 继续返回 `structuredContent`（哪怕里面只剩回执），这两家会重新只看到回执。可选做法，按稳妥程度排序：
   - 不返回 `structuredContent`（工具不声明 `outputSchema`），正文全部放 `content` 文本 —— 三个宿主都能看到正文；
   - 或者保留 `structuredContent`，但其中也放完整正文（Codex / Claude Code 走 JSON，DSH 走文本，两边都完整）；
   - 只在 `structuredContent` 放少量结构化摘要 + 把同一份摘要写进 `content` 文本作为回执，是可以的，但正文必须同时出现在 `content` 文本里。
3. DSH 端的 `render` 只取 `content` 文本，是宿主行为，不是 Envoy 能改的；Envoy 侧只能靠把正文放进 `content` 文本来兼容。

## 复现材料

- mock 服务：`/tmp/mcp_mock/server.py`（stdio，日志写 `server.log`）
- DSH 接入：`/tmp/dsh_mcp_probe.yml`，用 `insert` 挂 `@deepseek-ai/dsh-mcp-client`，`serverName: mockprobe`，工具名因此是 `mcp__mockprobe__probe`；运行 `dsh --profile headless --patch /tmp/dsh_mcp_probe.yml "<prompt>"`（headless profile 需要 `COMPANY_LLM_TOKEN`，从 `~/.dsh/.credentials.yaml` 取）。
- Codex 接入：`~/.codex/config.toml` 的 `[mcp_servers.mockprobe]`；运行 `codex exec --dangerously-bypass-approvals-and-sandbox "<prompt>"`（默认审批策略下 MCP 工具调用会被 `approval policy is never` 挡掉）。
- Claude Code 接入：`claude mcp add --transport stdio -s user mockprobe -- python3 /tmp/mcp_mock/server.py`。

## Envoy 0.1.11 的处理

采用上面第 2 条做法（保留 `structuredContent`，其中同样放完整载荷），因为 Codex 与
Claude Code 只读这一槽位：

- `content` 文本由 `_tool_content_text` 按工具渲染，自带块正文、搜索命中、`material_id`、
  `source_revision`、资源 id，以及下一步该调用的工具（`envoy_expand` / `envoy_observe`
  的完整参数形状）；legacy 协议仍返回完整 JSON。
- `structuredContent` 是完整 payload（`_read_payload` 只做 `exclude_none` /
  `exclude_defaults`，不裁剪块正文）。
- 两侧都由 `tests/cli/test_mcp_content_selfcontained.py` 断言：同一份证据在
  `modern=True` 与 legacy 分支都必须出现，且 `content` 里能直接抄出 `envoy_observe`
  调用。

仍未端到端验证的只有 Claude Code 一条：本机 CLI 的模型网关对每次请求返回 400
（`requested 0 output tokens ... at least 262145 input tokens`），因此它的结论来自
二进制静态证据，不来自一次成功调用。
