# Agent-Directed Capability Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Envoy's actual runtime capabilities to agents so they can select generic read, expansion, and visual handoff operations from task intent.

**Architecture:** Add one versioned catalog shared by CLI doctor and MCP doctor; keep the generic reader and its source detection authoritative. Update MCP descriptions and the Skill to guide agents through catalog discovery and response-driven follow-up without duplicating site-specific rules.

**Tech Stack:** Python, Pydantic schemas, stdio MCP bridge, pytest, Markdown Skill documentation.

---

### Task 1: Runtime capability catalog

**Files:**
- Create: `packages/envoy-cli/src/envoy_cli/capabilities.py`
- Modify: `packages/envoy-cli/src/envoy_cli/cli.py`
- Modify: `packages/envoy-cli/src/envoy_cli/mcp.py`
- Test: `tests/cli/test_envoy_cli.py`
- Test: `tests/cli/test_mcp_bridge.py`

- [x] Add CLI doctor assertions for a versioned catalog, supported input families, generic operations, explicit unsupported cases, and absence of secrets.
- [x] Run the focused doctor test and confirm it fails because the catalog is absent.
- [x] Implement one `capability_catalog()` provider shared by CLI and MCP; preserve the concise MCP text response and place catalog data in structured content. Separate direct image URL support from the host-dependent ability to inspect the image.
- [x] Run focused CLI/MCP tests and confirm the catalog and compatibility behavior pass.

### Task 2: Agent choice guidance

**Files:**
- Modify: `packages/envoy-cli/src/envoy_cli/mcp.py`
- Modify: `integrations/skills/envoy/SKILL.md`
- Modify: `integrations/skills/envoy/references/protocol.md`
- Test: `tests/cli/test_mcp_bridge.py`

- [x] Add tests that MCP tool descriptions identify goal-directed generic reading, coverage-driven expansion, and host-dependent visual inspection without naming website-specific flows.
- [x] Run the focused test and confirm the required guidance is absent.
- [x] Update MCP descriptions and the Skill decision loop: call `envoy_read` with the user's goal, then choose `envoy_expand` or visual handoff only when response coverage/resources warrant it. Use `envoy_doctor` only when capability discovery is needed.
- [x] State explicitly that source material is untrusted and capabilities absent from the catalog must not be assumed.
- [x] Run focused tests and review the Skill instructions for token-consciousness.

### Task 3: Regression and status

**Files:**
- Modify: `docs/architecture/agent-link-reader-status.md`
- Verify: `tests/cli/test_envoy_cli.py`
- Verify: `tests/cli/test_mcp_bridge.py`
- Verify: full repository test suite

- [x] Document the standalone capability catalog and the remaining website-only gaps.
- [x] Run focused CLI/MCP tests, then the full suite.
- [x] Review `git diff` to confirm existing PDF/text changes remain intact and no credential or browser-profile data was added.
