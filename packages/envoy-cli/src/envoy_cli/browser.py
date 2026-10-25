from __future__ import annotations

from .netpolicy import detected_proxy, explicit_proxy, proxy_plan

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


_CHALLENGE_MARKERS = (
    "验证码",
    "安全验证",
    "人机验证",
    "访问验证",
    "当前环境异常",
    "访问受限",
    "访问异常",
    "操作频繁",
    "访问过于频繁",
    "滑动验证",
    "登录后继续",
    "sign in to continue",
    "log in to continue",
    "login required",
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "access denied",
    "unusual traffic",
    "security check",
    "verify you are human",
    "checking your browser",
    "captcha",
)
_LOGIN_OVERLAY_MARKERS = (
    "login|sign in to continue|log in to continue|login required|"
    "登录后免费畅享|扫码登录|手机号登录|手机登录|验证码登录|"
    "请登录后继续观看"
)
_DISMISS_LOGIN_OVERLAY_SCRIPT = r"""
/* dismiss-login-overlay */
() => {
  const loginMarkers = /login|sign in|log in|登录|扫码|手机号|手机登录/i;
  const loginOverlayMarkers = /__LOGIN_OVERLAY_MARKERS__/i;
  const closeMarkers = /^(close|dismiss|关闭|关闭弹窗|×|✕|x)$/i;
  const overlays = [...document.querySelectorAll(
    'dialog,[role="dialog"],[class*="modal" i],[class*="popup" i],' +
    '[class*="login" i],[data-e2e*="login" i]'
  )];
  const viewportArea = Math.max(
    document.documentElement.clientWidth * document.documentElement.clientHeight,
    1
  );
  const viewportWidth = Math.max(document.documentElement.clientWidth, 1);
  const viewportHeight = Math.max(document.documentElement.clientHeight, 1);
  const loginTextNodes = [...document.querySelectorAll("body *")]
    .filter(node => {
      const text = (node.innerText || "").trim();
      return text.length > 0 && text.length <= 1200 &&
        loginOverlayMarkers.test(text);
    })
    .sort((left, right) =>
      (left.innerText || "").length - (right.innerText || "").length
    )
    .slice(0, 100);
  for (const node of loginTextNodes) {
    let ancestor = node;
    let panelAdded = false;
    for (let depth = 0; ancestor && depth < 8; depth += 1, ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      const box = ancestor.getBoundingClientRect();
      const area = box.width * box.height;
      const centeredDialogCard =
        area >= viewportArea * 0.1 &&
        area < viewportArea * 0.8 &&
        box.width >= Math.min(viewportWidth * 0.35, 320) &&
        box.height >= Math.min(viewportHeight * 0.3, 200) &&
        Math.abs((box.left + box.right) / 2 - viewportWidth / 2) <
          viewportWidth * 0.2 &&
        Math.abs((box.top + box.bottom) / 2 - viewportHeight / 2) <
          viewportHeight * 0.25;
      if (!panelAdded && area >= viewportArea * 0.12 &&
          area < viewportArea * 0.98 &&
          (style.position !== "static" || centeredDialogCard)) {
        overlays.push(ancestor);
        panelAdded = true;
      }
      if (["fixed", "absolute"].includes(style.position)) {
        if (!panelAdded) overlays.push(ancestor);
        break;
      }
    }
  }
  for (const overlay of [...new Set(overlays)]) {
    const rect = overlay.getBoundingClientRect();
    const style = getComputedStyle(overlay);
    if (!rect.width || !rect.height || style.display === "none" ||
        style.visibility === "hidden" || Number(style.opacity) === 0) continue;
    const text = overlay.innerText || "";
    const identity = [
      overlay.getAttribute("aria-label") || "",
      overlay.getAttribute("class") || "",
      overlay.getAttribute("data-e2e") || ""
    ].join(" ");
    if (!loginMarkers.test(text) && !loginMarkers.test(identity)) continue;

    const controls = [...overlay.querySelectorAll(
      'button,[role="button"],[aria-label],[title],svg'
    )];
    const visible = controls.filter(control => {
      const box = control.getBoundingClientRect();
      const controlStyle = getComputedStyle(control);
      return box.width && box.height && controlStyle.display !== "none" &&
        controlStyle.visibility !== "hidden";
    });
    const labeledClose = visible.find(control => {
      const label = [
        control.innerText || "",
        control.getAttribute("aria-label") || "",
        control.getAttribute("title") || "",
        control.getAttribute("data-e2e") || "",
        control.getAttribute("class") || ""
      ].join(" ").trim();
      return closeMarkers.test(label) || /close|dismiss|关闭/i.test(label);
    });
    const topRightClose = visible.find(control => {
      const box = control.getBoundingClientRect();
      return box.width <= 64 && box.height <= 64 &&
        box.right >= rect.right - Math.min(rect.width * 0.12, 72) &&
        box.top <= rect.top + Math.min(rect.height * 0.2, 96);
    });
    const close = labeledClose || topRightClose;
    if (!close) continue;
    close.dispatchEvent(new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window
    }));
    return 1;
  }
  return 0;
}
"""
_DISMISS_LOGIN_OVERLAY_SCRIPT = _DISMISS_LOGIN_OVERLAY_SCRIPT.replace(
    "__LOGIN_OVERLAY_MARKERS__",
    _LOGIN_OVERLAY_MARKERS,
)
# Consent walls, app-download banners, newsletter/subscribe dialogs and
# notification prompts all sit above the content and all have a dismiss control.
# The login script above is tuned for identity dialogs and skips them, so a
# second pass handles everything else: it clicks a bounded number of dismissal
# controls and restores scrolling that the overlay locked, then reports how many
# clicks it made so the caller can loop until the page settles.
_GENERIC_OVERLAY_SCRIPT = r"""
/* dismiss-generic-overlay */
() => {
  const overlayIdentity =
    /modal|popup|overlay|mask|dialog|consent|cookie|gdpr|banner|notice|notification|subscribe|paywall|newsletter|download|app-guide|guide/i;
  const dismissLabels =
    /^(×|✕|✖|╳|x|close|close dialog|close popup|dismiss|no thanks|not now|maybe later|later|skip|skip ad|got it|continue|continue in browser|continue on web|stay on web|accept|accept all|accept all cookies|accept cookies|agree|i agree|allow|allow all|关闭|关闭弹窗|关闭窗口|关闭广告|我知道了|知道了|跳过|跳过广告|暂不|暂不开启|暂不打开|以后再说|稍后|稍后再说|取消|继续浏览|继续浏览网页|继续在网页|接受|接受全部|接受所有|同意|同意并继续|同意并关闭|允许|允许全部)$/i;
  const skipLabels = /登录|登陆|sign in|log in|验证码|立即下载|马上安装|install now/i;
  const viewportWidth = Math.max(document.documentElement.clientWidth, 1);
  const viewportHeight = Math.max(document.documentElement.clientHeight, 1);
  const viewportArea = viewportWidth * viewportHeight;

  const unlockScrolling = () => {
    for (const node of [document.documentElement, document.body]) {
      if (!node) continue;
      const style = getComputedStyle(node);
      if (["hidden", "clip"].includes(style.overflowY) ||
          ["hidden", "clip"].includes(style.overflow)) {
        node.style.setProperty("overflow-y", "auto", "important");
        node.style.setProperty("overflow-x", "auto", "important");
      }
    }
  };

  const isVisible = (node) => {
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return box.width > 0 && box.height > 0 &&
      style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity || 1) > 0.01;
  };

  const isPageChrome = (node) => {
    if (node.querySelector("video,audio")) return true;
    if (node.querySelector("article,main")) return true;
    return (node.innerText || "").length > 2500;
  };

  const controls = (node) => [...node.querySelectorAll(
    'button,[role="button"],a,[aria-label],[title],svg,span,div'
  )].filter(control => control !== node && isVisible(control));

  const label = (control) => [
    control.innerText || "",
    control.getAttribute("aria-label") || "",
    control.getAttribute("title") || "",
    control.getAttribute("data-e2e") || ""
  ].join(" ").replace(/\s+/g, " ").trim();

  const candidates = [...document.querySelectorAll(
    'dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"],' +
    '[class*="modal" i],[class*="popup" i],[class*="overlay" i],[class*="mask" i],' +
    '[class*="consent" i],[class*="cookie" i],[class*="gdpr" i],[class*="banner" i],' +
    '[class*="notice" i],[class*="subscribe" i],[class*="paywall" i],' +
    '[class*="newsletter" i],[class*="download" i],[id*="cookie" i],[id*="consent" i]'
  )];
  let clicked = 0;
  for (const candidate of candidates) {
    if (clicked >= 3) break;
    if (!isVisible(candidate) || isPageChrome(candidate)) continue;
    const box = candidate.getBoundingClientRect();
    const style = getComputedStyle(candidate);
    const positioned = style.position === "fixed" || style.position === "absolute";
    const area = box.width * box.height;
    const identity = [
      candidate.getAttribute("class") || "",
      candidate.getAttribute("id") || "",
      candidate.getAttribute("role") || "",
      candidate.getAttribute("aria-label") || ""
    ].join(" ");
    if (!positioned && area < viewportArea * 0.02) continue;
    if (!overlayIdentity.test(identity) && area < viewportArea * 0.08) continue;

    const visibleControls = controls(candidate);
    let target = visibleControls.find(control => {
      const text = label(control);
      if (!text || text.length > 48) return false;
      if (skipLabels.test(text)) return false;
      return dismissLabels.test(text);
    });
    if (!target) {
      target = visibleControls.find(control => {
        const controlBox = control.getBoundingClientRect();
        return controlBox.width <= 64 && controlBox.height <= 64 &&
          controlBox.right >= box.right - Math.min(box.width * 0.12, 72) &&
          controlBox.top <= box.top + Math.min(box.height * 0.2, 96);
      });
    }
    if (!target) continue;
    target.dispatchEvent(new MouseEvent("click", {
      bubbles: true,
      cancelable: true,
      view: window
    }));
    clicked += 1;
  }
  unlockScrolling();
  return clicked;
}
"""

_EXPAND_SCRIPT = r"""
() => {
  const labels = new Set([
    "展开", "展开全文", "阅读全文", "继续阅读",
    "read more", "show more", "continue reading"
  ]);
  const candidates = [...document.querySelectorAll(
    'button,[role="button"],[aria-expanded="false"],a'
  )];
  let clicked = 0;
  for (const element of candidates) {
    const label = (element.innerText || element.getAttribute("aria-label") || "")
      .replace(/\s+/g, " ").trim().toLowerCase();
    if (!labels.has(label) || label.length > 40) continue;
    if (element.tagName === "A") {
      try {
        if (new URL(element.href, location.href).origin !== location.origin) continue;
      } catch (_) {
        continue;
      }
    }
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    element.click();
    clicked += 1;
    if (clicked >= 3) break;
  }
  return clicked;
}
"""


class BrowserRuntimeUnavailable(RuntimeError):
    pass


class BrowserReadError(RuntimeError):
    pass


class BrowserChallengeRequired(BrowserReadError):
    pass


@dataclass(frozen=True)
class BrowserPage:
    url: str
    title: str
    text: str
    html: str
    media_urls: tuple[str, ...] = ()
    page_video_urls: tuple[str, ...] = ()


def browser_profile_dir(*, create: bool = True) -> Path:
    configured = os.getenv("ENVOY_BROWSER_PROFILE_DIR", "").strip()
    profile = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".envoy" / "browser-profile"
    )
    if create:
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                profile.chmod(0o700)
            except OSError:
                pass
    return profile


def _browser_proxy_server() -> str | None:
    return explicit_proxy() or detected_proxy()


def browser_runtime_status() -> dict:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {
            "installed": False,
            "browser": "chromium",
            "ready": False,
            "profile": str(browser_profile_dir(create=False)),
        }
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            ready = executable.is_file()
    except Exception:
        ready = False
    return {
        "installed": True,
        "browser": "chromium",
        "ready": ready,
        "proxy_configured": _browser_proxy_server() is not None,
        "profile": str(browser_profile_dir(create=False)),
    }


def setup_browser() -> int:
    try:
        import playwright  # noqa: F401
    except ImportError:
        print(
            "Playwright is not installed. Reinstall Envoy's CLI runtime before setting up Chromium.",
            file=sys.stderr,
        )
        return 2
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    return int(result.returncode)


def _page_state(page) -> tuple[str, str, bool]:
    title = str(page.title() or "").strip()
    try:
        text = str(page.locator("body").inner_text(timeout=5000) or "").strip()
    except Exception:
        text = ""
    haystack = f"{title}\n{text[:5000]}".lower()
    challenge = any(marker in haystack for marker in _CHALLENGE_MARKERS)
    return title, text, challenge


def _guard_route(route, dns_cache: dict[str, bool]) -> None:
    from .reader import _is_public_http_url

    request_url = str(route.request.url)
    scheme = urlparse(request_url).scheme.lower()
    if scheme in {"data", "blob", "about"}:
        route.continue_()
    elif _is_public_http_url(request_url, dns_cache):
        route.continue_()
    else:
        route.abort("blockedbyclient")


def _navigate(context, page, url: str, dns_cache: dict[str, bool], timeout_ms: int) -> None:
    context.route("**/*", lambda route: _guard_route(route, dns_cache))
    page.goto(url, wait_until="commit", timeout=timeout_ms)
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=min(max(timeout_ms // 3, 1000), 5000),
        )
    except Exception as exc:
        if type(exc).__name__ != "TimeoutError":
            raise
    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=min(max(timeout_ms // 6, 500), 1500),
        )
    except Exception:
        pass


def _dismiss_overlays(page) -> int:
    """Clear identity dialogs and every other blocking overlay, silently.

    Two passes because they target different things: the login script is tuned
    for identity dialogs (and would ignore a cookie wall), the generic script
    closes consent/app-download/subscribe prompts and restores scrolling. Both
    run in the main frame and in same-process iframes, and both report the
    number of clicks so the caller can stop as soon as nothing changes.
    """

    dismissed = 0
    scripts = (_DISMISS_LOGIN_OVERLAY_SCRIPT, _GENERIC_OVERLAY_SCRIPT)
    for script in scripts:
        try:
            dismissed += int(page.evaluate(script) or 0)
        except Exception:
            pass
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame in frames[:20]:
        for script in scripts:
            try:
                dismissed += int(frame.evaluate(script) or 0)
            except Exception:
                continue
    return dismissed


# Older call sites and tests use the login name; the behaviour is now general.
_dismiss_login_overlays = _dismiss_overlays


def _close_extra_pages(context, keep_page) -> int:
    """Close popups a page opened in a new tab; they never show useful content."""

    try:
        pages = list(context.pages)
    except Exception:
        return 0
    closed = 0
    for candidate in pages:
        if candidate is keep_page:
            continue
        try:
            candidate.close()
            closed += 1
        except Exception:
            continue
    return closed


def _watch_for_popups(context, keep_page) -> None:
    try:
        context.on("page", lambda page: _close_extra_pages(context, keep_page))
    except Exception:
        pass


def _load_visible_content(page) -> tuple[str, str]:
    for _ in range(3):
        dismissed = _dismiss_login_overlays(page)
        if not dismissed:
            break
        page.wait_for_timeout(300)

    for _ in range(4):
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(350)
            page.evaluate(_EXPAND_SCRIPT)
            page.wait_for_timeout(250)
            _dismiss_login_overlays(page)
            page.wait_for_timeout(300)
        except Exception:
            break
    try:
        _dismiss_login_overlays(page)
        page.wait_for_timeout(300)
    except Exception:
        pass
    title, text, _challenge = _page_state(page)
    return title, text


def _visible_browser_allowed(override: bool | None) -> bool:
    """Visible browser mode is opt-in: silent headless reading is the default."""

    if override is not None:
        return bool(override)
    return os.environ.get("ENVOY_VISIBLE_BROWSER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_http_media_url(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def _await_page_video_url(page, timeout_seconds: float = 4.0) -> list[str]:
    """Give a lazy player a bounded chance to publish a real media URL.

    Feed pages reveal the player when a login overlay goes away: the element
    first appears with an empty ``src`` (or an MSE ``blob:`` source) and only
    binds the signed CDN URL once it starts loading. Waiting on an unstarted
    element is cheap; waiting on one that is already playing (bilibili-style
    MSE, which keeps ``blob:`` forever) is not, so a playing element returns
    immediately.
    """

    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    while True:
        urls = [url for url in _page_video_urls(page) if _is_http_media_url(url)]
        if urls:
            return urls
        try:
            stalled = bool(page.evaluate(
                """() => [...document.querySelectorAll('video')].some(node =>
                     Number(node.readyState || 0) < 2 || Number(node.currentTime || 0) <= 0)"""
            ))
        except Exception:
            return []
        if not stalled or time.monotonic() >= deadline:
            return []
        _dismiss_login_overlays(page)
        page.wait_for_timeout(300)


def _capture(page, source_url: str) -> BrowserPage:
    title, text = _load_visible_content(page)
    try:
        html = str(page.content())
    except Exception as exc:
        raise BrowserReadError(f"Could not capture the visible page ({type(exc).__name__})") from exc
    page_video_urls = tuple(_page_video_urls(page))
    if not any(_is_http_media_url(url) for url in page_video_urls):
        # A player that is still hidden or loading has no downloadable source
        # yet; without this window the capture records `blob:` internals only.
        page_video_urls = tuple(_await_page_video_url(page) or page_video_urls)
    media_urls = tuple(dict.fromkeys([*page_video_urls, *_captured_media_urls(page)]))
    return BrowserPage(
        url=str(page.url or source_url),
        title=title,
        text=text,
        html=html,
        media_urls=media_urls,
        page_video_urls=page_video_urls,
    )


def _page_video_urls(page) -> list[str]:
    urls: list[str] = []
    try:
        values = page.evaluate(
            """() => [...document.querySelectorAll('video,video source')]
              .flatMap(node => [node.currentSrc, node.src])
              .filter(Boolean)"""
        )
        if isinstance(values, list):
            urls.extend(str(value) for value in values if str(value).strip())
    except Exception:
        pass
    return list(dict.fromkeys(urls))


def _captured_media_urls(page) -> list[str]:
    captured = getattr(page, "_envoy_media_urls", ())
    return list(dict.fromkeys(
        str(value) for value in captured if str(value).strip()
    ))


def _page_media_urls(page) -> list[str]:
    return list(dict.fromkeys([
        *_page_video_urls(page),
        *_captured_media_urls(page),
    ]))


def read_page(
    source_url: str,
    *,
    profile_dir: Path | None = None,
    max_work_seconds: int = 180,
    playwright_factory=None,
    on_user_action: Callable[[str], None] | None = None,
    only_proxy: bool = False,
    allow_visible: bool | None = None,
) -> BrowserPage:
    try:
        if playwright_factory is None:
            from playwright.sync_api import sync_playwright
            playwright_factory = sync_playwright
    except ImportError as exc:
        raise BrowserRuntimeUnavailable(
            "Reinstall Envoy's CLI runtime and run `envoy browser setup`"
        ) from exc

    from .reader import _is_public_http_url

    dns_cache: dict[str, bool] = {}
    if not _is_public_http_url(source_url, dns_cache):
        raise BrowserReadError("Browser reading is limited to public HTTP(S) addresses")

    profile = profile_dir or browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        try:
            profile.chmod(0o700)
        except OSError:
            pass
    work_seconds = max(int(max_work_seconds), 1)
    deadline = time.monotonic() + work_seconds
    timeout_ms = min(work_seconds * 1000, 30_000)

    with playwright_factory() as playwright:
        def launch(headless: bool, proxy_server: str | None):
            browser_args = ["--disable-dev-shm-usage"]
            if proxy_server:
                browser_args.append(f"--proxy-server={proxy_server}")
            else:
                browser_args.append("--no-proxy-server")
            kwargs = {
                "headless": headless,
                "args": browser_args,
                "service_workers": "block",
            }
            if not headless:
                kwargs["args"].append("--new-window")
            try:
                return playwright.chromium.launch_persistent_context(str(profile), **kwargs)
            except Exception as exc:
                mode = "headless" if headless else "visible"
                raise BrowserRuntimeUnavailable(
                    f"Could not start the {mode} Envoy browser ({type(exc).__name__})"
                ) from exc

        challenge_proxy: str | None = None
        last_error: Exception | None = None
        if only_proxy:
            selected = explicit_proxy() or detected_proxy()
            if not selected:
                raise BrowserRuntimeUnavailable(
                    "No proxy is available for a second attempt"
                )
            attempts: list[str | None] = [selected]
        else:
            attempts = proxy_plan()
        for proxy_server in attempts:
            try:
                context = launch(True, proxy_server)
            except BrowserRuntimeUnavailable as exc:
                last_error = exc
                continue
            try:
                page = context.pages[0] if context.pages else context.new_page()
                _watch_for_popups(context, page)
                _install_media_capture(page)
                _navigate(context, page, source_url, dns_cache, timeout_ms)
                _load_visible_content(page)
                # A login dialog carries text such as `验证码登录`; leaving it in
                # the DOM made a dismissible overlay look like a security
                # challenge and escalate to a window the user never asked for.
                _dismiss_overlays(page)
                _close_extra_pages(context, page)
                _title, _text, challenge = _page_state(page)
                if not challenge:
                    return _capture(page, source_url)
                challenge_proxy = proxy_server
                break
            except (BrowserReadError, TimeoutError) as exc:
                last_error = exc
            finally:
                context.close()
        else:
            if last_error is not None:
                raise last_error
            raise BrowserReadError("The page could not be opened")

        if time.monotonic() >= deadline:
            raise BrowserReadError("Browser access challenge exceeded the work-time budget")

        if not _visible_browser_allowed(allow_visible):
            raise BrowserChallengeRequired(
                "The page blocks isolated headless reading with login or "
                "security verification; Envoy does not open a window unless "
                "visible browser mode is enabled"
            )

        context = launch(headless=False, proxy_server=challenge_proxy)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            _install_media_capture(page)
            _navigate(
                context,
                page,
                source_url,
                dns_cache,
                max(1000, int((deadline - time.monotonic()) * 1000)),
            )
            if on_user_action:
                on_user_action(str(page.url or source_url))
            else:
                print(
                    "Envoy opened its isolated browser. Complete the page's normal "
                    "login or verification action there; Envoy will resume automatically.",
                    file=sys.stderr,
                    flush=True,
                )

            while time.monotonic() < deadline:
                page.wait_for_timeout(500)
                _title, text, challenge = _page_state(page)
                if not challenge and len(text) >= 20:
                    return _capture(page, source_url)
            raise BrowserChallengeRequired(
                "The visible page still requires login or security verification; "
                "complete it in Envoy's browser and retry the link"
            )
        finally:
            context.close()


def _install_media_capture(page) -> None:
    captured: list[str] = []
    setattr(page, "_envoy_media_urls", captured)

    def on_response(response) -> None:
        try:
            content_type = str(response.headers.get("content-type") or "").lower()
            url = str(response.url or "")
        except Exception:
            return
        if (
            content_type.startswith("video/")
            or "mpegurl" in content_type
            or "dash" in content_type
            or re.search(r"\.(mp4|webm|m3u8|mpd|mov|m4v|ogv)(?:[?#]|$)", url, re.I)
        ):
            if url not in captured:
                captured.append(url)

    try:
        page.on("response", on_response)
    except Exception:
        pass
