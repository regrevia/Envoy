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


def test_download_video_can_read_netscape_cookie_file(tmp_path):
    from app.services.video_source_service import download_video

    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n", encoding="utf-8")
    output_file = tmp_path / "video.mp4"
    output_file.write_bytes(b"video")
    metadata = subprocess.CompletedProcess([], 0, '{"id":"abc123","title":"Demo","duration":12.5}\n', "")
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch("app.services.video_source_service.subprocess.run", side_effect=[metadata, downloaded]) as run:
        download_video(
            "https://example.com/watch?v=abc123",
            tmp_path,
            auth={"mode": "cookies_file", "path": str(cookie_path)},
        )

    first_command = run.call_args_list[0].args[0]
    assert "--cookies" in first_command
    assert first_command[first_command.index("--cookies") + 1] == str(cookie_path.resolve())


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
