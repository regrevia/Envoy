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


def _use_public_fake_dns(monkeypatch):
    import socket
    from envoy_cli import media

    monkeypatch.setattr(media.socket, "getaddrinfo", lambda _host, port, *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
    ])


def _run_subtitle_inline(monkeypatch, video):
    monkeypatch.setattr(
        video,
        "run_bounded_worker",
        lambda target, *args, max_work_seconds: target(*args),
    )


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


def test_subtitle_parser_preserves_bracketed_source_text():
    from envoy_cli.video import parse_subtitle_document

    cues = parse_subtitle_document(
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n[Step 1] Set PORT=3000\n",
        "vtt",
    )

    assert cues == [{"start": 1.0, "end": 2.0, "text": "[Step 1] Set PORT=3000"}]


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
    _use_public_fake_dns(monkeypatch)
    _run_subtitle_inline(monkeypatch, video)
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
    _use_public_fake_dns(monkeypatch)
    _run_subtitle_inline(monkeypatch, video)
    result = video.extract_public_subtitles(
        "https://www.youtube.com/watch?v=sample",
        preferred_language="zh-Hans",
    )

    assert result["language"] == "zh-Hans"
    assert result["cues"][0]["text"] == "Chinese caption"


def test_video_extractor_falls_back_to_supported_automatic_track_same_language(monkeypatch):
    from envoy_cli import video

    calls = []

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
                "language": "en",
                "subtitles": {"en": [{"ext": "ttml", "url": "https://cdn.example.org/manual.ttml"}]},
                "automatic_captions": {"en": [{"ext": "vtt", "url": "https://cdn.example.org/auto.vtt"}]},
            }

        def urlopen(self, url):
            calls.append(url)
            return SimpleNamespace(
                read=lambda _limit: b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nAutomatic caption\n",
                close=lambda: None,
            )

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    _use_public_fake_dns(monkeypatch)
    _run_subtitle_inline(monkeypatch, video)
    result = video.extract_public_subtitles("https://www.youtube.com/watch?v=sample")

    assert result["status"] == "ready"
    assert result["automatic"] is True
    assert calls == ["https://cdn.example.org/auto.vtt"]


def test_subtitle_download_respects_zero_byte_budget(monkeypatch):
    from envoy_cli import video

    class FakeYoutubeDL:
        def __init__(self, _options):
            raise AssertionError("do not contact extractor when download budget is zero")

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYoutubeDL, []))
    _run_subtitle_inline(monkeypatch, video)
    result = video.extract_public_subtitles(
        "https://www.youtube.com/watch?v=sample",
        max_download_bytes=0,
    )

    assert result["status"] == "budget_exceeded"
    assert result["cues"] == []


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
    _use_public_fake_dns(monkeypatch)
    _run_subtitle_inline(monkeypatch, video)
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
    _use_public_fake_dns(monkeypatch)
    _run_subtitle_inline(monkeypatch, video)
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


def test_subtitle_extractor_rejects_private_redirect_resolution(monkeypatch):
    import socket
    from envoy_cli import media, video

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is False
            socket.getaddrinfo("public.example", 443)
            socket.getaddrinfo("redirected.example", 443)

    def resolver(host, port, *_args, **_kwargs):
        address = "93.184.216.34" if host == "public.example" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(media.socket, "getaddrinfo", resolver)
    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYDL, []))
    _run_subtitle_inline(monkeypatch, video)

    result = video.extract_public_subtitles("https://public.example/watch/1")

    assert result["status"] == "unsafe_target"
    assert "non-public" in result["error"]
    assert media.socket.getaddrinfo is resolver


def test_subtitle_extractor_checks_work_deadline_after_metadata_extraction(monkeypatch):
    from envoy_cli import video

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is False
            return {
                "id": "slow",
                "title": "Slow metadata",
                "duration": 10,
                "subtitles": {},
            }

    clock_values = iter([100, 102])
    monkeypatch.setattr(video.time, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (FakeYDL, []))
    _run_subtitle_inline(monkeypatch, video)

    result = video.extract_public_subtitles(
        "https://video.example/watch/slow",
        max_work_seconds=1,
    )

    assert result["status"] == "budget_exceeded"


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


def test_video_subtitle_scope_returns_only_cues_intersecting_requested_range(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://video.example/watch/range"
    response = reader._video_subtitle_response(
        ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "subtitle-range-read",
            "session_id": "subtitle-range-session",
            "source": {"url": source_url, "kind": "video"},
            "scope": {
                "kind": "range",
                "start_seconds": 10,
                "end_seconds": 20,
            },
        }),
        source_url,
        {
            "title": "Range sample",
            "duration": 30,
            "language": "en",
            "automatic": False,
            "cues": [
                {"start": 0, "end": 5, "text": "Outside before."},
                {"start": 9, "end": 12, "text": "Boundary overlap."},
                {"start": 15, "end": 18, "text": "Inside range."},
                {"start": 20, "end": 22, "text": "Outside after."},
            ],
        },
    )

    transcript = response.blocks[1]
    assert "Outside before" not in transcript.content.text
    assert "Boundary overlap" in transcript.content.text
    assert "Inside range" in transcript.content.text
    assert "Outside after" not in transcript.content.text
    assert transcript.locator.start_seconds == 9
    assert transcript.locator.end_seconds == 18
    assert any(
        "Transcript coverage is limited to the requested range 00:10.000-00:20.000"
        in item
        for item in response.coverage.unresolved
    )
    assert response.coverage.modalities["video"].details["requested_range"] == {
        "start_seconds": 10,
        "end_seconds": 20,
    }


def test_video_subtitle_scope_with_no_cues_reports_empty_and_unresolved(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://video.example/watch/silent-range"
    response = reader._video_subtitle_response(
        ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "subtitle-empty-range",
            "source": {"url": source_url, "kind": "video"},
            "scope": {"start_seconds": 50, "end_seconds": 60},
        }),
        source_url,
        {
            "title": "Range sample",
            "duration": 100,
            "language": "en",
            "automatic": False,
            "cues": [{"start": 10, "end": 12, "text": "Outside range."}],
        },
    )

    assert response.result.value == "empty"
    assert not any(block.kind.value == "transcript" for block in response.blocks)
    assert "No transcript cues intersect the requested video time range" in response.coverage.unresolved


def test_video_source_gaps_survive_search_and_expansion(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, MaterialSearchRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://www.youtube.com/watch?v=coverage-gap"
    initial = reader._video_subtitle_response(
        ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "coverage-gap-read",
            "session_id": "coverage-gap-session",
            "source": {"url": source_url, "kind": "video"},
            "budget": {"max_response_chars": 620, "max_total_response_chars": 4000},
        }),
        source_url,
        {
            "status": "ready",
            "video_id": "coverage-gap",
            "title": "Coverage gap source",
            "duration": 200,
            "language": "en",
            "automatic": False,
            "cues": [
                {
                    "start": index * 10,
                    "end": index * 10 + 3,
                    "text": f"Lesson segment {index} includes distinct evidence.",
                }
                for index in range(20)
            ],
        },
    )

    searched = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "coverage-gap-search",
        "session_id": "coverage-gap-session",
        "material_id": initial.material_id,
        "query": "distinct evidence",
        "max_results": 1,
    }))
    text_coverage = initial.coverage.modalities["text"]
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "coverage-gap-expand",
        "session_id": "coverage-gap-session",
        "material_id": initial.material_id,
        "offset": text_coverage.details["next_offset"],
        "max_chars": 300,
    }))

    expected_gap = "Video frames were not inspected; this material contains transcript evidence only"
    assert searched.result.value == "partial"
    assert expected_gap in searched.coverage.unresolved
    assert expected_gap in expanded.coverage.unresolved


def test_repeated_video_read_reuses_material_without_resetting_budget(tmp_path, monkeypatch):
    import json
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "repeat-video-read-1",
        "session_id": "agent-session-video",
        "source": {"url": "https://www.youtube.com/watch?v=repeat", "kind": "video"},
        "budget": {"max_response_chars": 1200, "max_total_response_chars": 4000},
    })
    extracted = {
        "video_id": "repeat",
        "title": "Repeated source",
        "duration": 10,
        "language": "en",
        "automatic": False,
        "cues": [
            {"start": 0, "end": 2, "text": "First evidence."},
            {"start": 3, "end": 5, "text": "Second evidence."},
            {"start": 6, "end": 8, "text": "Third evidence."},
        ],
    }

    first = reader._video_subtitle_response(request, request.source.url, extracted)
    second = reader._video_subtitle_response(
        request.model_copy(update={"request_id": "repeat-video-read-2"}),
        request.source.url,
        extracted,
    )
    stored = json.loads((tmp_path / f"{first.material_id}.json").read_text(encoding="utf-8"))

    assert first.blocks[1].kind.value == "transcript"
    assert not any(block.kind.value == "transcript" for block in second.blocks)
    assert second.material_id == first.material_id
    assert second.coverage.modalities["text"].details["already_returned"] is True
    assert stored["budget_state"]["returned_chars"] == len(first.blocks[1].content.text)


def test_video_reads_without_session_id_do_not_share_returned_state(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    base_request = {
        "protocol_version": "envoy.read/1",
        "source": {"url": "https://www.youtube.com/watch?v=unscoped", "kind": "video"},
    }
    extracted = {
        "video_id": "unscoped",
        "title": "Unscoped source",
        "duration": 10,
        "language": "en",
        "automatic": False,
        "cues": [{"start": 0, "end": 2, "text": "The same source must be readable again."}],
    }

    first = reader._video_subtitle_response(
        ReadRequest.model_validate({**base_request, "request_id": "unscoped-read-1"}),
        base_request["source"]["url"],
        extracted,
    )
    second = reader._video_subtitle_response(
        ReadRequest.model_validate({**base_request, "request_id": "unscoped-read-2"}),
        base_request["source"]["url"],
        extracted,
    )

    first_transcript = next(block for block in first.blocks if block.kind.value == "transcript")
    second_transcript = next(block for block in second.blocks if block.kind.value == "transcript")
    assert first.material_id != second.material_id
    assert first_transcript.content.text == second_transcript.content.text


def test_read_auto_routes_recognized_platform_video_to_subtitles(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    calls = {}

    def extract(source_url, preferred_language=None, **_kwargs):
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


def test_asr_material_is_labeled_as_asr_not_as_subtitles():
    from envoy_cli.reader import _video_subtitle_response
    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-asr-source-label",
        "source": {"url": "https://video.example/watch/1", "kind": "video"},
        "goal": "Find the spoken instruction",
    })
    response = _video_subtitle_response(
        request,
        "https://video.example/watch/1",
        {
            "status": "ready",
            "source": "asr",
            "video_id": "sample",
            "title": "Sample",
            "duration": 30,
            "language": "en",
            "model": "base",
            "transcription_status": "partial",
            "processed_until_seconds": 30,
            "cues": [{"start": 4.0, "end": 6.0, "text": "Run the update command."}],
        },
    )

    transcript = response.blocks[1]
    assert response.result.value == "partial"
    assert transcript.origin.value == "asr"
    assert transcript.locator.type == "video_time"
    assert response.coverage.modalities["text"].details["source"] == "video_asr"
    assert response.coverage.modalities["video"].details["asr_model"] == "base"
    assert response.coverage.sampled_ranges == [{
        "modality": "audio",
        "start_seconds": 0,
        "end_seconds": 30,
    }]


def test_asr_origin_and_partial_audio_gap_survive_search_and_expansion(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest, MaterialSearchRequest, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://video.example/watch/asr-followup"
    initial = reader._video_subtitle_response(
        ReadRequest.model_validate({
            "protocol_version": "envoy.read/1",
            "request_id": "asr-followup-read",
            "session_id": "asr-followup-session",
            "source": {"url": source_url, "kind": "video"},
            "goal": "Find the specific final instruction",
            "budget": {"max_response_chars": 700, "max_total_response_chars": 5000},
        }),
        source_url,
        {
            "status": "ready",
            "source": "asr",
            "video_id": "asr-followup",
            "title": "ASR follow-up source",
            "duration": 300,
            "language": "en",
            "model": "base",
            "transcription_status": "partial",
            "processed_until_seconds": 200,
            "cues": [
                {
                    "start": index * 10,
                    "end": index * 10 + 3,
                    "text": (
                        "Rare final phrase for verification."
                        if index == 19
                        else f"Spoken lesson step number {index}."
                    ),
                }
                for index in range(20)
            ],
        },
    )
    searched = reader.search_material(MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "asr-followup-search",
        "session_id": "asr-followup-session",
        "material_id": initial.material_id,
        "query": "rare final phrase",
        "max_results": 1,
    }))
    expanded = reader.expand_material(ExpandRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "asr-followup-expand",
        "session_id": "asr-followup-session",
        "material_id": initial.material_id,
        "offset": initial.coverage.modalities["text"].details["next_offset"],
        "max_chars": 300,
    }))

    expected_audio_gap = "Local ASR stopped at 03:20.000; later audio remains untranscribed"
    assert initial.blocks[1].origin.value == "asr"
    assert searched.blocks[0].origin.value == "asr"
    assert expanded.blocks[0].origin.value == "asr"
    assert expected_audio_gap in initial.coverage.unresolved
    assert expected_audio_gap in searched.coverage.unresolved
    assert expected_audio_gap in expanded.coverage.unresolved


def test_platform_video_without_subtitles_falls_back_to_local_asr(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(reader, "extract_public_subtitles", lambda *_args, **_kwargs: {
        "status": "no_subtitles",
        "cues": [],
        "title": "A public video",
        "duration": 30,
    })
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "ffmpeg": "/usr/bin/ffmpeg",
        "ffprobe": "/usr/bin/ffprobe",
        "yt_dlp": "2026.8.19",
        "yt_dlp_supported": True,
        "asr_installed": True,
        "asr_model": "tiny",
        "asr_model_available": True,
    })
    monkeypatch.setattr(reader, "download_audio_for_asr", lambda _url, _path, **kwargs: {
        "path": str(tmp_path / "audio.m4a"),
        "video_id": "sample",
        "title": "A public video",
        "duration": 30,
        "downloaded_bytes": 1000,
        "elapsed_ms": 100,
    })
    transcribe_kwargs = {}

    def transcribe(_path, **kwargs):
        transcribe_kwargs.update(kwargs)
        return {
            "status": "success",
            "language": "en",
            "model": "tiny",
            "elapsed_ms": 200,
            "next_seconds": 20,
            "segments": [{"start": 12.0, "end": 16.0, "text": "Run the update command."}],
        }

    monkeypatch.setattr(reader, "transcribe_audio_file", transcribe)

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-asr-fallback",
        "source": {"url": "https://video.example/watch/1", "kind": "video"},
        "goal": "Find the spoken instruction",
        "scope": {
            "kind": "range",
            "start_seconds": 10,
            "end_seconds": 20,
        },
    }))

    assert response.result.value == "ready"
    assert response.blocks[1].origin.value == "asr"
    assert transcribe_kwargs["start_seconds"] == 10
    assert transcribe_kwargs["end_seconds"] == 20
    assert response.coverage.sampled_ranges == [{
        "modality": "audio",
        "start_seconds": 10,
        "end_seconds": 20,
    }]


def test_video_frames_require_explicit_visual_scope_and_host_local_image_capability(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    calls = []

    def download(*_args, **_kwargs):
        calls.append("download")
        raise AssertionError("frame download requires explicit visual scope and host support")

    monkeypatch.setattr(reader, "download_video_for_frames", download, raising=False)
    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "no-frame-consent",
        "source": {"url": "https://video.example/watch/1", "kind": "video"},
        "host_capabilities": {"image_input": "local_file", "video_input": "frames"},
    })
    base = reader._video_subtitle_response(
        request,
        request.source.url,
        {"title": "Video", "language": "en", "cues": [{"start": 1, "end": 2, "text": "Voice"}]},
    )

    response = reader._attach_video_frames(request, request.source.url, base)

    assert calls == []
    assert response.result.value == "ready"


def test_requested_video_frames_are_leased_and_source_located(tmp_path, monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://video.example/watch/1"

    def download(_url, output_dir, **_kwargs):
        video = Path(output_dir) / "video.mp4"
        video.write_bytes(b"temporary video")
        return {"path": str(video), "duration": 20, "downloaded_bytes": 15, "elapsed_ms": 5}

    def extract(_video, output_dir, timestamps, **_kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        frame = Path(output_dir) / "frame-001.jpg"
        frame.write_bytes(b"source frame")
        return [{
            "path": str(frame),
            "timestamp": timestamps[0],
            "width_px": 20,
            "height_px": 20,
            "pixels": 400,
            "size_bytes": 12,
            "sha256": "a" * 64,
        }]

    monkeypatch.setattr(reader, "download_video_for_frames", download, raising=False)
    monkeypatch.setattr(reader, "extract_video_frames", extract, raising=False)
    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "requested-frame",
        "source": {"url": source_url, "kind": "video"},
        "goal": "Inspect the settings shown",
        "scope": {
            "include_visual": True,
            "start_seconds": 5,
            "end_seconds": 10,
        },
        "host_capabilities": {
            "image_input": "local_file",
            "video_input": "frames",
        },
        "budget": {
            "max_visual_inputs": 2,
            "max_visual_pixels_per_input": 400,
            "max_total_visual_pixels": 800,
        },
    })
    base = reader._video_subtitle_response(
        request,
        source_url,
        {
            "title": "Video",
            "language": "en",
            "duration": 20,
            "cues": [{"start": 5, "end": 6, "text": "Open settings"}],
        },
    )

    response = reader._attach_video_frames(request, source_url, base)

    frame_block = next(block for block in response.blocks if block.kind.value == "video_frame")
    resource = response.resources[0]
    assert response.result.value == "needs_host"
    assert response.needs[0].type == "inspect_visual"
    assert resource.delivery.type == "local_file"
    assert Path(resource.delivery.path).is_file()
    assert frame_block.locator.type == "video_time"
    assert frame_block.locator.start_seconds == 5
    assert response.coverage.modalities["video"].delivered == 1


def test_direct_video_no_speech_still_hands_off_requested_frames(tmp_path, monkeypatch):
    import json
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    source_url = "https://cdn.example.org/quiet-demo.mp4"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": source_url,
            "mime_type": "video/mp4",
        })
    ))
    monkeypatch.setattr(
        reader,
        "_read_video_asr",
        lambda *_args: (_ for _ in ()).throw(
            reader.MediaNoSpeech("Local ASR found no speech segments")
        ),
    )

    def download(_url, output_dir, **_kwargs):
        video_path = Path(output_dir) / "video.mp4"
        video_path.write_bytes(b"video")
        return {
            "path": str(video_path),
            "downloaded_bytes": 5,
            "elapsed_ms": 10,
        }

    def extract(_video_path, output_dir, timestamps, **_kwargs):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        frame_path = Path(output_dir) / "frame-001.jpg"
        frame_path.write_bytes(b"frame")
        return [{
            "path": str(frame_path),
            "timestamp": timestamps[0],
            "width_px": 20,
            "height_px": 20,
            "pixels": 400,
            "size_bytes": 5,
            "sha256": "b" * 64,
        }]

    monkeypatch.setattr(reader, "download_video_for_frames", download)
    monkeypatch.setattr(reader, "extract_video_frames", extract)
    monkeypatch.setattr(reader, "probe_media_duration", lambda *_args, **_kwargs: 8)

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "quiet-video-visual",
        "session_id": "quiet-video-session",
        "source": {"url": source_url, "kind": "video"},
        "goal": "Inspect what appears on screen",
        "scope": {
            "include_visual": True,
            "start_seconds": 0,
            "end_seconds": 8,
        },
        "host_capabilities": {
            "video_input": "frames",
            "image_input": "local_file",
        },
        "budget": {
            "max_visual_inputs": 1,
            "max_visual_pixels_per_input": 400,
            "max_total_visual_pixels": 400,
            "max_download_bytes": 1024,
        },
    }))

    assert response.result.value == "needs_host"
    assert response.execution_state.value == "waiting_for_host"
    assert response.material_id is not None
    assert response.source_revision is not None
    assert response.resources[0].delivery.type == "local_file"
    assert Path(response.resources[0].delivery.path).is_file()
    assert response.resources[0].delivery_evidence == "generated_only"
    assert response.resources[0].sha256 == "b" * 64
    assert response.coverage.modalities["video"].details["frame_count"] == 1
    assert "Local ASR found no speech segments" in response.coverage.modalities["video"].unresolved
    stored = json.loads((tmp_path / f"{response.material_id}.json").read_text(encoding="utf-8"))
    assert "Local ASR found no speech segments" in stored["source_coverage_unresolved"]


def test_direct_video_private_redirect_is_blocked_without_frame_retry(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://cdn.example.org/redirecting.mp4"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": source_url,
            "mime_type": "video/mp4",
        })
    ))
    monkeypatch.setattr(
        reader,
        "_read_video_asr",
        lambda *_args: (_ for _ in ()).throw(
            reader.MediaUnsafeAddress("Media extraction resolved a non-public network address")
        ),
    )
    monkeypatch.setattr(
        reader,
        "download_video_for_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe yt-dlp resolution must not be retried for frames")
        ),
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "private-video-redirect",
        "source": {"url": source_url, "kind": "video"},
        "scope": {"include_visual": True, "start_seconds": 1, "end_seconds": 2},
        "host_capabilities": {
            "video_input": "frames",
            "image_input": "local_file",
        },
    }))

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "source_unavailable"
