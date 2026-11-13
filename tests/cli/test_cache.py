import json
import os
import time
from pathlib import Path

from envoy_cli import cache
from envoy_cli import cli

LIVE = "mat-" + "a" * 24
EXPIRED = "mat-" + "b" * 24
ORPHAN = "mat-" + "c" * 24


def _material(root: Path, material_id: str, *, expires_in: float) -> tuple[Path, Path]:
    body = root / f"{material_id}.json"
    expiry = root / f"{material_id}.expires"
    body.write_text(json.dumps({"material_id": material_id}), encoding="utf-8")
    expiry.write_text(str(time.time() + expires_in), encoding="ascii")
    return body, expiry


def _operation(root: Path, digest: str, *, expires_in: float) -> Path:
    path = root / f"operation-{digest}.json"
    path.write_text(json.dumps({"expires_at": time.time() + expires_in}), encoding="utf-8")
    return path


def test_status_counts_expired_materials_and_caches(tmp_path):
    _material(tmp_path, LIVE, expires_in=3600)
    _material(tmp_path, EXPIRED, expires_in=-60)
    (tmp_path / f"{ORPHAN}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ocr-inputs").mkdir()
    (tmp_path / "ocr-inputs" / "image.jpg").write_bytes(b"x" * 32)
    (tmp_path / "ocr-cache").mkdir()
    (tmp_path / "ocr-cache" / "result.json").write_text("{}", encoding="utf-8")
    _operation(tmp_path, "a" * 64, expires_in=-60)
    _operation(tmp_path, "b" * 64, expires_in=3600)
    live_frames = tmp_path / f"frames-{LIVE}"
    live_frames.mkdir()
    (live_frames / "frame.jpg").write_bytes(b"y" * 16)
    (tmp_path / f"frames-{ORPHAN}").mkdir()

    report = cache.cache_status(tmp_path, home=tmp_path / "home")

    categories = report["categories"]
    assert categories["materials"]["count"] == 2
    assert categories["materials"]["expired_count"] == 3
    assert categories["frames"] == {
        "count": 1,
        "bytes": 16,
        "orphan_count": 1,
        "orphan_bytes": 0,
    }
    assert categories["ocr_inputs"] == {"count": 1, "bytes": 32}
    assert categories["ocr_cache"]["count"] == 1
    assert categories["operations"]["count"] == 2
    assert categories["operations"]["expired_count"] == 1


def test_prune_removes_only_reclaimable_entries(tmp_path):
    live_body, live_expiry = _material(tmp_path, LIVE, expires_in=3600)
    expired_body, expired_expiry = _material(tmp_path, EXPIRED, expires_in=-60)
    orphan_body = tmp_path / f"{ORPHAN}.json"
    orphan_body.write_text("{}", encoding="utf-8")
    (tmp_path / "ocr-inputs").mkdir()
    download = tmp_path / "ocr-inputs" / "image.jpg"
    download.write_bytes(b"x" * 32)
    (tmp_path / "ocr-cache").mkdir()
    cached = tmp_path / "ocr-cache" / "result.json"
    cached.write_text("{}", encoding="utf-8")
    spent = _operation(tmp_path, "a" * 64, expires_in=-60)
    live_operation = _operation(tmp_path, "b" * 64, expires_in=3600)
    frames = tmp_path / f"frames-{LIVE}"
    frames.mkdir()
    (frames / "frame.jpg").write_bytes(b"y" * 16)
    orphan_frames = tmp_path / f"frames-{ORPHAN}"
    orphan_frames.mkdir()
    (orphan_frames / "frame.jpg").write_bytes(b"y" * 8)
    profile = tmp_path / "home" / "browser-profile"
    profile.mkdir(parents=True)
    (profile / "chromium").write_bytes(b"z" * 4)

    report = cache.prune_cache(tmp_path)

    assert report["dry_run"] is False
    assert not expired_body.exists()
    assert not expired_expiry.exists()
    assert not orphan_body.exists()
    assert live_body.is_file()
    assert live_expiry.is_file()
    assert not download.exists()
    assert not cached.exists()
    assert not spent.exists()
    assert live_operation.is_file()
    assert frames.is_dir()
    assert not orphan_frames.exists()
    assert (profile / "chromium").is_file()
    assert report["freed_bytes"] > 0


def test_prune_dry_run_reports_without_deleting(tmp_path):
    expired_body, expired_expiry = _material(tmp_path, EXPIRED, expires_in=-60)

    report = cache.prune_cache(tmp_path, dry_run=True)

    assert report["dry_run"] is True
    assert len(report["removed"]) == 2
    assert report["freed_bytes"] > 0
    assert expired_body.is_file()
    assert expired_expiry.is_file()


def test_prune_unlinks_a_symlinked_material_without_following_it(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("keep me", encoding="utf-8")
    link = tmp_path / f"{EXPIRED}.json"
    link.symlink_to(target)
    expiry = tmp_path / f"{EXPIRED}.expires"
    expiry.write_text(str(time.time() - 60), encoding="ascii")

    cache.prune_cache(tmp_path)

    assert not link.exists()
    assert not expiry.exists()
    assert target.read_text(encoding="utf-8") == "keep me"


def test_status_tolerates_a_missing_root(tmp_path):
    report = cache.cache_status(tmp_path / "absent", home=tmp_path)

    assert report["total_bytes"] == 0
    assert report["categories"]["materials"]["count"] == 0


def test_cli_reports_and_prunes_the_configured_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache, "envoy_home", lambda: tmp_path / "home")
    _material(tmp_path, EXPIRED, expires_in=-60)

    assert cli.main(["cache", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["materials_root"] == str(tmp_path)
    assert status["categories"]["materials"]["expired_count"] == 2

    assert cli.main(["cache", "prune", "--dry-run"]) == 0
    assert "would remove 2 entries" in capsys.readouterr().out

    assert cli.main(["cache", "prune"]) == 0
    assert "removed 2 entries" in capsys.readouterr().out
    assert not (tmp_path / f"{EXPIRED}.json").exists()


def _aged(path: Path, *, seconds_ago: float) -> Path:
    path.write_text("{}", encoding="utf-8")
    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))
    return path


def test_status_accounts_for_shared_derivation_caches(tmp_path):
    index = tmp_path / cache.GITHUB_INDEX_DIR
    index.mkdir()
    _aged(index / "owner__repo__abc.json", seconds_ago=60)
    _aged(index / "owner__repo__def.json", seconds_ago=cache.GITHUB_INDEX_MAX_AGE_SECONDS + 60)
    bodies = tmp_path / cache.HTTP_CACHE_DIR
    bodies.mkdir()
    _aged(bodies / ("a" * 64 + ".json"), seconds_ago=60)
    _aged(bodies / ("." + "b" * 64 + ".lock"), seconds_ago=cache.HTTP_CACHE_MAX_AGE_SECONDS + 60)

    report = cache.cache_status(tmp_path)

    assert report["categories"]["github_index"]["count"] == 2
    assert report["categories"]["github_index"]["expired_count"] == 1
    assert report["categories"]["http_cache"]["count"] == 2
    assert report["categories"]["http_cache"]["expired_count"] == 1
    assert report["total_bytes"] > 0


def test_prune_reclaims_only_aged_derivation_cache_entries(tmp_path):
    index = tmp_path / cache.GITHUB_INDEX_DIR
    index.mkdir()
    fresh_index = _aged(index / "owner__repo__abc.json", seconds_ago=60)
    old_index = _aged(index / "owner__repo__def.json", seconds_ago=cache.GITHUB_INDEX_MAX_AGE_SECONDS + 60)
    bodies = tmp_path / cache.HTTP_CACHE_DIR
    bodies.mkdir()
    fresh_body = _aged(bodies / ("a" * 64 + ".json"), seconds_ago=60)
    old_body = _aged(bodies / ("c" * 64 + ".json"), seconds_ago=cache.HTTP_CACHE_MAX_AGE_SECONDS + 60)

    report = cache.prune_cache(tmp_path)

    assert not old_index.exists()
    assert not old_body.exists()
    assert fresh_index.is_file()
    assert fresh_body.is_file()
    assert report["freed_bytes"] > 0


def _populated_root(root: Path, *, home: Path) -> None:
    """A cache root with one live and one dead entry per category.

    ``cache status`` and ``cache prune`` must describe this identically, so the
    fixture has to exercise every category both of them know about.
    """

    _material(root, LIVE, expires_in=3600)
    _material(root, EXPIRED, expires_in=-60)
    (root / f"{ORPHAN}.json").write_text("{}", encoding="utf-8")
    (root / "ocr-inputs").mkdir()
    (root / "ocr-inputs" / "image.jpg").write_bytes(b"x" * 32)
    (root / "ocr-cache").mkdir()
    (root / "ocr-cache" / "result.json").write_text("{}", encoding="utf-8")
    _operation(root, "a" * 64, expires_in=-60)
    _operation(root, "b" * 64, expires_in=3600)
    live_frames = root / f"frames-{LIVE}"
    live_frames.mkdir()
    (live_frames / "frame.jpg").write_bytes(b"y" * 16)
    orphan_frames = root / f"frames-{ORPHAN}"
    orphan_frames.mkdir()
    (orphan_frames / "frame.jpg").write_bytes(b"y" * 8)
    live_media = root / f"video-{LIVE}"
    live_media.mkdir()
    (live_media / "clip.mp4").write_bytes(b"v" * 40)
    orphan_media = root / f"video-{ORPHAN}"
    orphan_media.mkdir()
    (orphan_media / "clip.mp4").write_bytes(b"v" * 24)
    index = root / cache.GITHUB_INDEX_DIR
    index.mkdir()
    (index / "recent.json").write_text("{}", encoding="utf-8")
    (index / "stale.json").write_text("{}", encoding="utf-8")
    _aged(index / "stale.json", seconds_ago=cache.GITHUB_INDEX_MAX_AGE_SECONDS + 60)
    http = root / cache.HTTP_CACHE_DIR
    http.mkdir()
    (http / "recent.bin").write_bytes(b"h" * 8)
    (http / "stale.bin").write_bytes(b"h" * 12)
    _aged(http / "stale.bin", seconds_ago=cache.HTTP_CACHE_MAX_AGE_SECONDS + 60)
    profile = home / "browser-profile"
    profile.mkdir(parents=True)
    (profile / "chromium").write_bytes(b"z" * 4)


def test_status_and_prune_agree_on_what_is_reclaimable(tmp_path):
    home = tmp_path / "home"
    _populated_root(tmp_path, home=home)

    status = cache.cache_status(tmp_path, home=home)
    dry = cache.prune_cache(tmp_path, dry_run=True, home=home)

    # The status must be able to predict the prune, category by category.
    assert status["reclaimable_files"] == len(dry["removed"])
    assert status["reclaimable_bytes"] == dry["freed_bytes"]
    for key, planned in status["reclaimable"].items():
        if key == "browser_cache":
            continue
        assert planned["files"] == dry["categories"][key]["removed_files"], key
        assert planned["bytes"] == dry["categories"][key]["removed_bytes"], key
    assert status["unreadable"] == dry["unreadable"]


def test_a_dry_run_counts_exactly_what_the_real_prune_removes(tmp_path):
    home = tmp_path / "home"
    _populated_root(tmp_path, home=home)

    dry = cache.prune_cache(tmp_path, dry_run=True, home=home)
    expected = {
        key: (row["removed_files"], row["removed_bytes"])
        for key, row in dry["categories"].items()
    }
    assert any(files for files, _ in expected.values())

    real = cache.prune_cache(tmp_path, home=home)

    assert real["dry_run"] is False
    assert real["freed_bytes"] == dry["freed_bytes"]
    assert len(real["removed"]) == len(dry["removed"])
    for key, (files, size) in expected.items():
        assert real["categories"][key]["removed_files"] == files, key
        assert real["categories"][key]["removed_bytes"] == size, key
        assert real["categories"][key]["kept_files"] >= 0
        assert real["categories"][key]["kept_bytes"] >= 0
    # What the dry run promised is gone; what it promised to keep survives.
    for entry in dry["removed"]:
        assert not Path(entry["path"]).exists(), entry["path"]
    assert (tmp_path / f"{LIVE}.json").is_file()
    assert (tmp_path / f"frames-{LIVE}").is_dir()
    assert (tmp_path / f"video-{LIVE}").is_dir()


def test_a_prune_names_the_reason_it_kept_an_entry(tmp_path):
    home = tmp_path / "home"
    _populated_root(tmp_path, home=home)

    report = cache.prune_cache(tmp_path, home=home)

    # Every kept entry is accounted for with one of the three documented words.
    known = {cache.KEPT_UNEXPIRED, cache.KEPT_LOCKED, cache.KEPT_UNREADABLE}
    for key, row in report["categories"].items():
        assert set(row["skipped"]) <= known, key
        assert row["kept_files"] == sum(
            bucket["files"] for bucket in row["skipped"].values()
        )
    assert report["categories"]["materials"]["skipped"][cache.KEPT_UNEXPIRED][
        "files"
    ] == 2


def test_a_prune_that_frees_nothing_still_reports_every_category(tmp_path):
    home = tmp_path / "home"
    _material(tmp_path, LIVE, expires_in=3600)

    report = cache.prune_cache(tmp_path, home=home)

    assert report["freed_bytes"] == 0
    assert report["removed"] == []
    assert set(report["categories"]) >= {
        "materials",
        "frames",
        "ocr_inputs",
        "ocr_cache",
        "video_media",
        "operations",
        "github_index",
        "http_cache",
    }


def test_status_walks_the_cache_exactly_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _material(tmp_path, EXPIRED, expires_in=-60)
    real_scandir = cache._scandir
    walked: list[Path] = []

    def counting(directory):
        walked.append(directory)
        return real_scandir(directory)

    monkeypatch.setattr(cache, "_scandir", counting)
    cache._enumerate_cache(tmp_path, time.time(), home)
    one_walk = len(walked)
    walked.clear()

    cache.cache_status(tmp_path, home=home)
    assert len(walked) == one_walk
