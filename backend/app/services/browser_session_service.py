from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
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
            "message": "Browser page opened. Complete any login or verification, then request discovery.",
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

    def _request(self, session_id: str, command: str) -> dict:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise BrowserSessionError("Browser session not found or expired")
        with session.lock:
            if session.process.poll() is not None:
                raise BrowserSessionError("Browser session has exited; start a new session")
            if session.process.stdin is None:
                raise BrowserSessionError("Browser worker stdin is unavailable")
            session.process.stdin.write(json.dumps({"command": command}) + "\n")
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
