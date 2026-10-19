# Envoy Host Setup

See [`../INSTALL.md`](../INSTALL.md) for GitHub Skill and runtime installation.
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

After installing the Release wheel, verify the runtime:

```bash
envoy doctor --json
```

The host configuration contains no API keys, cookies, browser paths, or model
configuration. The host is responsible for declaring image input capability in
an `envoy_read` request only when it can inspect URI image resources.
