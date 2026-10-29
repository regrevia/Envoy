"""The browser profile's regenerable caches are reclaimable, its sessions are not."""

from __future__ import annotations

import os
from pathlib import Path

from envoy_cli import cache


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "browser-profile"
    for relative, payload in (
        ("Default/Cache/data_0", b"x" * 4096),
        ("Default/Code Cache/js/index", b"y" * 2048),
        ("GraphiteDawnCache/graphite", b"z" * 1024),
    ):
        path = profile / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    (profile / "Default" / "Cookies").write_bytes(b"stored sid=secret")
    (profile / "Default" / "Login Data").write_bytes(b"stored login")
    (profile / "Local Storage" / "leveldb").mkdir(parents=True, exist_ok=True)
    (profile / "Local Storage" / "leveldb" / "000003.log").write_bytes(b"state")
    return profile


def test_status_separates_the_regenerable_cache_from_the_profile(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))

    state = cache.browser_cache_state()

    assert state["present"] is True
    assert state["in_use"] is False
    assert state["bytes"] == 4096 + 2048 + 1024
    assert set(state["paths"]) == {
        str(profile / "Default" / "Cache"),
        str(profile / "Default" / "Code Cache"),
        str(profile / "GraphiteDawnCache"),
    }


def test_pruning_keeps_every_stored_session(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))

    report = cache.clear_browser_cache()

    assert report["skipped_in_use"] is False
    assert report["freed_bytes"] == 4096 + 2048 + 1024
    assert not (profile / "Default" / "Cache").exists()
    assert not (profile / "Default" / "Code Cache").exists()
    assert (profile / "Default" / "Cookies").read_bytes() == b"stored sid=secret"
    assert (profile / "Default" / "Login Data").read_bytes() == b"stored login"
    assert (profile / "Local Storage" / "leveldb" / "000003.log").exists()


def test_a_running_browser_keeps_its_cache(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))
    lock = profile / "SingletonLock"
    try:
        os.symlink(f"host-{os.getpid()}", lock)
    except (OSError, NotImplementedError):
        lock.write_text(str(os.getpid()))

    report = cache.clear_browser_cache()

    assert report["skipped_in_use"] is True
    assert report["freed_bytes"] == 0
    assert (profile / "Default" / "Cache" / "data_0").exists()


def test_prune_cache_reclaims_the_browser_cache_too(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))
    materials = tmp_path / "materials"
    materials.mkdir()

    report = cache.prune_cache(materials, home=tmp_path)

    assert report["browser_cache"]["freed_bytes"] == 4096 + 2048 + 1024
    assert profile / "Default" / "Cookies" in {p for p in profile.rglob("Cookies")}
    assert not (profile / "Default" / "Cache").exists()


def test_a_dry_run_counts_the_same_bytes_and_deletes_nothing(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("ENVOY_BROWSER_PROFILE_DIR", str(profile))

    report = cache.prune_cache(tmp_path / "materials", dry_run=True, home=tmp_path)

    assert report["freed_bytes"] == 4096 + 2048 + 1024
    assert (profile / "Default" / "Cache" / "data_0").exists()
