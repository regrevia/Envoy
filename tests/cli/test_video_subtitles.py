import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for path in (
    ROOT / "packages" / "envoy-core" / "src",
    ROOT / "packages" / "envoy-cli" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_webvtt_parser_keeps_caption_times_and_cleans_markup():
    from envoy_cli.video import parse_subtitle_document

    cues = parse_subtitle_document(
        b"WEBVTT\n\n00:00:01.200 --> 00:00:03.400\n<c.yellow>First step</c>\n\n"
        b"00:00:04.000 --> 00:00:05.500\nCheck &amp; save.\n",
        "vtt",
    )

    assert cues == [
        {"start": 1.2, "end": 3.4, "text": "First step"},
        {"start": 4.0, "end": 5.5, "text": "Check & save."},
    ]


def test_json3_parser_keeps_segment_times():
    from envoy_cli.video import parse_subtitle_document

    cues = parse_subtitle_document(
        b'{"events":[{"tStartMs":1250,"dDurationMs":2250,"segs":[{"utf8":"Open menu"}]},'
        b'{"tStartMs":4000,"dDurationMs":500,"segs":[{"utf8":"\\n"}]}]}',
        "json3",
    )

    assert cues == [{"start": 1.25, "end": 3.5, "text": "Open menu"}]


def test_video_extractor_reads_one_public_caption_without_downloading_media(monkeypatch):
    from envoy_cli import video

    calls = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            calls["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download):
            calls["extract"] = (url, download)
            return {
                "id": "sample",
                "title": "A public lesson",
                "duration": 12.0,
                "language": "en",
                "subtitles": {
                    "en": [{"ext": "vtt", "url": "https://cdn.example.org/manual.vtt"}],
                },
                "automatic_captions": {
                    "zh-Hans": [{"ext": "vtt", "url": "https://cdn.example.org/auto.vtt"}],
                },
            }

        def urlopen(self, url):
            calls["subtitle_url"] = url
            return SimpleNamespace(
                read=lambda limit: b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello world\n",
                close=lambda: None,
            )

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    result = video.extract_public_subtitles("https://www.youtube.com/watch?v=sample")

    assert calls["extract"] == ("https://www.youtube.com/watch?v=sample", False)
    assert calls["options"]["skip_download"] is True
    assert calls["options"]["cachedir"] is False
    assert result["status"] == "ready"
    assert result["language"] == "en"
    assert result["cues"][0]["start"] == 1.0


def test_video_extractor_honors_requested_subtitle_language(monkeypatch):
    from envoy_cli import video

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is False
            return {
                "id": "sample",
                "title": "A public lesson",
                "subtitles": {
                    "en": [{"ext": "vtt", "url": "https://cdn.example.org/en.vtt"}],
                },
                "automatic_captions": {
                    "zh-Hans": [{"ext": "vtt", "url": "https://cdn.example.org/zh.vtt"}],
                },
            }

        def urlopen(self, _url):
            return SimpleNamespace(
                read=lambda _limit: b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nChinese caption\n",
                close=lambda: None,
            )

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    result = video.extract_public_subtitles(
        "https://www.youtube.com/watch?v=sample",
        preferred_language="zh-Hans",
    )

    assert result["language"] == "zh-Hans"
    assert result["cues"][0]["text"] == "Chinese caption"


def test_video_extractor_returns_explicit_no_subtitles_state(monkeypatch):
    from envoy_cli import video

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is False
            return {"id": "sample", "title": "Silent video", "subtitles": {}, "automatic_captions": {}}

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    result = video.extract_public_subtitles("https://www.youtube.com/watch?v=sample")

    assert result["status"] == "no_subtitles"
    assert result["cues"] == []


def test_video_extractor_fetches_signed_caption_url_locally_without_returning_it(monkeypatch):
    from envoy_cli import video

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is False
            return {
                "id": "sample",
                "subtitles": {
                    "en": [{"ext": "vtt", "url": "https://cdn.example.org/sub.vtt?signature=secret"}],
                },
            }

        def urlopen(self, url):
            assert "secret" in url
            return SimpleNamespace(
                read=lambda _limit: b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nCaption\n",
                close=lambda: None,
            )

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    result = video.extract_public_subtitles("https://www.youtube.com/watch?v=sample")

    assert result["status"] == "ready"
    assert "secret" not in str(result)


def test_supported_extractor_detection_excludes_generic_web_pages(monkeypatch):
    from envoy_cli import video

    class YouTube:
        IE_NAME = "youtube"

        @staticmethod
        def suitable(url):
            return "youtube.com/watch" in url

    class Generic:
        IE_NAME = "generic"

        @staticmethod
        def suitable(_url):
            return True

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (object, [YouTube, Generic]))

    assert video.has_specialized_extractor("https://www.youtube.com/watch?v=sample") is True
    assert video.has_specialized_extractor("https://example.org/article") is False


def test_video_subtitle_material_search_returns_source_time_locator(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import MaterialSearchRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    cues = [
        {"start": index * 5.0, "end": index * 5.0 + 3.0, "text": f"General lesson detail number {index}."}
        for index in range(12)
    ]
    cues[-1] = {"start": 42.0, "end": 45.0, "text": "Open status menu to inspect boss phase."}
    initial = reader._video_subtitle_response(
        ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "video-caption-read",
            "source": {"url": "https://www.youtube.com/watch?v=sample", "kind": "video"},
            "goal": "Find how to inspect the boss phase",
            "budget": {"max_response_chars": 300},
        }),
        "https://www.youtube.com/watch?v=sample",
        {
            "status": "ready",
            "video_id": "sample",
            "title": "Public lesson",
            "duration": 60,
            "language": "en",
            "automatic": False,
            "available_languages": ["en"],
            "cues": cues,
        },
    )

    result = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "video-caption-search",
        "material_id": initial.material_id,
        "query": "open status menu",
    }))

    assert initial.result.value == "partial"
    assert result.blocks[0].content.text.endswith("Open status menu to inspect boss phase.")
    assert result.blocks[0].locator.type == "video_time"
    assert result.blocks[0].locator.start_seconds == 42.0
    assert result.blocks[0].locator.end_seconds == 45.0


def test_read_auto_routes_recognized_platform_video_to_subtitles(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    calls = {}

    def extract(source_url, preferred_language=None):
        calls["url"] = source_url
        calls["preferred_language"] = preferred_language
        return {
            "status": "ready",
            "video_id": "sample",
            "title": "Public lesson",
            "duration": 8,
            "language": "zh-Hans",
            "automatic": True,
            "available_languages": ["en", "zh-Hans"],
            "cues": [{"start": 1.0, "end": 2.0, "text": "打开设置菜单"}],
        }

    monkeypatch.setattr(reader, "extract_public_subtitles", extract)
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "auto-platform-video",
        "source": {"url": "https://video.example/watch?id=sample"},
        "goal": "解释视频里的设置步骤",
        "video_subtitle_language": "zh-Hans",
    }))

    assert calls["preferred_language"] == "zh-Hans"
    assert response.result.value == "ready"
    assert response.blocks[1].kind.value == "transcript"
    assert response.blocks[1].origin.value == "subtitle"
    assert response.coverage.modalities["video"].details["visual_inspection"] == "not_performed"
