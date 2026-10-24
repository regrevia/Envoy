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


def _dismiss_login_overlays(page) -> int:
    dismissed = 0
    try:
        dismissed += int(page.evaluate(_DISMISS_LOGIN_OVERLAY_SCRIPT) or 0)
    except Exception:
        pass
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for frame in frames[:20]:
        try:
            dismissed += int(frame.evaluate(_DISMISS_LOGIN_OVERLAY_SCRIPT) or 0)
        except Exception:
            continue
    return dismissed


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


def _capture(page, source_url: str) -> BrowserPage:
    title, text = _load_visible_content(page)
    try:
        html = str(page.content())
    except Exception as exc:
        raise BrowserReadError(f"Could not capture the visible page ({type(exc).__name__})") from exc
    page_video_urls = tuple(_page_video_urls(page))
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
                _install_media_capture(page)
                _navigate(context, page, source_url, dns_cache, timeout_ms)
                _load_visible_content(page)
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
