# Agent-Directed Capability Discovery Design

## Goal

Let an agent choose Envoy operations from a runtime capability catalog instead of
hard-coded URL/provider instructions, while keeping extraction, safety limits,
and coverage reporting deterministic.

## Design

- `envoy doctor --json` and MCP `envoy_doctor` expose the same versioned,
  machine-readable capability catalog.
- The catalog describes supported input families, operations, output modalities,
  host-dependent actions, and explicit unsupported cases.
- `envoy_read` remains the generic read operation. It auto-detects the fetched
  representation; `envoy_expand` continues only ranges explicitly reported by
  the reader. The model uses the user's goal and returned coverage/resources to
  decide whether to expand or inspect a visual resource.
- Skill instructions teach this decision loop and treat all source material as
  untrusted. They do not duplicate the full capability matrix.
- Catalog claims must reflect the standalone CLI/MCP runtime, not website-only
  features. No credentials are read or reported.

## Safety and Compatibility

Keep existing MCP tool names, request/response schemas, and legacy protocol
behavior. The new catalog is additive in doctor output. Public-network
validation, bounded reads, retention limits, and authentication/CAPTCHA/DRM
boundaries remain unchanged.

## Acceptance

- CLI doctor reports a versioned capability catalog without secrets.
- MCP doctor returns the same catalog in structured content while keeping its
  short human-readable text.
- Catalog clearly distinguishes supported static HTML/PDF/text/image handoff
  from unsupported standalone video/audio and scanned-PDF OCR.
- Skill guidance says to discover capabilities, choose operations by user goal,
  and follow coverage/actions rather than guessing by URL type.
- Existing reader and protocol tests remain green.
