"""The local login state Envoy keeps for a site.

Envoy reuses one persistent browser profile so a sign-in survives between
runs. That profile is also the only place a site's session is stored, so
"sign out" has to reach it, not just the cookie file that an import writes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from .browser import browser_profile_dir


def _cookie_db(profile: Path) -> Path | None:
    for candidate in (
        profile / "Default" / "Cookies",
        profile / "Cookies",
        profile / "Default" / "Network" / "Cookies",
    ):
        if candidate.is_file():
            return candidate
    return None


def clear_site_cookies(host: str) -> dict:
    """Delete one site's rows from the profile cookie database."""

    host = str(host or "").strip().lower()
    if not host:
        return {"cleared": False, "reason": "no_host"}
    profile = browser_profile_dir(create=False)
    database = _cookie_db(profile)
    if database is None:
        return {"cleared": False, "reason": "no_profile_cookies"}
    suffix = "%" + host + "%"
    try:
        connection = sqlite3.connect(f"file:{database}?mode=rw", uri=True)
        try:
            cursor = connection.execute(
                "DELETE FROM cookies WHERE host_key = ? OR host_key LIKE ?",
                (host, suffix),
            )
            connection.commit()
            removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"cleared": False, "reason": f"sqlite: {exc}"}
    return {"cleared": True, "host": host, "rows_removed": removed}


def clear_profile_state() -> dict:
    """Remove every trace of a sign-in that Envoy stores locally."""

    profile = browser_profile_dir(create=False)
    if not profile.is_dir():
        return {"cleared": False, "reason": "no_profile", "path": str(profile)}
    removed = 0
    failures = 0
    for child in list(profile.iterdir()):
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except OSError:
            failures += 1
    return {
        "cleared": failures == 0,
        "path": str(profile),
        "entries_removed": removed,
        "failures": failures,
    }


def profile_state_status() -> dict:
    profile = browser_profile_dir(create=False)
    database = _cookie_db(profile)
    rows = 0
    if database is not None:
        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                rows = int(
                    connection.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
                )
            finally:
                connection.close()
        except sqlite3.Error:
            rows = 0
    return {
        "profile": str(profile),
        "exists": profile.is_dir(),
        "cookie_rows": rows,
        "signed_in_sites": rows,
        "env_override": bool(os.environ.get("ENVOY_BROWSER_PROFILE_DIR")),
    }
