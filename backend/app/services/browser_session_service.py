from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from uuid import uuid4

from app.core.config import BASE_DIR
from app.services.browser_discovery_service import (
    browser_source_metadata,
    classify_browser_page_state,
    extract_visible_video_items,
)


class BrowserSessionError(RuntimeError):
    pass


def _netscape_cookie_line(cookie: dict) -> str:
    domain = str(cookie.get("domain") or "")
    path = str(cookie.get("path") or "/")
    name = str(cookie.get("name") or "")
    value = str(cookie.get("value") or "")
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    secure = "TRUE" if cookie.get("secure") else "FALSE"
    raw_expires = cookie.get("expires")
    try:
        expires = int(raw_expires or 0)
    except (TypeError, ValueError):
        expires = 0
    if expires < 0:
        expires = 0
    return "\t".join([
        domain,
        include_subdomains,
        path,
        secure,
        str(expires),
        name,
        value,
    ]) + "\n"


class _BrowserSession:
    def __init__(self, process, source_url: str, platform: str, limit: int):
        self.process = process
        self.source_url = source_url
        self.platform = platform
        self.limit = limit
        self.lock = threading.Lock()


class BrowserSessionManager:
    def __init__(self):
        self._sessions: dict[str, _BrowserSession] = {}
        self._lock = threading.Lock()

    def _worker_command(self, source_url: str, platform: str, limit: int, profile_dir: Path):
        return [
            sys.executable,
            "-m",
            "app.services.browser_worker",
            "--url",
            source_url,
            "--platform",
            platform,
            "--limit",
            str(limit),
            "--profile-dir",
            str(profile_dir),
        ]

    def create(self, source_url: str, limit: int = 100) -> dict:
        session_id = str(uuid4())
        metadata = browser_source_metadata(source_url)
        profile_dir = Path(
            os.getenv(
                "ENVOY_BROWSER_PROFILE_DIR",
                str(BASE_DIR / "tmp" / "browser-sessions"),
            )
        ) / session_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            self._worker_command(source_url, metadata["platform"], limit, profile_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        session = _BrowserSession(process, source_url, metadata["platform"], limit)
        try:
            ready = self._read_response(session)
        except Exception:
            process.kill()
            raise
        if ready.get("status") != "ready":
            self._stop_process(session)
            raise BrowserSessionError(str(ready.get("error") or "Browser session failed to start"))
        with self._lock:
            self._sessions[session_id] = session
        return {
            "session_id": session_id,
            "source_url": source_url,
            "platform": metadata["platform"],
            "source_kind": metadata["source_kind"],
            "status": "waiting_for_user",
            "requires_user_action": True,
            "navigation_error": ready.get("navigation_error"),
            "message": (
                "浏览器窗口已保留。若页面未加载，请在窗口中刷新或手动打开来源链接，"
                "完成登录/验证后再继续。"
                if ready.get("navigation_error")
                else "浏览器页面已打开，请完成登录或验证后继续。"
            ),
        }

    @staticmethod
    def _read_response(session: _BrowserSession) -> dict:
        if session.process.stdout is None:
            raise BrowserSessionError("Browser worker stdout is unavailable")
        line = session.process.stdout.readline()
        if not line:
            raise BrowserSessionError("Browser worker exited before returning a result")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BrowserSessionError(f"Browser worker returned invalid JSON: {line[:300]}") from exc
        if not isinstance(payload, dict):
            raise BrowserSessionError("Browser worker returned an invalid response")
        return payload

    def _request(self, session_id: str, command: str, payload: dict | None = None) -> dict:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise BrowserSessionError("Browser session not found or expired")
        with session.lock:
            if session.process.poll() is not None:
                raise BrowserSessionError("Browser session has exited; start a new session")
            if session.process.stdin is None:
                raise BrowserSessionError("Browser worker stdin is unavailable")
            message = {"command": command, **(payload or {})}
            session.process.stdin.write(json.dumps(message) + "\n")
            session.process.stdin.flush()
            return self._read_response(session)

    def status(self, session_id: str) -> dict:
        return self._request(session_id, "status")

    def discover(self, session_id: str) -> dict:
        result = self._request(session_id, "extract")
        records = result.get("records") if isinstance(result.get("records"), list) else []
        items = extract_visible_video_items(records, self._sessions[session_id].platform, self._sessions[session_id].limit)
        state = classify_browser_page_state(
            str(result.get("title") or ""),
            str(result.get("body_text") or ""),
            len(items),
        )
        return {
            **state,
            "source_url": self._sessions[session_id].source_url,
            "platform": self._sessions[session_id].platform,
            "source_kind": browser_source_metadata(self._sessions[session_id].source_url)["source_kind"],
            "discovery_method": "browser_visible",
            "items": items,
            "item_count": len(items),
        }

    def export_cookies(self, session_id: str) -> str:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise BrowserSessionError("Browser session not found or expired")
        result = self._request(session_id, "cookies")
        cookies = result.get("cookies") if isinstance(result.get("cookies"), list) else []
        if not cookies:
            raise BrowserSessionError("Browser session has no cookies; complete the page login or verification first")
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="envoy-browser-",
            suffix=".cookies.txt",
            delete=False,
        )
        try:
            handle.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = str(cookie.get("domain") or "")
                name = str(cookie.get("name") or "")
                if not domain or not name:
                    continue
                handle.write(_netscape_cookie_line(cookie))
            return handle.name
        finally:
            handle.close()

    def download_media(self, session_id: str, source_url: str, output_dir: str) -> dict:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise BrowserSessionError("Browser session not found or expired")
        output_path = Path(output_dir) / "browser-session.mp4"
        result = self._request(
            session_id,
            "download_media",
            {"output_path": str(output_path)},
        )
        if result.get("status") != "downloaded":
            raise BrowserSessionError(str(result.get("error") or "Browser session could not download media"))
        return {
            "path": str(result["path"]),
            "video_id": Path(source_url.rstrip("/")).name or "browser-video",
            "title": str(result.get("title") or "Browser video"),
            "duration_sec": None,
            "extractor": "browser_session",
            "webpage_url": source_url,
        }

    @staticmethod
    def _stop_process(session: _BrowserSession):
        try:
            if session.process.stdin:
                session.process.stdin.write(json.dumps({"command": "close"}) + "\n")
                session.process.stdin.flush()
        except Exception:
            pass
        try:
            session.process.terminate()
            session.process.wait(timeout=5)
        except Exception:
            try:
                session.process.kill()
            except Exception:
                pass

    def close(self, session_id: str) -> dict:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return {"status": "closed", "session_id": session_id}
        self._stop_process(session)
        return {"status": "closed", "session_id": session_id}


browser_session_manager = BrowserSessionManager()
