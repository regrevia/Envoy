"""Kept media must be short-lived, discoverable and reclaimable.

A caller that asked for a video expects the file it just read about to be
inspectable, and expects it to disappear on its own once it is not. That lease
lives in a ``.expires`` sidecar inside the media directory, so both
``envoy cache prune`` and the sweep after every read agree on the deadline.
"""

from __future__ import annotations

import time
from pathlib import Path

from envoy_cli import cache, reader

MATERIAL_ID = "mat-" + "a" * 24


def _materials(root: Path) -> Path:
    """The material root: kept media lives directly in it, beside the files."""

    return root


def _live_material(root: Path, material_id: str = MATERIAL_ID, ttl: int = 86_400) -> None:
    """Keep a material alive so pruning does not treat its media as an orphan."""

    directory = _materials(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{material_id}.json").write_text("{}", encoding="utf-8")
    (directory / f"{material_id}.expires").write_text(
        f"{time.time() + ttl:.3f}", encoding="ascii"
    )


def _media_dir(root: Path, material_id: str = MATERIAL_ID) -> Path:
    directory = _materials(root) / f"video-{material_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "video.mp4").write_bytes(b"x" * 16)
    return directory


def test_a_kept_media_directory_records_its_deadline(tmp_path: Path):
    directory = tmp_path / ("video-mat-" + "b" * 24)
    reader._write_media_deadline(directory, 60)

    deadline = reader._media_deadline(directory)
    assert deadline is not None, "the deadline sidecar was not written"
    remaining = deadline - time.time()
    assert 55 < remaining <= 61, remaining
    assert reader._media_deadline_iso(directory) is not None


def test_pruning_removes_media_past_its_deadline(tmp_path: Path):
    directory = _media_dir(tmp_path)
    _live_material(tmp_path)
    reader._write_media_deadline(directory, 1)

    report = cache.prune_cache(root=tmp_path, now=time.time() + 2)

    assert not directory.exists()
    assert any(entry["path"].endswith(directory.name) for entry in report["removed"])
    assert report["freed_bytes"] >= 16


def test_pruning_keeps_media_that_is_still_leased(tmp_path: Path):
    directory = _media_dir(tmp_path)
    _live_material(tmp_path)
    reader._write_media_deadline(directory, 3600)

    cache.prune_cache(root=tmp_path, now=time.time())

    assert directory.is_dir()
    assert (directory / "video.mp4").is_file()


def test_pruning_removes_an_empty_media_directory(tmp_path: Path):
    _live_material(tmp_path)
    directory = _materials(tmp_path) / ("video-mat-" + "c" * 24)
    directory.mkdir()

    cache.prune_cache(root=tmp_path, now=time.time())

    assert not directory.exists()


def test_pruning_never_follows_a_media_symlink(tmp_path: Path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("do not delete", encoding="utf-8")
    _live_material(tmp_path)
    link = _materials(tmp_path) / ("video-mat-" + "d" * 24)
    link.symlink_to(target)

    cache.prune_cache(root=tmp_path, now=time.time())

    assert not link.exists()
    assert (target / "keep.txt").is_file()


def test_the_default_retention_keeps_media_briefly():
    from envoy_core.schemas.read_v1 import Retention

    retention = Retention()
    assert retention.raw_media == "session"
    assert retention.derived_ttl_seconds > 0
    assert reader.MEDIA_RETENTION_SECONDS <= 3600


def test_an_expired_media_lease_is_reclaimed_after_a_read(tmp_path, monkeypatch):
    directory = _media_dir(tmp_path)
    reader._write_media_deadline(directory, 1)
    monkeypatch.setattr(reader, "_material_root", lambda: _materials(tmp_path))
    monkeypatch.setattr(reader, "_media_deadline", lambda _directory: time.time() - 1)

    reader._reclaim_expired_media()

    assert not directory.exists()
