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

## Expand long text

When a read returns `partial` with `continuation_available: true`, send:

```json
{
  "protocol_version": "envoy.read/1",
  "request_id": "req_unique_02",
  "material_id": "mat_<24-hex-id>",
  "offset": 12000,
  "max_chars": 4000,
  "max_total_response_chars": 48000
}
```

The returned text begins at `offset`; use `next_offset` for the next request.
The local cache follows `retention.derived_ttl_seconds` (capped at seven days)
and 100 materials. The cumulative response character limit is enforced against
the absolute returned range.
