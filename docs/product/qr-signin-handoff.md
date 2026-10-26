# In-chat sign-in handoff

A link that answers with a risk-control or sign-in page used to end the task:
the reader reported `needs_user_action`, the model relayed a terminal command,
and the human had to leave the conversation, find a shell, and run it. The
screenshot of what the site actually wanted was never shown, so a QR-code login
was impossible to complete from the chat.

`envoy_auth` closes that gap. It opens the sign-in page in a **background**
browser session, saves a picture of it, and returns the picture's path to the
model. The model shows the picture; the user scans it; the same link reads
normally afterwards.

## Flow

1. `envoy_read` on a gated link returns `blocked` with
   `details.needs_user_action.kind = "login_or_verification"`, plus
   `action_id` and `resume_token`.
2. The model calls `envoy_auth` with `action: "open"` and the blocked `url`.
   Envoy creates (or reuses) the auth action, spawns a detached worker
   (`python -m envoy_cli.auth_session --worker`) that launches Playwright
   Chromium headless against the shared profile
   `~/.envoy/browser-profile`, and writes
   `~/.envoy/auth/actions/<action-id>/sign-in.png`.
3. `envoy_auth open` returns `screen_path` after the first frame (≤ 20 s), with
   `status: "waiting"`, `screen_mime: "image/png"`, and an `instruction` that
   names the next call. The image is what the model shows the user.
4. The user scans or confirms. Nothing else is needed from them in the terminal.
5. `envoy_auth` with `action: "status"` reports `signed_in` once the page body is
   non-empty and free of login/captcha hints; the action moves to `verifying`
   and the model re-reads the original link with `envoy_read`.
6. `action: "cancel"` stops the worker (`os.killpg`) and marks the action
   `canceled`.

The browser is not closed between steps, so one scan covers every paused link
for the same site in the same batch. `start()` reuses a live worker instead of
launching a second one, which also avoids two Chromium instances competing for
the same profile.

## Why the picture, not a command

- The host decides whether an image can be delivered (`host_capabilities`), but a
  file path is plain text, so every host can at least show it and open it.
- The QR code itself is time-limited. Returning a path to a live screenshot lets
  the user scan the current code rather than one that expired while a command
  was being typed.
- No cookie forging, no user-agent spoofing: the user's own sign-in is what
  unblocks the page, exactly as the skill already required.

## Evidence

- Live run against `https://zhuanlan.zhihu.com/p/2046163499819317101`:
  the article URL answers with Zhihu's 403 risk-control page
  (`{"error":{"code":40362}}`), so the worker falls back to the site's sign-in
  entry (`_LOGIN_URLS` → `https://www.zhihu.com/signin`), and the saved frame is
  the sign-in dialog with its QR code
  (`~/.envoy/auth/actions/<action-id>/sign-in.png`, 1280×720).
- `envoy auth shot --url <link> --json` returns the same payload outside a host,
  for terminals and scripts.
- Unit tests: `tests/cli/test_auth_session.py` (page-choice rules, stale-worker
  state, first-frame handoff, tool payloads, toolset membership).

## Limits

- The fallback map covers zhihu, xiaohongshu, weibo, douyin; other sites are
  photographed as they are, which is still the page a sign-in needs.
- A site that blocks by IP rather than by identity can stay blocked after
  sign-in; the read then still reports the challenge instead of pretending the
  session fixed it.
- The worker expires after `timeout_seconds` (30–900 s, default 300). After
  expiry `status` reports `not_running` and `open` starts a fresh worker.
- One profile, one browser: concurrent reads that need the browser serialize on
  the profile lock; sequential use, as the tools intend, is unaffected.
