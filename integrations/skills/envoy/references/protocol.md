# Envoy Read Protocol

The independent Skill uses `envoy.read/1`.

## Minimal request

```json
{
  "protocol_version": "envoy.read/1",
  "request_id": "req_unique_01",
  "source": {
    "url": "https://example.org/article"
  },
  "goal": "Extract the main claims and supporting details"
}
```

## Output rule

The response is JSON and contains `result`, `execution_state`, `blocks`,
`resources`, `coverage`, and `errors`. The host should use `blocks` first and
only load a `resources` URI when the task requires visual inspection.

`source_revision` identifies the fetched snapshot. A later read may produce a
new revision; do not silently merge blocks from different revisions.
