import json
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
