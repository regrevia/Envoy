"""Open the site's own sign-in page, then check whether it actually helped.

Opening a window is not a success signal. The user may sign in, hit a
captcha, or leave the window on a page that still refuses the media, so the
window only marks the request as "a human looked at it"; a separate
headless check decides whether the original link is readable now.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from .browser import _EGRESS_CONTROL_MARKERS, _page_state, browser_profile_dir
from .media import interprocess_file_lock

# A site only sets these once a person is actually signed in; their presence in
# the window's profile is harder evidence than any wording on the page.
_SIGNED_IN_COOKIES = {
    "douyin.com": ("sessionid", "sessionid_ss", "sid_tt"),
    "xiaohongshu.com": ("web_session",),
    "bilibili.com": ("SESSDATA",),
    "zhihu.com": ("z_c0",),
    "weibo.com": ("SUB",),
}

# How long a captured sign-in is kept in the profile. Douyin and Xiaohongshu
# set their session cookies without an expiry, which Chromium never writes to
# disk; re-adding them with one keeps the sign-in for the headless runs after
# the window is gone.
_SIGNED_IN_COOKIE_DAYS = 30

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


def _signed_in_cookie_names(host: str) -> tuple[str, ...]:
    lowered = (host or "").strip().lower()
    if not lowered:
        return ()
    for domain, names in _SIGNED_IN_COOKIES.items():
        if lowered == domain or lowered.endswith("." + domain):
            return names
    return ()


def _domain_covers(cookie_domain: str, host: str) -> bool:
    domain = (cookie_domain or "").strip().lstrip(".").lower()
    lowered = (host or "").strip().lower()
    if not domain or not lowered:
        return False
    return lowered == domain or lowered.endswith("." + domain)


def _persist_session_cookies(context, host: str) -> int:
    """Give the host's session-only cookies an expiry so they survive the close."""

    try:
        cookies = context.cookies()
    except Exception:
        return 0
    expires = int(time.time()) + _SIGNED_IN_COOKIE_DAYS * 24 * 60 * 60
    keep = []
    for cookie in cookies:
        if not _domain_covers(str(cookie.get("domain") or ""), host):
            continue
        try:
            already_persistent = float(cookie.get("expires") or -1) > 0
        except (TypeError, ValueError):
            already_persistent = False
        value = str(cookie.get("value") or "")
        name = str(cookie.get("name") or "")
        if already_persistent or not value or not name:
            continue
        item = {
            "name": name,
            "value": value,
            "domain": str(cookie.get("domain") or ""),
            "path": str(cookie.get("path") or "/"),
            "expires": expires,
            "httpOnly": bool(cookie.get("httpOnly")),
            "secure": bool(cookie.get("secure")),
        }
        same_site = str(cookie.get("sameSite") or "")
        if same_site in {"Strict", "Lax", "None"}:
            item["sameSite"] = same_site
        keep.append(item)
    if not keep:
        return 0
    try:
        context.add_cookies(keep)
    except Exception:
        return 0
    return len(keep)


def _has_sign_in_cookie(context, host: str) -> bool:
    names = _signed_in_cookie_names(host)
    if not names:
        return False
    try:
        cookies = context.cookies()
    except Exception:
        return False
    return any(
        str(cookie.get("name") or "") in names and str(cookie.get("value") or "")
        for cookie in cookies
    )


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
    host = urlparse(str(source_url or "")).hostname or ""
    started = time.monotonic()
    timed_out = True
    signed_in = False
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
                    # Close the window the moment the sign-in lands: a window
                    # left in front of the person is in their way, not help.
                    if _has_sign_in_cookie(context, host):
                        signed_in = True
                        timed_out = False
                        _persist_session_cookies(context, host)
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    return {
        "opened": True,
        "signed_in": signed_in,
        "closed_after_login": signed_in,
        "timed_out": timed_out,
        "waited_seconds": round(time.monotonic() - started, 1),
        "pages_seen": pages_seen,
        "final_url": final_url,
    }


# One list, not two: `browser._EGRESS_CONTROL_MARKERS` already names the wording a
# site uses when it refuses this network rather than this reader, and a copy here
# would drift until the re-check and the read disagreed about the same page.
# Xiaohongshu answers the QR create API with error_code 300012 and lands on
# /website-login/error with "当前网络存在风险" instead of a QR panel; no scan can
# clear that, so neither the session worker nor a headless re-check may report a
# sign-in from such a page.
_RISK_CONTROL_HINTS = _EGRESS_CONTROL_MARKERS


def _risk_controlled(title: str, text: str, url: str = "") -> bool:
    """True when the site refused the network, not the sign-in step.

    A readable page is not proof that the wall came down: the site can serve the
    risk-control page with 200 and real text, and the fetch can also happen to
    land past it while the sign-in path itself is still refused.
    """

    haystack = f"{title}\n{text}\n{url}".lower()
    return any(hint in haystack for hint in _RISK_CONTROL_HINTS)


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
    final_url = str(source_url or "")
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
                    final_url = page.url or final_url
                except Exception:
                    pass
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
    risk_blocked = _risk_controlled(title, text, final_url)
    page_access = bool(status and 200 <= int(status) < 400) and bool(
        text.strip() or media_urls
    )
    if looks_like_login and not media_urls:
        page_access = False
    if risk_blocked:
        # A 200 carrying the risk-control page is not access to the material;
        # reporting it as access would let the caller resume a task it cannot do.
        page_access = False
    media_access = bool(media_urls)
    headless_reusable = bool(
        page_access and not (looks_like_login and not media_access) and not risk_blocked
    )
    if headless_reusable:
        reason = "readable"
        try:
            from . import authflow

            authflow.mark_profile_session(urlparse(str(source_url or "")).hostname or "")
        except Exception:  # noqa: BLE001 - the verification result still stands
            pass
    elif risk_blocked:
        # Not a login failure and not a success: the egress was refused, so the
        # same call from another network is the only thing that can change it.
        reason = "egress_blocked"
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
        "egress_blocked": risk_blocked,
    }
