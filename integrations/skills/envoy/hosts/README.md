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
envoy browser setup
envoy doctor --json
```

The host configuration contains no API keys, cookies, browser paths, or model
configuration. The host is responsible for declaring image input capability in
an `envoy_read` request only when it can inspect URI image resources. Envoy's
own isolated Chromium profile is local to the CLI; it does not use or import
the host's browser profile. Public pages run headlessly, and a visible Envoy
window opens only when user login or verification is required. The browser
profile is private to the OS account (POSIX mode `0700`; Windows inherits the
user's home-directory ACL).
