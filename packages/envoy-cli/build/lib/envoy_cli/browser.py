from __future__ import annotations

from .netpolicy import detected_proxy, explicit_proxy, proxy_plan

import os
import re
import shutil
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
    '[class*="newsletter" i],[class*="download" i],[id*="cookie" i],[id*="consent" i],' +
    '[aria-label*="consent" i],.cmp-container,#onetrust-banner-sdk,.qc-cmp2-container'
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
    """The browser runtime cannot start yet.

    ``setup_required`` marks the recoverable case: Playwright is installed but
    the Chromium binary is not. The caller can then tell the user to run
    ``envoy browser setup`` once instead of reporting a page-level failure, and
    ``next_action`` carries that command (or the reason none exists).
    """

    def __init__(self, message: str, *, setup_required: bool = False) -> None:
        super().__init__(message)
        self.setup_required = setup_required


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


def _playwright_cache_dir() -> Path | None:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override != "0":
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            return None
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def browser_disk_usage() -> dict:
    """Report how much disk the browser runtime occupies, per download.

    The browser is by far the largest thing Envoy can put on a machine (683 MB
    on the measured Windows host: 412 MB headed Chromium plus a 267 MB headless
    shell), and an installer user has no other way to see it. Read from the same
    cache Playwright itself uses, without importing Playwright.
    """
    root = _playwright_cache_dir()
    if root is None:
        return {"path": None, "total_mb": 0.0, "total_bytes": 0, "components": {}}
    components: dict[str, float] = {}
    total = 0
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
        except OSError:
            continue
        size = _dir_size(entry)
        total += size
        components[entry.name] = round(size / 1_048_576, 1)
    return {
        "path": str(root),
        # Exact bytes as well: the MB figure is rounded for display, so a test (or
        # a caller comparing two hosts) needs the number that was actually summed.
        "total_bytes": total,
        "total_mb": round(total / 1_048_576, 1),
        "components": components,
    }


_BROWSER_FAMILIES = ("chromium_headless_shell", "chromium", "ffmpeg", "firefox", "webkit")


def _browser_revision(name: str) -> tuple[str, int] | None:
    for family in _BROWSER_FAMILIES:
        prefix = f"{family}-"
        if name.startswith(prefix):
            suffix = name[len(prefix) :]
            return (family, int(suffix)) if suffix.isdigit() else None
    return None


def prune_browser_versions(*, dry_run: bool = False) -> dict:
    """Delete browser downloads the installed Playwright no longer uses.

    Playwright writes a new revision per browser release and never removes the
    previous one, so every Envoy upgrade that pulls a newer Playwright leaves
    another 200-400 MB behind (five revisions on the machine this was written
    on). Only directory names that are a known family plus a numeric revision
    are candidates; anything else in the cache is left untouched.
    """
    root = _playwright_cache_dir()
    report: dict = {
        "path": None if root is None else str(root),
        "removed": [],
        "kept": [],
        "freed_bytes": 0,
        "freed_mb": 0.0,
        "dry_run": dry_run,
    }
    if root is None:
        return report
    try:
        entries = list(root.iterdir())
    except OSError:
        return report
    families: dict[str, list[tuple[int, Path]]] = {}
    for entry in entries:
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
        except OSError:
            continue
        parsed = _browser_revision(entry.name)
        if parsed is None:
            continue
        families.setdefault(parsed[0], []).append((parsed[1], entry))
    for items in families.values():
        items.sort(key=lambda item: item[0])
        newest = items[-1][1]
        report["kept"].append({"name": newest.name, "bytes": _dir_size(newest)})
        for _revision, stale in items[:-1]:
            size = _dir_size(stale)
            if not dry_run:
                try:
                    shutil.rmtree(stale)
                except OSError:
                    # A locked directory stays reported as kept; the next setup
                    # retries it instead of failing the install.
                    report["kept"].append({"name": stale.name, "bytes": size})
                    continue
            report["removed"].append({"name": stale.name, "bytes": size})
            report["freed_bytes"] += size
    report["freed_mb"] = round(report["freed_bytes"] / 1_048_576, 1)
    return report


def browser_runtime_status() -> dict:
    disk = browser_disk_usage()
    try:
        import playwright  # noqa: F401
    except ImportError:
        return {
            "installed": False,
            "browser": "chromium",
            "ready": False,
            "profile": str(browser_profile_dir(create=False)),
            "disk": disk,
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
        "disk": disk,
    }


_AUTO_BROWSER_SETUP_ATTEMPTED = False


def _auto_setup_allowed() -> bool:
    """Chromium may be installed on demand, once, outside tests.

    A freshly provisioned runtime has the Playwright package but not the
    browser binary, so the first page that needs a browser would otherwise end
    in "run envoy browser setup" — a command a plugin user often has no shell
    for. Installing it here keeps the first click working; the download is
    skipped under pytest and can be turned off with
    ENVOY_DISABLE_AUTO_BROWSER_SETUP=1.
    """
    if os.environ.get("ENVOY_DISABLE_AUTO_BROWSER_SETUP"):
        return False
    return "pytest" not in sys.modules


def _chromium_executable_exists() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except Exception:
        return False


def ensure_browser_ready(
    *,
    timeout_seconds: float = 900.0,
    auto_setup: bool | None = None,
) -> bool:
    """Return True when a Chromium binary is present, installing it once if not.

    ``timeout_seconds`` bounds the download so a caller working inside a host
    tool deadline can stop waiting instead of burning that deadline on a
    bootstrap. ``auto_setup=None`` keeps the historical on-demand behaviour;
    read paths pass ``auto_setup=False`` and let the install step provision the
    browser up front (see ``integrations/install.sh``), because a first read
    must not spend minutes downloading.
    """
    global _AUTO_BROWSER_SETUP_ATTEMPTED
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    if _chromium_executable_exists():
        return True
    allowed = _auto_setup_allowed() if auto_setup is None else bool(auto_setup)
    if _AUTO_BROWSER_SETUP_ATTEMPTED or not allowed:
        return False
    if timeout_seconds <= 0:
        return False
    _AUTO_BROWSER_SETUP_ATTEMPTED = True
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        return False
    if int(result.returncode) != 0:
        return False
    return _chromium_executable_exists()


# A read may hand the setup at most this long before it gives up and reports a
# capability gap. The full download takes 1-2 minutes on a normal connection;
# anything slower belongs in the install step, not in a user's first click.
_READ_SETUP_BUDGET_SECONDS = 120.0


def _read_setup_budget(max_work_seconds: int) -> float:
    return max(0.0, min(_READ_SETUP_BUDGET_SECONDS, float(max_work_seconds)))


def _read_auto_setup_allowed() -> bool:
    """A read provisions Chromium only when the user opted in for this host."""
    return os.environ.get("ENVOY_AUTO_BROWSER_SETUP") == "1"


def setup_command() -> str:
    return "envoy browser setup"


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
    if int(result.returncode) != 0:
        return int(result.returncode)
    # A Playwright upgrade downloads a fresh revision; drop the one it replaced
    # so re-installing Envoy on a provisioned machine reuses the cache instead
    # of growing it by a few hundred MB each time.
    pruned = prune_browser_versions()
    if pruned["removed"]:
        print(
            f"envoy browser setup: removed {len(pruned['removed'])} stale browser "
            f"download(s), freed {pruned['freed_mb']} MB"
        )
    return 0


def _page_state(page) -> tuple[str, str, bool]:
    title = str(page.title() or "").strip()
    try:
        text = str(page.locator("body").inner_text(timeout=5000) or "").strip()
    except Exception:
        text = ""
    haystack = f"{title}\n{text[:5000]}".lower()
    challenge = any(marker in haystack for marker in _CHALLENGE_MARKERS)
    return title, text, challenge


def _guard_route(route, dns_cache: dict[str, str]) -> None:
    from .reader import _is_public_http_url

    request_url = str(route.request.url)
    scheme = urlparse(request_url).scheme.lower()
    if scheme in {"data", "blob", "about"}:
        route.continue_()
    elif _is_public_http_url(request_url, dns_cache):
        route.continue_()
    else:
        route.abort("blockedbyclient")


def _navigate(context, page, url: str, dns_cache: dict[str, str], timeout_ms: int) -> None:
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
    """Visible browser mode is opt-in, and only for the local user's terminal.

    Envoy normally runs behind an MCP host whose stdio is a pipe. Launching a
    visible window there raises a login prompt on a screen nobody is watching
    and hands the model a foreground action it must not take on its own, so
    the opt-in only takes effect when a human is at an interactive terminal.
    """

    enabled = (
        bool(override)
        if override is not None
        else os.environ.get("ENVOY_VISIBLE_BROWSER", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if not enabled:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _is_http_media_url(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


# A feed page publishes a short muted teaser clip next to the real player
# (Douyin serves it from `lf-douyin-pc-web.douyinstatic.com`). The teaser is a
# legal HTTPS video URL that autoplays, so a player which has not mounted yet
# looks resolved: capture downloads a 2.6 s silent preview and every later
# stage reports "no audio" instead of "the player never loaded".
_PREVIEW_MEDIA_HOSTS = ("douyinstatic.com",)


def _is_preview_media_url(value: str) -> bool:
    host = (urlparse(str(value or "")).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _PREVIEW_MEDIA_HOSTS)


def _await_page_video_url(page, timeout_seconds: float = 12.0) -> list[str]:
    """Give a lazy player a bounded chance to publish a real media URL.

    Feed pages reveal the player when a login overlay goes away: the element
    first appears with an empty ``src`` (or an MSE ``blob:`` source) and only
    binds the signed CDN URL once it starts loading. Waiting on an unstarted
    element is cheap; waiting on one that is already playing (bilibili-style
    MSE, which keeps ``blob:`` forever) is not, so a playing element returns
    immediately. A bound teaser URL does not count as resolved: it keeps the
    wait alive until the real source appears or the window closes.
    """

    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    while True:
        urls = [url for url in _page_video_urls(page) if _is_http_media_url(url)]
        playable = [url for url in urls if not _is_preview_media_url(url)]
        if playable:
            return playable
        if time.monotonic() >= deadline:
            return []
        if urls:
            # Only a teaser is bound, which is proof the player is still mounting.
            stalled = True
        else:
            try:
                stalled = bool(page.evaluate(
                    """() => [...document.querySelectorAll('video')].some(node =>
                         Number(node.readyState || 0) < 2 || Number(node.currentTime || 0) <= 0)"""
                ))
            except Exception:
                return []
        if not stalled:
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


def _apply_imported_cookies(context, source_url: str) -> int:
    """Load the cookies the user imported for this site, if any.

    Injection is scoped to the requested host, so the isolated profile never
    picks up unrelated sessions, and a missing store is a no-op: anonymous
    pages keep reading exactly as before. Credential reuse itself is opt-in:
    with the switch off (the default) a stored session is never attached.
    """

    from . import features

    if not features.credential_handoff_enabled():
        return 0
    try:
        from .cookies import load_cookies_for

        cookies = load_cookies_for(source_url)
    except Exception:
        return 0
    if not cookies:
        return 0
    try:
        context.add_cookies(cookies)
    except Exception:
        return 0
    return len(cookies)


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

    dns_cache: dict[str, str] = {}
    if not _is_public_http_url(source_url, dns_cache):
        raise BrowserReadError("Browser reading is limited to public HTTP(S) addresses")

    # Bootstrap is judged here, not performed: a read that downloads Chromium
    # inside the host's tool deadline turns a 300 s timeout into "page failed".
    # The install step provisions the browser; a user who opted in with
    # ENVOY_AUTO_BROWSER_SETUP=1 still gets one bounded attempt.
    if not ensure_browser_ready(
        timeout_seconds=_read_setup_budget(max_work_seconds),
        auto_setup=_read_auto_setup_allowed(),
    ):
        raise BrowserRuntimeUnavailable(
            "Envoy's browser is not installed yet; run "
            f"`{setup_command()}` once (about 1-2 minutes) and retry. "
            "Reading did not start, so nothing was downloaded or marked as a page failure.",
            setup_required=True,
        )
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
                context = playwright.chromium.launch_persistent_context(str(profile), **kwargs)
            except Exception as exc:
                mode = "headless" if headless else "visible"
                raise BrowserRuntimeUnavailable(
                    f"Could not start the {mode} Envoy browser ({type(exc).__name__})"
                ) from exc
            _apply_imported_cookies(context, source_url)
            return context

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
                "security verification; Envoy does not open a window from a "
                "non-interactive session. Run the read yourself in a terminal "
                "with visible browser mode enabled, or import cookies."
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
