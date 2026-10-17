import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_download_video_rejects_non_http_source():
    from app.services.video_source_service import VideoSourceError, validate_video_url

    try:
        validate_video_url("file:///tmp/video.mp4")
    except VideoSourceError as exc:
        assert str(exc) == "Video URL must start with http:// or https://"
    else:
        raise AssertionError("expected invalid video URL to be rejected")


def test_download_video_uses_ytdlp_and_returns_metadata(tmp_path):
    from app.services.video_source_service import download_video

    output_file = tmp_path / "video.mp4"
    output_file.write_bytes(b"video")
    metadata = subprocess.CompletedProcess([], 0, '{"id":"abc123","title":"Demo","duration":12.5,"ext":"mp4"}\n', "")
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch("app.services.video_source_service.subprocess.run", side_effect=[metadata, downloaded]) as run:
        result = download_video("https://example.com/watch?v=abc123", tmp_path)

    assert result["video_id"] == "abc123"
    assert result["title"] == "Demo"
    assert result["duration_sec"] == 12.5
    assert result["path"].endswith(".mp4")
    command = run.call_args_list[0].args[0]
    assert command[0] in {"yt-dlp", sys.executable}
    if command[0] == sys.executable:
        assert command[1:3] == ["-m", "yt_dlp"]
    assert "--ignore-config" in command
    assert "--dump-single-json" in command
    assert "--no-playlist" in command
    assert "https://example.com/watch?v=abc123" in command
    assert run.call_count == 2


def test_download_video_preserves_raw_ytdlp_error(tmp_path):
    from app.services.video_source_service import VideoSourceError, download_video

    error = subprocess.CalledProcessError(
        1,
        ["yt-dlp"],
        stderr="yt-dlp: ERROR: Sign in to confirm you’re not a bot",
    )
    with patch("app.services.video_source_service.subprocess.run", side_effect=error):
        try:
            download_video("https://example.com/watch?v=blocked", tmp_path)
        except VideoSourceError as exc:
            assert str(exc) == "yt-dlp: ERROR: Sign in to confirm you’re not a bot"
        else:
            raise AssertionError("expected downloader error to be surfaced")


def test_download_video_can_read_browser_cookies(tmp_path):
    from app.services.video_source_service import download_video

    output_file = tmp_path / "video.mp4"
    output_file.write_bytes(b"video")
    metadata = subprocess.CompletedProcess([], 0, '{"id":"abc123","title":"Demo","duration":12.5}\n', "")
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch("app.services.video_source_service.subprocess.run", side_effect=[metadata, downloaded]) as run:
        download_video(
            "https://www.bilibili.com/video/BV1demo",
            tmp_path,
            auth={"mode": "browser", "browser": "chrome"},
        )

    first_command = run.call_args_list[0].args[0]
    assert "--cookies-from-browser" in first_command
    assert first_command[first_command.index("--cookies-from-browser") + 1] == "chrome"


def test_download_video_auto_falls_back_from_public_to_browser_cookies(tmp_path):
    from app.services.video_source_service import download_video

    output_file = tmp_path / "video.mp4"
    output_file.write_bytes(b"video")
    public_error = subprocess.CalledProcessError(1, ["yt-dlp"], stderr="HTTP Error 403: Forbidden")
    metadata = subprocess.CompletedProcess([], 0, '{"id":"abc123","title":"Demo","duration":12.5}\n', "")
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch(
        "app.services.video_source_service.subprocess.run",
        side_effect=[public_error, metadata, downloaded],
    ) as run:
        result = download_video("https://www.douyin.com/video/abc123", tmp_path, auth={"mode": "auto"})

    assert result["video_id"] == "abc123"
    browser_command = run.call_args_list[1].args[0]
    assert "--cookies-from-browser" in browser_command
    assert browser_command[browser_command.index("--cookies-from-browser") + 1] == "chrome"


def test_discover_video_playlist_returns_selectable_video_items(monkeypatch):
    import json
    from app.services.video_source_service import discover_video_items

    playlist_json = json.dumps({
        "title": "示例 UP 主",
        "extractor_key": "BiliBili",
        "entries": [
            {"id": "BV1example", "title": "第一条视频", "url": "BV1example", "duration": 32},
            {"id": "BV2example", "title": "第二条视频", "webpage_url": "https://www.bilibili.com/video/BV2example"},
        ],
    })
    completed = subprocess.CompletedProcess([], 0, playlist_json, "")
    monkeypatch.setattr("app.services.video_source_service.subprocess.run", lambda *args, **kwargs: completed)

    result = discover_video_items("https://space.bilibili.com/123456")

    assert result["creator_title"] == "示例 UP 主"
    assert [item["title"] for item in result["items"]] == ["第一条视频", "第二条视频"]
    assert result["items"][0]["source_url"] == "https://www.bilibili.com/video/BV1example"


def test_classify_video_source_covers_major_platforms_and_source_kinds():
    from app.services.video_source_service import classify_video_source

    cases = [
        ("https://www.youtube.com/@creator/videos", "youtube", "creator"),
        ("https://youtu.be/demo", "youtube", "video"),
        ("https://www.bilibili.com/video/BV1demo", "bilibili", "video"),
        ("https://b23.tv/demo", "bilibili", "share"),
        ("https://v.douyin.com/example/", "douyin", "share"),
        ("https://vimeo.com/showcase/123", "vimeo", "collection"),
        ("https://vimeo.com/123456", "vimeo", "video"),
        ("https://www.twitch.tv/example/videos", "twitch", "creator"),
        ("https://www.instagram.com/example/reels/", "instagram", "creator"),
        ("https://x.com/example/status/123", "x", "video"),
        ("https://www.kuaishou.com/profile/abc", "kuaishou", "creator"),
        ("https://www.ixigua.com/123", "ixigua", "video"),
    ]

    for url, platform, source_kind in cases:
        result = classify_video_source(url)
        assert result["platform"] == platform
        assert result["source_kind"] == source_kind


def test_discover_video_playlist_includes_source_metadata(monkeypatch):
    import json
    from app.services.video_source_service import discover_video_items

    playlist_json = json.dumps({
        "title": "示例频道",
        "extractor_key": "Youtube",
        "entries": [{
            "id": "demo",
            "title": "Demo",
            "url": "demo",
            "duration": 12,
            "upload_date": "20261017",
            "timestamp": 1792195200,
            "thumbnail": "https://img.example/demo.jpg",
        }],
    })
    completed = subprocess.CompletedProcess([], 0, playlist_json, "")
    monkeypatch.setattr("app.services.video_source_service.subprocess.run", lambda *args, **kwargs: completed)

    result = discover_video_items("https://www.youtube.com/@creator/videos")

    assert result["platform"] == "youtube"
    assert result["source_kind"] == "creator"
    assert result["discovery_method"] == "ytdlp"
    assert result["items"][0]["published_at"] == 1792195200
    assert result["items"][0]["thumbnail"] == "https://img.example/demo.jpg"


def test_playlist_discovery_does_not_try_browser_cookies_for_unsupported_urls():
    from app.services.video_source_service import VideoSourceError, discover_video_items

    error = subprocess.CalledProcessError(1, ["yt-dlp"], stderr="ERROR: Unsupported URL")
    with patch("app.services.video_source_service.subprocess.run", side_effect=error) as run:
        try:
            discover_video_items("https://v.douyin.com/example/", auth={"mode": "auto"})
        except VideoSourceError as exc:
            assert "Unsupported URL" in str(exc)
        else:
            raise AssertionError("expected unsupported URL to be reported")

    assert run.call_count == 1


def test_playlist_discovery_uses_browser_cookies_after_auth_error(monkeypatch):
    import json
    from app.services.video_source_service import discover_video_items

    unauthorized = subprocess.CalledProcessError(1, ["yt-dlp"], stderr="HTTP Error 403: Forbidden")
    playlist = subprocess.CompletedProcess([], 0, json.dumps({
        "title": "Creator",
        "extractor_key": "BiliBili",
        "entries": [{"id": "BV1demo", "title": "Demo", "url": "BV1demo"}],
    }), "")
    with patch("app.services.video_source_service.subprocess.run", side_effect=[unauthorized, playlist]) as run:
        result = discover_video_items("https://space.bilibili.com/123", auth={"mode": "auto"})

    assert result["item_count"] == 1
    assert run.call_count == 2
    assert "--cookies-from-browser" in run.call_args_list[1].args[0]


def test_classify_discovery_error_distinguishes_user_action_from_unsupported():
    from app.services.video_source_service import classify_discovery_error

    auth = classify_discovery_error("ERROR: Sign in to confirm you are not a bot")
    assert auth["reason_code"] == "authentication_required"
    assert auth["requires_user_action"] is True

    challenge = classify_discovery_error("captcha challenge required")
    assert challenge["reason_code"] == "challenge_required"
    assert challenge["requires_user_action"] is True

    rejected = classify_discovery_error("ERROR: Request is rejected by server (352)")
    assert rejected["reason_code"] == "challenge_required"
    assert rejected["requires_user_action"] is True

    unsupported = classify_discovery_error("ERROR: Unsupported URL")
    assert unsupported["reason_code"] == "unsupported_url"
    assert unsupported["requires_user_action"] is False


def test_playlist_discovery_api_returns_user_action_contract(monkeypatch):
    from fastapi import HTTPException
    from app.api.v1 import tasks
    from app.schemas.video_batch import VideoPlaylistDiscover
    from app.services.video_source_service import VideoSourceError

    monkeypatch.setattr(
        tasks,
        "discover_video_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            VideoSourceError("ERROR: Sign in to confirm you are not a bot")
        ),
    )

    try:
        tasks.discover_video_playlist(VideoPlaylistDiscover(
            source_url="https://www.youtube.com/@creator/videos",
            limit=10,
        ))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["requires_user_action"] is True
        assert exc.detail["platform"] == "youtube"
    else:
        raise AssertionError("expected a user-action discovery response")


def test_video_batch_item_accepts_local_asset_without_source_url():
    from app.schemas.video_batch import VideoBatchItem

    item = VideoBatchItem(asset_id="asset-local-1", title="本地视频")

    assert item.asset_id == "asset-local-1"
    assert item.source_url is None


def test_video_batch_item_rejects_missing_or_ambiguous_source():
    from pydantic import ValidationError
    from app.schemas.video_batch import VideoBatchItem

    for payload in ({}, {"asset_id": "asset-1", "source_url": "https://example.test/v"}):
        try:
            VideoBatchItem(**payload)
        except ValidationError:
            continue
        raise AssertionError("expected exactly one video source")


def test_video_source_service_recognizes_common_local_video_extensions():
    from app.api.v1.assets import _is_supported_video_filename

    for filename in ("clip.mp4", "clip.mov", "clip.mkv", "clip.webm", "clip.avi", "clip.m4v", "clip.ts"):
        assert _is_supported_video_filename(filename)
    assert not _is_supported_video_filename("notes.txt")
