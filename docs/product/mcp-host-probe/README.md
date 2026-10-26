# 抓宿主真实发给模型的工具结果

用途：判断某个宿主到底把 MCP 结果里的哪个字段交给模型。做法是保留真实客户端和真实
MCP 服务，只把模型端点换成本地假端点，并把收到的请求体写盘。

## Claude Code

```bash
python3 docs/product/mcp-host-probe/anthropic_stub.py 8787 &
mkdir -p /tmp/claude_mock && : > /tmp/claude_mock/requests.jsonl
claude --settings docs/product/mcp-host-probe/settings.example.json \
  -p "Call the envoy_read tool once on example.com" --dangerously-skip-permissions
```

第二条请求里的 `tool_result` 就是模型看到的原样内容。假端点第一次回 `tool_use`
（工具名从请求的 `tools` 里挑以 `envoy_read` 结尾的那个），之后再回纯文本。

## DSH

`dsh` 的 MCP 客户端只把 `content` 文本放进模型上下文，用
`envoy mcp` 的标准输入输出直接驱动即可，不需要假端点：manifest 里发 legacy
`initialize`，每次 `tools/call` 带
`_meta["io.modelcontextprotocol/protocolVersion"]`。

## Codex CLI

`codex exec --dangerously-bypass-approvals-and-sandbox "<prompt>"`，看它复述的正文；
`~/.codex/sessions/**/rollout-*.jsonl` 里也会留下工具结果原文。
