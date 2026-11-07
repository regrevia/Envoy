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
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from . import authflow, process_control

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
_BLOCKED_HINTS = (
    "异常",
    "403",
    "captcha",
    "安全验证",
    "人机",
    "访问受限",
    "forbidden",
    "too many requests",
)
# A risk-control page refuses the sign-in entry itself (Xiaohongshu answers the
# QR create API with error_code 300012 and lands on /website-login/error). No
# scan can clear it: the verdict is about the network egress, so the state must
# say so instead of reporting a sign-in that never happened.
_RISK_CONTROL_HINTS = (
    "安全限制",
    "ip存在风险",
    "ip 存在风险",
    "切换可靠网络",
    "访问过于频繁",
    "请求过于频繁",
    "too many requests",
    "300012",
)
# A page that still sits on the site's sign-in path has not moved past the gate
# even when its wording carries no hint word (a password form, a QR panel).
_LOGIN_PATH_HINTS = ("/login", "/signin", "/sign-in", "/passport", "/auth/", "/sso")


def _looks_like_gate(title: str, text: str, url: str = "") -> bool:
    """True while the page still shows the sign-in/verification step."""

    try:
        path = urllib.parse.urlsplit(str(url or "")).path.lower()
    except ValueError:
        path = ""
    if any(hint in path for hint in _LOGIN_PATH_HINTS):
        return True
    probe = f"{title} {text}".lower()
    if not probe.strip():
        return False
    if any(hint in probe for hint in _LOGIN_HINTS):
        return True
    return any(hint in probe for hint in _BLOCKED_HINTS)


def _looks_blocked(text: str) -> bool:
    lowered = text.lower()
    if not text.strip():
        return False
    return any(hint in lowered for hint in _BLOCKED_HINTS)


def _risk_controlled(title: str, text: str, url: str = "") -> bool:
    """True when the site refused the network itself, not the sign-in step."""

    haystack = f"{title}\n{text}\n{url}".lower()
    if not haystack.strip():
        return False
    return any(hint in haystack for hint in _RISK_CONTROL_HINTS)


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
    # One portable probe: on Windows the Unix answer would terminate the
    # process it was asked about (see process_control).
    return process_control.pid_alive(pid)


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
        "egress_blocked": bool(current.get("egress_blocked")),
        "reason": str(current.get("reason") or ""),
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
        "egress_blocked": bool(current.get("egress_blocked")),
        "reason": str(current.get("reason") or ""),
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
    stopped = process_control.stop_process(pid)
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
    risk_blocked = False
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
                try:
                    _t0, first_text, _c0 = _page_state(page)
                except Exception:
                    _t0, first_text = "", ""
                try:
                    first_url = page.url
                except Exception:
                    first_url = url
                if fallback != url and _looks_blocked(first_text):
                    try:
                        page.goto(fallback, wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        pass
                    try:
                        _t0, first_text, _c0 = _page_state(page)
                    except Exception:
                        first_text = ""
                    try:
                        first_url = page.url
                    except Exception:
                        first_url = fallback
                # A page that never showed a gate proves nothing: the link may
                # simply have been readable, and claiming "signed in" would send
                # the caller into a retry loop. Only a page that moved off the
                # gate counts.
                challenged = _looks_like_gate(_t0, first_text, first_url)
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
                    if _risk_controlled(_title, text, final_url):
                        risk_blocked = True
                        break
                    if (
                        challenged
                        and len(text.strip()) > 20
                        and not _looks_like_gate(_title, text, final_url)
                    ):
                        signed_in = True
                        break
                    if _looks_like_gate(_title, text, final_url):
                        challenged = True
                    time.sleep(1.5)
                status = "failed" if risk_blocked else ("signed_in" if signed_in else "waiting")
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
    if status == "signed_in":
        # The browser is closed now, so the profile's cookies are on disk. Only
        # this marker makes the cheap legs (page fetch, yt-dlp jar) pay for a
        # profile read; a public link never touches the browser.
        try:
            authflow.mark_profile_session(urllib.parse.urlsplit(url).hostname or "")
        except Exception:  # noqa: BLE001 - the record must still be written
            pass
    _write_state(
        action_id,
        {
            "status": status,
            "pid": None,
            "screenshot": str(shot) if shot.exists() else "",
            "final_url": final_url,
            "egress_blocked": risk_blocked,
            "reason": (
                "the site's network risk control refused the sign-in page itself, "
                "so this sign-in cannot clear it; a different network egress is "
                "what the site asks for"
                if risk_blocked
                else ""
            ),
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
