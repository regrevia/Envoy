---
name: envoy
description: >
  Read, understand, compare, or extract material from public URLs with Envoy.
  Use when the user asks what a link contains, asks to inspect an article,
  image post, video, audio, PDF, or web page, or asks to transform linked
  material. Do not trigger for merely editing or quoting a URL.
---

# Envoy Link Reader

Use Envoy when the user's task depends on the contents of a link. Preserve
source URLs and material locations in the answer. Do not claim that a source
was read when the tool returns `blocked`, `partial`, or `needs_host`.

## Read

Build an `envoy.read/1` request and send it through the local launcher:

```bash
printf '%s' '<request-json>' | envoy read --request-json -
```

The request must include:

- `protocol_version: "envoy.read/1"`
- a unique `request_id`
- `source.url`
- the user's actual reading goal in `goal`

Do not put API keys, cookies, browser profile paths, or full chat history in
the request. Envoy does not read credentials for the independent Skill.

## Interpret the response

- `ready`: use the returned blocks and resources for the user's task.
- `partial`: answer from returned material and state what was not covered.
- `needs_host`: perform only the requested host action, then submit the
  observation using the host integration.
- `blocked`: report the structured error and the smallest next action. Do not
  bypass login, CAPTCHA, DRM, or private-network protections.

Prefer text blocks for normal answers. Use an image resource only when the
user's question depends on pixels, layout, a chart, a table, or an interface
state. Keep the original `source_url`, block IDs, and resource IDs available
for citations or follow-up reads.

## Current independent runtime

The standalone runtime currently reads public static HTML without a website
backend or credentials. The Envoy website additionally handles dynamic public
pages, videos, OCR, ASR, browser recovery, long tasks, and ZIP exports.
Do not describe those website-only capabilities as available from this
standalone Skill until the support matrix says so.

Check the installation before a first read:

```bash
envoy doctor --json
```

The launcher writes protocol JSON to stdout. Human-readable diagnostics belong
on stderr; never copy secrets into logs or generated materials.
