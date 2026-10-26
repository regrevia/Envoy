"""A background sign-in session for links that need a human.

A model cannot see a login page, and asking the user to find a terminal is the
wrong shape for a chat. This session keeps the site's own sign-in page open in a
browser of its own, publishes a screenshot of that page (usually the QR code the
site wants scanned) so the agent can hand the picture to the user, and records
when the sign-in actually worked so the original link can be read afterwards.

The browser is a separate process on purpose: the agent's turn ends right after
the screenshot is delivered, the page stays open while the user scans, and a
later call inspects the record instead of holding a request open.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import authflow

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
    "扫码",
    "二维码",
)

FIRST_SHOT_SECONDS = 20.0
DEFAULT_TIMEOUT_SECONDS = 300

# Sites that answer a link with a risk-control page instead of the article put
# their own sign-in entry on a stable path; the picture the user needs is the
# code on that page, not the error text of the blocked one.
_LOGIN_URLS = {
    "zhihu.com": "https://www.zhihu.com/signin",
    "xiaohongshu.com": "https://www.xiaohongshu.com/login",
    "weibo.com": "https://weibo.com/login.php",
    "douyin.com": "https://www.douyin.com/",
}
_BLOCKED_HINTS = ("异常", "403", "captcha", "安全验证", "人机", "访问受限", "forbidden")


def _looks_blocked(text: str) -> bool:
    lowered = text.lower()
    if not text.strip():
        return False
    return any(hint in lowered for hint in _BLOCKED_HINTS)


def _login_url(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return url
    for site, login in _LOGIN_URLS.items():
        if host == site or host.endswith("." + site):
            return login
    return url


def _session_dir(action_id: str) -> Path:
    return authflow.actions_dir() / str(action_id)


def _state_path(action_id: str) -> Path:
    return _session_dir(action_id) / "session.json"


def read_state(action_id: str) -> dict:
    try:
        payload = json.loads(_state_path(action_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(action_id: str, payload: dict) -> None:
    path = _state_path(action_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Windows raises WinError 87 instead of ProcessLookupError for a pid
        # that is already gone.
        return False
    return True


def start(
    url: str,
    *,
    action_id: str | None = None,
    purpose: str = "read_source",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    visible: bool = False,
) -> dict:
    """Open the sign-in page in a detached browser and wait for its first picture."""

    action = authflow.load_auth_action(action_id) if action_id else None
    if action is None:
        action = authflow.create_auth_action(url, purpose=purpose, reason="needs_user_action")
    action_id = str(action["action_id"])
    work = _session_dir(action_id)
    work.mkdir(parents=True, exist_ok=True)

    state = read_state(action_id)
    running = bool(state) and state.get("status") not in {"signed_in", "canceled", "failed"}
    running = running and _pid_alive(state.get("pid"))
    if not running:
        log = work / "session.log"
        cmd = [
            sys.executable,
            "-m",
            "envoy_cli.auth_session",
            "--worker",
            "--url",
            str(action.get("source_url") or url),
            "--action-id",
            action_id,
            "--out",
            str(work),
            "--timeout",
            str(int(timeout_seconds)),
        ]
        cmd.append("--visible" if visible else "--headless")
        with open(log, "ab") as handle:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                start_new_session=True,
            )

    deadline = time.monotonic() + FIRST_SHOT_SECONDS
    screen_path = ""
    while time.monotonic() < deadline:
        current = read_state(action_id)
        candidate = str(current.get("screenshot") or "")
        if candidate and Path(candidate).exists():
            screen_path = candidate
            break
        if current.get("status") in {"failed", "signed_in"}:
            break
        time.sleep(0.5)

    current = read_state(action_id)
    return {
        "action_id": action_id,
        "host": action.get("host"),
        "source_url": action.get("source_url") or url,
        "status": current.get("status") or ("screenshot_ready" if screen_path else "starting"),
        "screen_path": screen_path,
        "screen_mime": "image/png",
        "browser": "visible" if visible else "background",
        "timeout_seconds": int(timeout_seconds),
        "log_path": str(work / "session.log"),
        "browser_pid": current.get("pid"),
        "final_url": current.get("final_url") or "",
    }


def status(action_id: str) -> dict:
    """Report whether the human finished, without opening anything."""

    action = authflow.load_auth_action(action_id) or {}
    current = read_state(action_id)
    screen = str(current.get("screenshot") or "")
    state = current.get("status") or "unknown"
    alive = _pid_alive(current.get("pid"))
    if state in {"starting", "waiting"} and not alive:
        state = "not_running"
    payload = {
        "action_id": action_id,
        "host": action.get("host"),
        "source_url": action.get("source_url"),
        "status": state,
        "screen_path": screen if screen and Path(screen).exists() else "",
        "screen_mime": "image/png",
        "final_url": current.get("final_url") or "",
        "browser_alive": alive,
        "action_status": action.get("status"),
        "action_url": action.get("source_url"),
    }
    if payload["status"] == "signed_in":
        action = authflow.update_auth_action(action_id, status="verifying") or action
        payload["action_status"] = action.get("status")
    return payload


def stop(action_id: str) -> dict:
    """Close the background browser and mark the pause canceled."""

    current = read_state(action_id)
    pid = current.get("pid")
    stopped = False
    if _pid_alive(pid):
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            stopped = True
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(int(pid), signal.SIGTERM)
                stopped = True
            except (ProcessLookupError, PermissionError, TypeError, ValueError):
                stopped = False
    _write_state(
        action_id,
        {**current, "status": "canceled", "updated_at": time.time(), "pid": None},
    )
    authflow.update_auth_action(action_id, status="canceled")
    return {"action_id": action_id, "status": "canceled", "browser_stopped": stopped}


def _page_state(page) -> tuple[str, str, str]:
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        text = page.inner_text("body", timeout=5000)
    except Exception:
        text = ""
    return title, text, ""


def _worker(
    url: str,
    action_id: str,
    out_dir: Path,
    timeout_seconds: int,
    visible: bool,
) -> int:
    from .browser import browser_profile_dir

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _write_state(
            action_id,
            {
                "status": "failed",
                "reason": "playwright_missing",
                "updated_at": time.time(),
            },
        )
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "sign-in.png"
    shot_tmp = out_dir / "sign-in.tmp.png"
    _write_state(
        action_id,
        {"status": "starting", "pid": os.getpid(), "updated_at": time.time()},
    )
    signed_in = False
    status = "waiting"
    final_url = ""
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(browser_profile_dir()),
                headless=not visible,
                args=["--disable-dev-shm-usage", "--new-window"],
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
                fallback = _login_url(url)
                if fallback != url:
                    try:
                        _t, first_text, _c = _page_state(page)
                    except Exception:
                        first_text = ""
                    if _looks_blocked(first_text):
                        try:
                            page.goto(fallback, wait_until="domcontentloaded", timeout=45000)
                        except Exception:
                            pass
                deadline = time.monotonic() + max(30, int(timeout_seconds))
                while time.monotonic() < deadline:
                    try:
                        page.screenshot(path=str(shot_tmp))
                        os.replace(shot_tmp, shot)
                    except Exception:
                        pass
                    _title, text, _challenge = _page_state(page)
                    try:
                        final_url = page.url
                    except Exception:
                        final_url = ""
                    if shot.exists():
                        _write_state(
                            action_id,
                            {
                                "status": "waiting",
                                "pid": os.getpid(),
                                "screenshot": str(shot),
                                "final_url": final_url,
                                "updated_at": time.time(),
                            },
                        )
                    lowered = text.lower()
                    if text.strip() and not any(hint in lowered for hint in _LOGIN_HINTS):
                        signed_in = True
                        break
                    time.sleep(1.5)
                status = "signed_in" if signed_in else "waiting"
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:  # pragma: no cover - depends on the local browser
        _write_state(
            action_id,
            {
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "screenshot": str(shot) if shot.exists() else "",
                "updated_at": time.time(),
            },
        )
        return 1
    _write_state(
        action_id,
        {
            "status": status,
            "pid": None,
            "screenshot": str(shot) if shot.exists() else "",
            "final_url": final_url,
            "updated_at": time.time(),
        },
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="envoy-auth-session")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--url", default="")
    parser.add_argument("--action-id", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--visible", action="store_true")
    group.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    if not args.worker or not args.action_id:
        parser.error("internal worker entry point")
    return _worker(
        args.url,
        args.action_id,
        Path(args.out or _session_dir(args.action_id)),
        args.timeout,
        bool(args.visible),
    )


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
