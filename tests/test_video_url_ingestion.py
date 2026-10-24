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


def test_bilibili_identifiers_are_normalized_to_video_urls():
    from app.services.video_source_service import validate_video_url

    assert validate_video_url("BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert validate_video_url("av170001") == "https://www.bilibili.com/video/av170001"
    assert validate_video_url(" https://www.bilibili.com/video/BV1xx411c7mD ") == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def test_bilibili_short_share_link_follows_redirect(monkeypatch):
    from types import SimpleNamespace
    from app.services.video_source_service import resolve_video_url

    monkeypatch.setattr(
        "app.services.video_source_service.httpx.get",
        lambda *args, **kwargs: SimpleNamespace(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            raise_for_status=lambda: None,
        ),
    )

    assert resolve_video_url("https://b23.tv/AbCdEf") == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def test_known_platforms_enable_browser_fallback_even_when_ytdlp_is_blocked():
    from app.services.video_source_service import classify_video_source

    for url in (
        "https://vimeo.com/123456",
        "https://www.twitch.tv/videos/123456",
        "https://www.youku.com/v_show/id_demo.html",
        "https://v.qq.com/x/page/demo.html",
    ):
        assert classify_video_source(url)["supports_browser_discovery"] is True


def test_dailymotion_video_is_classified():
    from app.services.video_source_service import classify_video_source

    source = classify_video_source("https://www.dailymotion.com/video/x26m1j4")
    assert source["platform"] == "dailymotion"
    assert source["source_kind"] == "video"


def test_generic_direct_video_and_stream_urls_are_marked_downloadable():
    from app.services.video_source_service import classify_video_source

    cases = [
        ("https://cdn.example.test/lesson.mp4?token=short", "direct_media"),
        ("https://cdn.example.test/master.m3u8", "direct_media"),
        ("https://cdn.example.test/manifest.mpd", "direct_media"),
        ("https://cdn.example.test/lesson.webm", "direct_media"),
    ]
    for url, expected_kind in cases:
        source = classify_video_source(url)
        assert source["platform"] == "unknown"
        assert source["source_kind"] == expected_kind
        assert source["supports_ytdlp"] is True


def test_douyin_note_is_classified_as_image_text_content():
    from app.services.video_source_service import classify_video_source

    source = classify_video_source("https://www.douyin.com/note/7682946325957568933")

    assert source["platform"] == "douyin"
    assert source["source_kind"] == "image_text"
    assert source["supports_ytdlp"] is False
    assert source["supports_browser_discovery"] is False


def test_wechat_public_article_and_unrecognized_public_pages_are_auto_routed():
    from app.services.video_source_service import classify_video_source

    wechat = classify_video_source("https://mp.weixin.qq.com/s/example-article")
    generic = classify_video_source("https://articles.example.test/guides/topic")

    assert wechat["platform"] == "wechat"
    assert wechat["source_kind"] == "article"
    assert generic["platform"] == "unknown"
    assert generic["source_kind"] == "public_page"


def test_video_task_endpoint_routes_douyin_notes_to_public_page_pipeline(monkeypatch):
    from app.api.v1 import tasks
    from app.schemas.video_task import VideoToMarkdownCreate

    captured = {}

    def create_public_page_task(db, project_id, source_url, title, input_config):
        captured.update({
            "project_id": project_id,
            "source_url": source_url,
            "title": title,
            "input_config": input_config,
        })
        return {"task_type": "public_page_to_markdown"}

    monkeypatch.setattr(tasks, "_create_public_page_task", create_public_page_task)

    result = tasks.create_video_to_markdown_task(
        VideoToMarkdownCreate(
            project_id="project-1",
            source_url="https://www.douyin.com/note/7682946325957568933",
            input_config={"output_formats": ["markdown", "html"]},
        ),
        db=None,
    )

    assert result["task_type"] == "public_page_to_markdown"
    assert captured["project_id"] == "project-1"
    assert captured["source_url"] == "https://www.douyin.com/note/7682946325957568933"
    assert captured["input_config"]["output_formats"] == ["markdown", "html"]


def test_missing_video_is_not_classified_as_login_required():
    from app.services.video_source_service import classify_discovery_error

    result = classify_discovery_error("ERROR: HTTP Error 404: Not Found")
    assert result["reason_code"] == "source_not_found"
    assert result["requires_user_action"] is False


def test_youtube_uses_android_client_fallback_args():
    from app.services.video_source_service import _platform_download_args

    args = _platform_download_args("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "--extractor-args" in args
    assert "youtube:player_client=android" in args
    assert "--force-ipv4" in args


def test_unknown_websites_are_eligible_for_generic_browser_fallback():
    from app.services.video_source_service import _is_browser_recoverable_error

    assert _is_browser_recoverable_error("Unsupported URL: https://video.example.test/watch/1")
    assert _is_browser_recoverable_error("No video entries were found at this URL")


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
    assert command[0] in {"yt-dlp", sys.executable} or command[0].endswith("/yt-dlp")
    if command[0] == sys.executable:
        assert command[1:3] == ["-m", "yt_dlp"]
    assert "--ignore-config" in command
    assert "--dump-single-json" in command
    assert "--no-playlist" in command
    assert "https://example.com/watch?v=abc123" in command
    assert run.call_count == 2
    download_command = run.call_args_list[1].args[0]
    assert download_command[download_command.index("--format") + 1] == "bestvideo*+bestaudio/best"


def test_download_video_limits_generic_streams_to_720p_and_retries_fragments(tmp_path):
    from app.services.video_source_service import download_video

    output_file = tmp_path / "stream.mp4"
    output_file.write_bytes(b"video")
    metadata = subprocess.CompletedProcess(
        [], 0, '{"id":"stream","title":"Stream","duration":12.5,"ext":"mp4"}\n', ""
    )
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch("app.services.video_source_service.subprocess.run", side_effect=[metadata, downloaded]) as run:
        download_video("https://cdn.example.test/master.m3u8", tmp_path)

    command = run.call_args_list[1].args[0]
    assert command[command.index("--format") + 1] == "best[height<=720]/best"
    assert command[command.index("--socket-timeout") + 1] == "20"
    assert command[command.index("--retries") + 1] == "2"
    assert command[command.index("--fragment-retries") + 1] == "2"


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


def test_download_video_can_read_browser_cookies_for_douyin(tmp_path):
    from app.services.video_source_service import download_video

    output_file = tmp_path / "video.mp4"
    output_file.write_bytes(b"video")
    metadata = subprocess.CompletedProcess([], 0, '{"id":"abc123","title":"Demo","duration":12.5}\n', "")
    downloaded = subprocess.CompletedProcess([], 0, f"{output_file}\n", "")

    with patch("app.services.video_source_service.subprocess.run", side_effect=[metadata, downloaded]) as run:
        download_video(
            "https://www.douyin.com/video/abc123",
            tmp_path,
            auth={"mode": "browser", "browser": "chrome"},
        )

    command = run.call_args_list[0].args[0]
    assert "--cookies-from-browser" in command
    assert command[command.index("--cookies-from-browser") + 1] == "chrome"


def test_download_video_uses_active_browser_media_for_browser_session(tmp_path, monkeypatch):
    from app.services.video_source_service import download_video

    expected = {
        "path": str(tmp_path / "douyin.mp4"),
        "video_id": "7681899614340910376",
        "title": "浏览器视频",
        "duration_sec": 12.0,
        "extractor": "browser_session",
        "webpage_url": "https://www.douyin.com/video/7681899614340910376",
    }
    monkeypatch.setattr(
        "app.services.video_source_service.download_browser_session_video",
        lambda session_id, source_url, output_dir: expected,
    )

    result = download_video(
        "https://www.douyin.com/video/7681899614340910376",
        tmp_path,
        auth={"mode": "browser_session", "session_id": "session-1"},
    )

    assert result["extractor"] == "browser_session"
    assert result["video_id"] == "7681899614340910376"


def test_browser_cookie_export_uses_zero_for_session_cookie_expiry():
    from app.services.browser_session_service import _netscape_cookie_line

    line = _netscape_cookie_line({
        "domain": "www.douyin.com",
        "path": "/",
        "name": "session_cookie",
        "value": "value",
        "secure": False,
        "expires": -1,
    })

    assert line == "www.douyin.com\tFALSE\t/\tFALSE\t0\tsession_cookie\tvalue\n"


def test_browser_media_capture_keeps_dynamic_douyin_media_urls():
    from app.services.browser_worker import _media_url_candidates

    class FakeVideoLocator:
        def evaluate_all(self, _script):
            return ["blob:https://www.douyin.com/video"]

    class FakePage:
        def locator(self, _selector):
            return FakeVideoLocator()

        def evaluate(self, _script):
            return ["https://v26-web.douyinvod.com/segment?mime=video/mp4"]

    result = _media_url_candidates(
        FakePage(),
        [],
        [{"url": "https://v26-web.douyinvod.com/segment?id=1", "content_type": "video/mp4"}],
    )

    assert result == [
        "https://v26-web.douyinvod.com/segment?id=1",
        "https://v26-web.douyinvod.com/segment?mime=video/mp4",
    ]


def test_browser_media_capture_accepts_video_content_type_from_unlisted_cdn():
    from app.services.browser_worker import _media_url_candidates

    class FakePage:
        def locator(self, _selector):
            raise RuntimeError("no video element src")

        def evaluate(self, _script):
            return []

    result = _media_url_candidates(
        FakePage(),
        [],
        [{"url": "https://v99-unlisted-cdn.example/stream?id=1", "content_type": "video/mp2t"}],
    )

    assert result == ["https://v99-unlisted-cdn.example/stream?id=1"]


def test_browser_media_capture_reads_html5_sources_and_video_meta_tags():
    from app.services.browser_worker import _media_url_candidates

    class FakeLocator:
        def evaluate_all(self, script):
            if "video" in script and "currentSrc" in script:
                return []
            return ["https://cdn.example.test/video.mp4"]

    class FakePage:
        def locator(self, selector):
            if selector == "video":
                return FakeLocator()
            if selector == "video source":
                return FakeLocator()
            if selector.startswith("meta"):
                return FakeLocator()
            raise RuntimeError(selector)

        def evaluate(self, script):
            return []

    result = _media_url_candidates(FakePage(), [], [])
    assert result == ["https://cdn.example.test/video.mp4"]


def test_browser_media_capture_ignores_generic_aweme_json_and_finds_play_addr():
    from app.services.browser_worker import _video_urls_from_payload, _media_url_candidates

    class FakePage:
        def locator(self, _selector):
            raise RuntimeError("video source is a blob URL")

        def evaluate(self, _script):
            return []

    payload = {
        "aweme_detail": {
            "video": {
                "play_addr": {
                    "url_list": ["https://v26-web.douyinvod.com/play?video_id=123"],
                },
            },
        },
    }
    extracted = _video_urls_from_payload(payload)
    result = _media_url_candidates(
        FakePage(),
        [{"url": "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123", "resource_type": "xhr"}],
        [{"url": "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=123", "content_type": "application/json", "resource_type": "xhr"}],
        extracted,
    )

    assert result == ["https://v26-web.douyinvod.com/play?video_id=123"]


def test_browser_session_download_request_includes_output_path(tmp_path, monkeypatch):
    from app.services.browser_session_service import BrowserSessionManager

    manager = BrowserSessionManager()
    captured = {}
    monkeypatch.setattr(manager, "_request", lambda session_id, command, payload=None: captured.update({
        "session_id": session_id,
        "command": command,
        "payload": payload,
    }) or {"status": "downloaded", "path": str(tmp_path / "browser-session.mp4"), "title": "Demo"})
    manager._sessions["session-1"] = object()

    result = manager.download_media("session-1", "https://www.douyin.com/video/123", str(tmp_path))

    assert result["extractor"] == "browser_session"
    assert captured == {
        "session_id": "session-1",
        "command": "download_media",
        "payload": {"output_path": str(tmp_path / "browser-session.mp4")},
    }


def test_download_video_auto_falls_back_from_public_to_browser_cookies(tmp_path, monkeypatch):
    from app.services.video_source_service import download_video

    monkeypatch.setattr(
        "app.services.video_source_service._browser_cookie_store_exists",
        lambda browser: browser == "chrome",
    )
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


def test_download_video_auto_opens_ephemeral_browser_after_auth_failure(tmp_path, monkeypatch):
    from app.services.video_source_service import download_video

    public_error = subprocess.CalledProcessError(
        1,
        ["yt-dlp"],
        stderr="ERROR: [Douyin] Fresh cookies are needed",
    )
    browser_result = {
        "path": str(tmp_path / "browser.mp4"),
        "video_id": "abc123",
        "title": "Browser demo",
        "extractor": "browser_session",
        "webpage_url": "https://www.douyin.com/video/abc123",
    }
    manager = type("Manager", (), {
        "create": lambda self, source_url, limit=1: {"session_id": "ephemeral-1"},
        "download_media": lambda self, session_id, source_url, output_dir: browser_result,
        "close": lambda self, session_id: {"status": "closed"},
    })()
    monkeypatch.setattr("app.services.video_source_service._browser_cookie_store_exists", lambda browser: False)
    monkeypatch.setattr("app.services.browser_session_service.browser_session_manager", manager)

    with patch(
        "app.services.video_source_service.subprocess.run",
        side_effect=[public_error],
    ):
        result = download_video(
            "https://www.douyin.com/video/abc123",
            tmp_path,
            auth={"mode": "auto"},
        )

    assert result["extractor"] == "browser_session"
    assert result["video_id"] == "abc123"


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


def test_douyin_user_modal_link_is_normalized_to_video_source():
    from app.services.video_source_service import classify_video_source, validate_video_url

    source = "https://www.douyin.com/user/creator?modal_id=7681899614340910376"

    assert validate_video_url(source) == "https://www.douyin.com/video/7681899614340910376"
    result = classify_video_source(source)
    assert result["source_url"] == "https://www.douyin.com/video/7681899614340910376"
    assert result["source_kind"] == "video"


def test_douyin_share_link_resolves_to_final_video_url(monkeypatch):
    from types import SimpleNamespace
    from app.services.video_source_service import resolve_video_url

    monkeypatch.setattr(
        "app.services.video_source_service.httpx.get",
        lambda *args, **kwargs: SimpleNamespace(
            url="https://www.douyin.com/user/creator?modal_id=7681899614340910376",
            raise_for_status=lambda: None,
        ),
    )

    assert resolve_video_url("https://v.douyin.com/EpZMA6dLSbE/") == (
        "https://www.douyin.com/video/7681899614340910376"
    )


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

    monkeypatch.setattr(
        "app.services.video_source_service._browser_cookie_store_exists",
        lambda browser: browser == "chrome",
    )
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


def test_auto_auth_only_tries_browsers_with_existing_cookie_stores(tmp_path, monkeypatch):
    from app.services.video_source_service import _auth_candidates

    chrome_cookie_db = tmp_path / "Library/Application Support/Google/Chrome/Default/Cookies"
    safari_cookie_db = tmp_path / "Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"
    chrome_cookie_db.parent.mkdir(parents=True)
    safari_cookie_db.parent.mkdir(parents=True)
    chrome_cookie_db.touch()
    safari_cookie_db.touch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ENVOY_ALLOW_SAFARI_COOKIES", raising=False)

    assert _auth_candidates({"mode": "auto"}) == [
        {},
        {"mode": "browser", "browser": "chrome"},
    ]


def test_auto_auth_does_not_probe_absent_browser_profiles(tmp_path, monkeypatch):
    from app.services.video_source_service import _auth_candidates

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("ENVOY_ALLOW_SAFARI_COOKIES", raising=False)

    assert _auth_candidates({"mode": "auto"}) == [{}]


def test_ytdlp_error_cleanup_removes_runtime_and_cookie_profile_noise():
    from app.services.video_source_service import _clean_ytdlp_error

    error = "\n".join([
        "NotOpenSSLWarning: urllib3 v2 only supports OpenSSL",
        "warnings.warn(Deprecated Feature)",
        "Deprecated Feature: Support for Python version 3.9 has been deprecated",
        "ERROR: [Errno 1] Operation not permitted: '/Users/test/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies'",
        "ERROR: could not find brave cookies database in '/Users/test/Library/Application Support/BraveSoftware/Brave-Browser'",
        "ERROR: [VideoSite] Sign in to confirm you are not a bot",
    ])

    cleaned = _clean_ytdlp_error(error)

    assert "NotOpenSSLWarning" not in cleaned
    assert "Deprecated Feature" not in cleaned
    assert "Cookies.binarycookies" not in cleaned
    assert "could not find brave cookies database" not in cleaned
    assert "Sign in to confirm you are not a bot" in cleaned


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
