"""What the sign-in profile reports must be what was actually counted."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from envoy_cli import profile_state


def _profile_with_cookies(root: Path) -> Path:
    profile = root / "browser-profile"
    (profile / "Default").mkdir(parents=True)
    connection = sqlite3.connect(profile / "Default" / "Cookies")
    try:
        connection.execute(
            "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)"
        )
        connection.executemany(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            [
                (".example.org", "session", "a"),
                (".example.org", "prefs", "b"),
                (".news.test", "ab", "c"),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return profile


def test_cookie_rows_are_not_reported_as_signed_in_sites(tmp_path, monkeypatch):
    profile = _profile_with_cookies(tmp_path)
    monkeypatch.setattr(
        profile_state, "browser_profile_dir", lambda create=True: profile
    )

    status = profile_state.profile_state_status()

    assert status["cookie_rows"] == 3
    # Two domains, not three credentials, and never a claim that any of them is
    # signed in: cookie rows are also left behind by plain public browsing.
    assert status["cookie_domains"] == 2
    assert "signed_in_sites" not in status


def test_a_missing_profile_reports_zero_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(
        profile_state, "browser_profile_dir", lambda create=True: tmp_path / "absent"
    )

    status = profile_state.profile_state_status()

    assert status["exists"] is False
    assert status["cookie_rows"] == 0
    assert status["cookie_domains"] == 0
