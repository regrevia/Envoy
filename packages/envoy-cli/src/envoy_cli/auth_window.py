"""Open the site's own sign-in page, then check whether it actually helped.

Opening a window is not a success signal. The user may sign in, hit a
captcha, or leave the window on a page that still refuses the media, so the
window only marks the request as "a human looked at it"; a separate
headless check decides whether the original link is readable now.
"""

from __future__ import annotations

import time

from .browser import _page_state, browser_profile_dir
from .media import interprocess_file_lock

_LOGIN_HINTS = (
    "登录",
    "登入",
    "sign in",
    "log in",
    "login",
    "验证",
    "verify",
    "captcha",
    "人机",
    "安全验证",
)


class AuthWindowError(RuntimeError):
    pass


def _lease(timeout_seconds: float):
    lock_path = browser_profile_dir(create=False).parent / "browser-profile.lock"
    return interprocess_file_lock(
        lock_path,
        max_wait_seconds=timeout_seconds,
        busy_message=(
            "Another Envoy browser is using this profile. Close it, or wait for "
            "the run to finish, then try again."
        ),
    )


def open_login_window(
    source_url: str,
    *,
    timeout_seconds: float = 300,
    lease_wait_seconds: float = 15,
) -> dict:
    """Show the real page in a visible window and wait for the human."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise AuthWindowError(
            "A visible sign-in window needs Playwright: run `envoy browser setup`."
        ) from exc

    profile = browser_profile_dir()
    started = time.monotonic()
    timed_out = True
    pages_seen = 0
    final_url = ""
    with _lease(lease_wait_seconds):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                headless=False,
                args=["--disable-dev-shm-usage", "--no-proxy-server", "--new-window"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(source_url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                final_url = page.url
                deadline = started + float(timeout_seconds)
                while time.monotonic() < deadline:
                    pages = list(context.pages)
                    pages_seen = max(pages_seen, len(pages))
                    if not pages:
                        timed_out = False
                        break
                    time.sleep(1.0)
                    try:
                        final_url = page.url
                    except Exception:
                        final_url = ""
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    return {
        "opened": True,
        "timed_out": timed_out,
        "waited_seconds": round(time.monotonic() - started, 1),
        "pages_seen": pages_seen,
        "final_url": final_url,
    }


def verify_source(source_url: str, *, timeout_ms: int = 45000) -> dict:
    """Headless check of the original link; never signs anything in."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - depends on install extras
        return {
            "page_access": False,
            "media_access": False,
            "headless_reusable": False,
            "reason": "playwright_missing",
        }

    profile = browser_profile_dir()
    status = None
    title = ""
    text = ""
    media_urls: list[str] = []
    with _lease(15):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                args=["--disable-dev-shm-usage", "--no-proxy-server"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    response = page.goto(
                        source_url, wait_until="domcontentloaded", timeout=timeout_ms
                    )
                    status = getattr(response, "status", None)
                except Exception:
                    status = None
                try:
                    title, text, _challenge = _page_state(page)
                except Exception:
                    title, text = "", ""
                try:
                    media_urls = page.evaluate(
                        """() => Array.from(new Set([
                            ...Array.from(document.querySelectorAll('video[src]')).map(v => v.src),
                            ...Array.from(document.querySelectorAll('video source[src]')).map(v => v.src),
                        ])).filter(Boolean).slice(0, 20)"""
                    ) or []
                except Exception:
                    media_urls = []
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    lowered = f"{title}\n{text}".lower()
    looks_like_login = any(hint in lowered for hint in _LOGIN_HINTS)
    page_access = bool(status and 200 <= int(status) < 400) and bool(
        text.strip() or media_urls
    )
    if looks_like_login and not media_urls:
        page_access = False
    media_access = bool(media_urls)
    headless_reusable = bool(page_access and not (looks_like_login and not media_access))
    if headless_reusable:
        reason = "readable"
    elif looks_like_login:
        reason = "still_behind_login"
    elif not page_access:
        reason = f"http_{status}" if status else "unreachable"
    else:
        reason = "page_readable_media_missing"
    return {
        "page_access": page_access,
        "media_access": media_access,
        "headless_reusable": headless_reusable,
        "reason": reason,
        "http_status": status,
        "media_urls": len(media_urls),
    }
