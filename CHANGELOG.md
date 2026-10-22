# Changelog

## 0.1.6 - 2026-10-22

- Adopt Apache-2.0 for original code and documentation; include LICENSE and
  NOTICE in the repository, Skill, and Python distributions.
- Retry recoverable blocked reads with the same operation key after network,
  dependency, login, or verification recovery. Migrate old cached failures;
  preserve completed evidence and visual handoffs without repeating costly work.
- Add experimental Xiaohongshu image-note/share routing with source-bound album
  extraction, ordering, and explicit unknown-total coverage.
- Expand common platform URL routing and headless fallback. Routing support
  does not imply real-world platform acceptance or bypass site restrictions.
- Return structured HTTP 404 diagnostics without assuming deletion or blocking.
  Only configured/user-approved trusted proxies may be used.
- Put Agent installation first, clarify separate Skill/runtime/MCP steps, and
  add redaction-aware feedback forms.

### Known Boundaries

- Whisper and FFmpeg are optional; absent ASR or sparse frames are not full
  video evidence. No new real-platform acceptance is claimed by this release.
- Douyin discovery is experimental: canonical author URLs, at most 100 saved
  candidates, snapshot cursors, no date-range query, host-accounted batch budget.
- Scan-only PDF OCR is not available in the standalone runtime.
- v1.0 readiness requires repeatable fresh-install and real-link acceptance,
  optional ASR recovery validation, and measured host usage comparisons.
