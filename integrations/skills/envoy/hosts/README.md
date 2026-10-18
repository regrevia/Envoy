# Envoy Host Setup

The bundled MCP configuration assumes the `envoy` command is available on
`PATH`:

```json
{
  "mcpServers": {
    "envoy": {
      "command": "envoy",
      "args": ["mcp"]
    }
  }
}
```

Install the CLI and core packages separately, then verify the runtime:

```bash
envoy doctor --json
```

The host configuration contains no API keys, cookies, browser paths, or model
configuration. The host is responsible for declaring image input capability in
an `envoy_read` request only when it can inspect URI image resources.
