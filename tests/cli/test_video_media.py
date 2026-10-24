from pathlib import Path
from types import SimpleNamespace

import pytest


def _bounded_worker_returns(value):
    return {"value": value}


def _bounded_worker_sleeps(seconds):
    import time
    time.sleep(seconds)
    return {"finished": True}


def _run_ytdlp_inline(monkeypatch, media):
    monkeypatch.setattr(
        media,
        "run_bounded_worker",
        lambda target, *args, max_work_seconds: target(*args),
    )


def test_bounded_worker_returns_result_from_child_process():
    from envoy_cli.media import run_bounded_worker

    assert run_bounded_worker(
        _bounded_worker_returns,
        "metadata",
        max_work_seconds=10,
    ) == {"value": "metadata"}


def test_bounded_worker_terminates_child_at_total_deadline():
    from envoy_cli.media import MediaBudgetExceeded, run_bounded_worker

    with pytest.raises(MediaBudgetExceeded, match="Media worker exceeded"):
        run_bounded_worker(
            _bounded_worker_sleeps,
            5,
            max_work_seconds=1,
        )


def test_audio_download_uses_cookie_free_bounded_yt_dlp_options(tmp_path, monkeypatch):
    from envoy_cli import media

    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            path = tmp_path / "audio.m4a"
            path.write_bytes(b"audio")
            captured["progress_hooks"][0]({
                "status": "finished",
                "filename": str(path),
                "total_bytes_downloaded": path.stat().st_size,
            })
            return {
                "id": "sample",
                "title": "Sample",
                "duration": 30,
                "requested_downloads": [{"filepath": str(path)}],
            }

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")
    _run_ytdlp_inline(monkeypatch, media)

    result = media.download_audio_for_asr(
        "https://video.example/watch/1",
        tmp_path,
        max_download_bytes=1024,
        max_work_seconds=30,
    )

    assert result["path"] == str(tmp_path / "audio.m4a")
    assert result["downloaded_bytes"] == 5
    assert captured["ignoreconfig"] is True
    assert captured["noplaylist"] is True
    assert captured["format"] == "bestaudio/best"
    assert "cookiefile" not in captured
    assert "cookiesfrombrowser" not in captured


def test_audio_download_pins_yt_dlp_to_a_direct_connection_by_default(tmp_path, monkeypatch):
    from envoy_cli import media

    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            path = tmp_path / "audio.m4a"
            path.write_bytes(b"audio")
            return {
                "id": "sample",
                "title": "Sample",
                "duration": 30,
                "requested_downloads": [{"filepath": str(path)}],
            }

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")
    monkeypatch.setattr(media, "proxy_plan", lambda: [None])
    _run_ytdlp_inline(monkeypatch, media)

    media.download_audio_for_asr(
        "https://video.example/watch/1",
        tmp_path,
        max_download_bytes=1024,
        max_work_seconds=30,
    )

    assert captured["proxy"] == ""


def test_audio_download_retries_through_the_detected_proxy_after_a_direct_failure(
    tmp_path, monkeypatch
):
    from envoy_cli import media

    proxies = []

    class FlakyYDL:
        def __init__(self, options):
            proxies.append(options["proxy"])
            self.failing = options["proxy"] == ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            if self.failing:
                raise OSError("handshake timed out")
            path = tmp_path / "audio.m4a"
            path.write_bytes(b"audio")
            return {
                "id": "sample",
                "title": "Sample",
                "duration": 30,
                "requested_downloads": [{"filepath": str(path)}],
            }

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FlakyYDL)
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")
    monkeypatch.setattr(media, "proxy_plan", lambda: [None, "http://127.0.0.1:7897"])
    _run_ytdlp_inline(monkeypatch, media)

    result = media.download_audio_for_asr(
        "https://video.example/watch/1",
        tmp_path,
        max_download_bytes=1024,
        max_work_seconds=30,
    )

    assert proxies == ["", "http://127.0.0.1:7897"]
    assert result["path"] == str(tmp_path / "audio.m4a")


def test_audio_download_raises_socket_timeout_for_longer_work_budget(tmp_path, monkeypatch):
    import socket
    from envoy_cli import media

    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            path = tmp_path / "long-audio.m4a"
            path.write_bytes(b"audio")
            return {
                "id": "sample",
                "title": "Sample",
                "duration": 1200,
                "requested_downloads": [{"filepath": str(path)}],
            }

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    monkeypatch.setattr(media.socket, "getaddrinfo", lambda _host, port, *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
    ])
    _run_ytdlp_inline(monkeypatch, media)

    media.download_audio_for_asr(
        "https://video.example/watch/1",
        tmp_path,
        max_download_bytes=50 * 1024 * 1024,
        max_work_seconds=900,
    )

    # yt-dlp receives a bounded per-request timeout; the work budget is
    # enforced independently by the progress hook / overall deadline.
    assert captured["socket_timeout"] == 30


def test_audio_download_reports_metadata_work_overrun_before_postprocessing(tmp_path, monkeypatch):
    from envoy_cli import media

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            return {"id": "slow-metadata", "duration": 60}

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    clock_values = iter([100, 102])
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock_values))
    _run_ytdlp_inline(monkeypatch, media)

    with pytest.raises(media.MediaBudgetExceeded, match="max_work_seconds"):
        media.download_audio_for_asr(
            "https://public.example/watch/1",
            tmp_path,
            max_download_bytes=1024,
            max_work_seconds=1,
        )


def test_audio_download_aborts_when_observed_bytes_exceed_budget(tmp_path, monkeypatch):
    from envoy_cli import media

    class FakeYDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            self.options["progress_hooks"][0]({
                "status": "downloading",
                "total_bytes_downloaded": 11,
            })
            raise AssertionError("the budget hook must stop the transfer")

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    _run_ytdlp_inline(monkeypatch, media)

    with pytest.raises(media.MediaBudgetExceeded):
        media.download_audio_for_asr(
            "https://video.example/watch/1",
            tmp_path,
            max_download_bytes=10,
            max_work_seconds=30,
        )


def test_yt_dlp_public_network_guard_rejects_private_redirect_target(monkeypatch):
    import socket
    from envoy_cli import media

    def fake_resolver(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_resolver)

    with pytest.raises(media.MediaUnsafeAddress):
        with media.public_network_guard():
            socket.getaddrinfo("redirected.example", 443)

    assert socket.getaddrinfo("restored.example", 443)[0][4][0] == "127.0.0.1"
    assert socket.getaddrinfo is fake_resolver


def test_public_network_guard_allows_configured_local_proxy_but_checks_target(monkeypatch):
    import socket
    from envoy_cli import media

    def resolver(host, port, *_args, **_kwargs):
        normalized = str(host).lower()
        address = {
            "127.0.0.1": "127.0.0.1",
            "public.example": "93.184.216.34",
            "private.example": "10.0.0.12",
        }[normalized]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    class FakeYDL:
        calls = []

        def urlopen(self, url):
            self.calls.append(url)
            socket.getaddrinfo("127.0.0.1", 8080)
            return "ok"

    monkeypatch.setattr(media.socket, "getaddrinfo", resolver)
    monkeypatch.setattr(
        media.urllib.request,
        "getproxies",
        lambda: {"https": "http://127.0.0.1:8080"},
    )
    client = FakeYDL()
    with media.public_network_guard():
        media.guard_yt_dlp_client(client)
        assert client.urlopen("https://public.example/video") == "ok"
        with pytest.raises(media.MediaUnsafeAddress):
            client.urlopen("https://private.example/video")

    assert client.calls == ["https://public.example/video"]
    assert media.socket.getaddrinfo is resolver


def test_yt_dlp_client_disables_http_handlers_that_bypass_redirect_guard():
    from envoy_cli.media import guard_yt_dlp_client

    urllib_handler = object()

    class FakeDirector:
        handlers = {
            "Urllib": urllib_handler,
            "Requests": object(),
            "CurlCFFI": object(),
        }

    class FakeYDL:
        _request_director = FakeDirector()

        def urlopen(self, url):
            return url

    client = FakeYDL()
    guard_yt_dlp_client(client)

    assert client._request_director.handlers == {"Urllib": urllib_handler}


def test_yt_dlp_redirect_handler_checks_location_before_following(monkeypatch):
    import socket
    import urllib.request
    from envoy_cli import media
    from yt_dlp.networking._urllib import RedirectHandler

    def resolver(host, port, *_args, **_kwargs):
        address = "93.184.216.34" if host == "public.example" else "10.0.0.12"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(media.socket, "getaddrinfo", resolver)
    request = urllib.request.Request("https://public.example/watch")
    handler = RedirectHandler()
    with media.public_network_guard():
        with pytest.raises(media.MediaUnsafeAddress):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://private.example/admin",
            )


def test_audio_download_rejects_private_media_host_resolution(tmp_path, monkeypatch):
    import socket
    from envoy_cli import media

    class FakeYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            socket.getaddrinfo("media.example", 443)

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.10", 443)),
    ])
    _run_ytdlp_inline(monkeypatch, media)

    with pytest.raises(media.MediaUnsafeAddress):
        media.download_audio_for_asr(
            "https://public.example/watch/1",
            tmp_path,
            max_download_bytes=1024,
            max_work_seconds=30,
        )


def test_video_download_byte_budget_counts_separate_audio_and_video_streams():
    from envoy_cli import media

    progress = media._download_progress_hook(10, float("inf"))
    progress({
        "status": "downloading",
        "filename": "merged.mp4",
        "tmpfilename": "video.part",
        "downloaded_bytes": 7,
        "info_dict": {"ctx_id": "video-format"},
    })

    with pytest.raises(media.MediaBudgetExceeded, match="max_download_bytes"):
        progress({
            "status": "downloading",
            "filename": "merged.mp4",
            "tmpfilename": "audio.part",
            "downloaded_bytes": 7,
            "info_dict": {"ctx_id": "audio-format"},
        })


def test_video_download_finished_event_does_not_double_count_renamed_file():
    from envoy_cli import media

    progress = media._download_progress_hook(10, float("inf"))
    progress({
        "status": "downloading",
        "filename": "final.mp4",
        "tmpfilename": "final.mp4.part",
        "downloaded_bytes": 7,
        "info_dict": {"ctx_id": "format-1"},
    })
    progress({
        "status": "finished",
        "filename": "final.mp4",
        "downloaded_bytes": 7,
        "total_bytes": 7,
        "info_dict": {"ctx_id": "format-1"},
    })


def test_video_download_for_frames_is_cookie_free_and_size_bounded(tmp_path, monkeypatch):
    from envoy_cli import media

    captured = {}
    video_path = tmp_path / "video.mp4"

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            assert download is True
            video_path.write_bytes(b"video")
            captured["progress_hooks"][0]({
                "status": "finished",
                "total_bytes_downloaded": video_path.stat().st_size,
            })
            return {
                "id": "sample",
                "title": "Sample",
                "duration": 20,
                "requested_downloads": [{"filepath": str(video_path)}],
            }

    monkeypatch.setattr(media, "_load_yt_dlp", lambda: FakeYDL)
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")
    _run_ytdlp_inline(monkeypatch, media)

    result = media.download_video_for_frames(
        "https://video.example/watch/1",
        tmp_path,
        max_download_bytes=1024,
        max_work_seconds=30,
    )

    assert result["path"] == str(video_path)
    assert captured["ignoreconfig"] is True
    assert captured["noplaylist"] is True
    assert "cookiefile" not in captured


def test_frame_extraction_respects_pixel_budget_and_returns_timestamp(tmp_path, monkeypatch):
    from envoy_cli import media

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(stdout='{"streams":[{"width":1920,"height":1080}]}')
        Path(command[-1]).write_bytes(b"jpeg")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(media.shutil, "which", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    frames = media.extract_video_frames(
        video_path,
        tmp_path / "frames",
        [12.5],
        max_visual_pixels_per_input=400_000,
        max_work_seconds=30,
    )

    assert len(frames) == 1
    assert frames[0]["timestamp"] == 12.5
    assert frames[0]["pixels"] <= 400_000
    assert frames[0]["sha256"]
    assert "-ss" in calls[1]


def test_asr_segments_keep_video_time_offsets_and_skip_empty_cues():
    from envoy_cli.media import normalize_asr_segments

    cues = normalize_asr_segments([
        {"start": 0.0, "end": 1.4, "text": "  First words "},
        {"start": 2.0, "end": 2.0, "text": "invalid interval"},
        {"start": 3.0, "end": 4.0, "text": "Second"},
        {"start": 4.5, "end": 5.0, "text": "   "},
        {"start": 5.0, "end": 5.5, "text": "… … …"},
        {
            "start": 6.0,
            "end": 6.5,
            "text": "hallucinated words",
            "no_speech_prob": 0.676,
            "avg_logprob": -0.575,
        },
        {
            "start": 7.0,
            "end": 7.5,
            "text": "low confidence words",
            "no_speech_prob": 0.1,
            "avg_logprob": -1.2,
            "compression_ratio": 1.1,
        },
        {
            "start": 8.0,
            "end": 8.5,
            "text": "repeated repeated repeated",
            "no_speech_prob": 0.1,
            "avg_logprob": -0.2,
            "compression_ratio": 2.5,
        },
    ], offset_seconds=60)

    assert cues == [
        {"start": 60.0, "end": 61.4, "text": "First words"},
        {"start": 63.0, "end": 64.0, "text": "Second"},
    ]


def test_whisper_worker_keeps_progress_output_off_protocol_stdout(monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    from envoy_cli import media

    class Model:
        def transcribe(self, *_args, **_kwargs):
            print("Detected language: English")
            return {
                "language": "en",
                "segments": [{"start": 0, "end": 1, "text": "Hello"}],
            }

    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(
        load_model=lambda *_args, **_kwargs: Model(),
        load_audio=lambda *_args, **_kwargs: [0] * 16_000,
    ))

    class Connection:
        def __init__(self):
            self.events = []

        def send(self, event):
            self.events.append(event)

        def close(self):
            pass

    connection = Connection()
    media._whisper_worker("audio.wav", "base", "/tmp/models", 0, 1, connection)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "Detected language: English" in captured.err
    assert connection.events[-1]["event"] == "complete"


def test_completed_asr_with_no_speech_is_not_reported_as_success(tmp_path, monkeypatch):
    import multiprocessing
    from envoy_cli import media

    audio = tmp_path / "quiet.wav"
    audio.write_bytes(b"audio")

    events = [
        {
            "event": "chunk",
            "start_seconds": 0,
            "end_seconds": 60,
            "language": "en",
            "segments": [],
        },
        {"event": "complete", "end_seconds": 60, "language": "en"},
    ]

    class FakeConnection:
        def send(self, event):
            events.append(event)

        def recv(self):
            return events.pop(0)

        def close(self):
            pass

    class FakeProcess:
        sentinel = object()

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, _timeout=None):
            pass

    monkeypatch.setattr(
        multiprocessing,
        "get_context",
        lambda _method: SimpleNamespace(
            Pipe=lambda duplex=False: (FakeConnection(), FakeConnection()),
            Process=FakeProcess,
        ),
    )
    monkeypatch.setattr(
        media,
        "wait_for_connections",
        lambda connections, timeout=None: [connections[0]] if events else [connections[1]],
    )
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media.time, "monotonic", lambda: 100)

    result = media.transcribe_audio_file(
        audio,
        end_seconds=60,
        max_work_seconds=30,
    )

    assert result["status"] == "no_speech"
    assert result["segments"] == []


def test_completed_asr_allows_worker_to_exit_before_termination(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import multiprocessing
    from envoy_cli import media

    audio = tmp_path / "spoken.wav"
    audio.write_bytes(b"audio")
    events = [
        {
            "event": "chunk",
            "start_seconds": 0,
            "end_seconds": 60,
            "language": "en",
            "segments": [{"start": 1, "end": 2, "text": "Spoken words"}],
        },
        {"event": "complete", "end_seconds": 60, "language": "en"},
    ]
    processes = []

    class FakeConnection:
        def send(self, event):
            events.append(event)

        def recv(self):
            return events.pop(0)

        def close(self):
            pass

    class FakeProcess:
        sentinel = object()

        def __init__(self, *_args, **_kwargs):
            self.alive = True
            self.terminated = False
            self.join_calls = []
            processes.append(self)

        def start(self):
            pass

        def join(self, timeout=None):
            self.join_calls.append(timeout)
            self.alive = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    context = SimpleNamespace(
        Pipe=lambda duplex=False: (FakeConnection(), FakeConnection()),
        Process=FakeProcess,
    )
    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: context)
    monkeypatch.setattr(
        media,
        "wait_for_connections",
        lambda connections, timeout=None: [connections[0]] if events else [connections[1]],
    )
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media.time, "monotonic", lambda: 100)
    slot_calls = []

    @contextmanager
    def model_slot(model_root, *, max_wait_seconds):
        slot_calls.append((Path(model_root), max_wait_seconds))
        yield

    monkeypatch.setattr(media, "asr_model_slot", model_slot)

    result = media.transcribe_audio_file(
        audio,
        end_seconds=60,
        max_work_seconds=30,
    )

    assert result["status"] == "success"
    assert result["segments"][0]["text"] == "Spoken words"
    assert slot_calls == [(Path(media.asr_model_cache_dir()), 30)]
    assert processes[0].join_calls == [5]
    assert processes[0].terminated is False


def test_local_asr_returns_partial_checkpoint_when_work_budget_expires(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import multiprocessing
    from envoy_cli import media

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    events = [{
        "event": "chunk",
        "start_seconds": 0,
        "end_seconds": 60,
        "language": "en",
        "segments": [{"start": 1, "end": 2, "text": "First cue"}],
    }]

    class FakeConnection:
        def send(self, event):
            events.append(event)

        def recv(self):
            return events.pop(0)

        def close(self):
            pass

    class FakeProcess:
        is_alive_state = True
        sentinel = object()

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def join(self, _timeout=None):
            pass

        def is_alive(self):
            return self.is_alive_state

        def terminate(self):
            self.is_alive_state = False

        def kill(self):
            self.is_alive_state = False

    fake_context = SimpleNamespace(
        Pipe=lambda duplex=False: (FakeConnection(), FakeConnection()),
        Process=FakeProcess,
    )
    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: fake_context)
    monkeypatch.setattr(
        media,
        "wait_for_connections",
        lambda connections, timeout=None: [connections[0]] if events else [],
    )
    monkeypatch.setattr(media, "probe_media_duration", lambda _path, **_kwargs: 600)
    monkeypatch.setattr(media, "ASR_CHUNK_SECONDS", 60)
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    clock_values = iter([100, 100, 100, 100, 102, 103])
    monkeypatch.setattr(media.time, "monotonic", lambda: next(clock_values))

    @contextmanager
    def no_model_slot(*_args, **_kwargs):
        yield

    monkeypatch.setattr(
        media,
        "asr_model_slot",
        no_model_slot,
    )

    result = media.transcribe_audio_file(audio, max_work_seconds=1)

    assert result["status"] == "partial"
    assert result["next_seconds"] == 60
    assert result["segments"] == [{"start": 1, "end": 2, "text": "First cue"}]


def test_asr_model_setup_resumes_partial_download_and_verifies_checksum(
    tmp_path,
    monkeypatch,
):
    import hashlib
    import sys
    from types import SimpleNamespace
    from envoy_cli import media

    payload = b"verified-whisper-model-payload"
    digest = hashlib.sha256(payload).hexdigest()
    url = f"https://models.example/{digest}/base.pt"
    requests = []

    class Response:
        def __init__(self, body, *, status, fail_after=None):
            self.body = body
            self.status = status
            self.fail_after = fail_after
            self.read_bytes = 0
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            if self.fail_after is not None and self.read_bytes >= self.fail_after:
                raise OSError("connection interrupted")
            limit = size
            if self.fail_after is not None:
                limit = min(limit, self.fail_after - self.read_bytes)
            chunk = self.body[self.read_bytes:self.read_bytes + limit]
            self.read_bytes += len(chunk)
            return chunk

    def urlopen(request, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        requests.append({"range": headers.get("range"), "timeout": timeout})
        if len(requests) == 1:
            return Response(payload, status=200, fail_after=9)
        assert headers.get("range") == "bytes=9-"
        return Response(payload[9:], status=206)

    def load_model(model_name, *, device, download_root):
        model_path = Path(download_root) / f"{model_name}.pt"
        assert model_path.read_bytes() == payload
        return object()

    monkeypatch.setattr(media, "asr_model_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(media, "asr_model_slot", lambda *_args, **_kwargs: _null_context())
    monkeypatch.setitem(sys.modules, "whisper", SimpleNamespace(
        _MODELS={"base": url},
        load_model=load_model,
    ))

    result = media.prepare_asr_model("base")

    assert result["available"] is True
    assert [item["range"] for item in requests] == [None, "bytes=9-"]
    assert (tmp_path / "base.pt").read_bytes() == payload
    assert not (tmp_path / "base.pt.part").exists()


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_runtime_status_reports_audio_and_local_asr_capability(monkeypatch):
    from envoy_cli import media

    monkeypatch.setattr(media, "shutil", SimpleNamespace(which=lambda _name: "/usr/bin/media"))
    monkeypatch.setattr(media, "_load_yt_dlp", lambda: object)
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")
    monkeypatch.setenv("ENVOY_ASR_MODEL", "tiny")
    monkeypatch.setattr(media, "asr_model_cache_dir", lambda: Path("/tmp/asr-models"))
    monkeypatch.setattr(Path, "is_file", lambda self: self.name == "tiny.pt")

    status = media.media_runtime_status()

    assert status == {
        "ffmpeg": "/usr/bin/media",
        "ffprobe": "/usr/bin/media",
        "yt_dlp": "2026.8.19",
        "yt_dlp_supported": True,
        "asr_engine": "openai-whisper",
        "asr_installed": True,
        "asr_model": "tiny",
        "asr_model_available": False,
        "asr_configured": True,
        "asr_model_file_present": True,
        "asr_model_verified": None,
        "asr_operational": "not_checked",
        "asr_next_step": "run envoy doctor --verify-asr",
    }


def test_asr_model_slot_serializes_competing_jobs_with_budget(tmp_path):
    from threading import Event, Thread
    from envoy_cli import media

    result = []
    finished = Event()

    def attempt_second_slot():
        try:
            with media.asr_model_slot(tmp_path, max_wait_seconds=0.1):
                result.append("acquired")
        except media.MediaBudgetExceeded:
            result.append("busy")
        finally:
            finished.set()

    with media.asr_model_slot(tmp_path, max_wait_seconds=1):
        thread = Thread(target=attempt_second_slot)
        thread.start()
        assert finished.wait(1)
    thread.join(timeout=1)

    assert result == ["busy"]
    assert not thread.is_alive()


def test_nested_file_locks_clear_thread_state_after_release(tmp_path):
    from envoy_cli import media

    before = len(media._FILE_LOCKS_BY_PATH)
    with media.interprocess_file_lock(
        tmp_path / ".outer.lock",
        max_wait_seconds=1,
        busy_message="outer lock timed out",
    ):
        with media.interprocess_file_lock(
            tmp_path / ".inner.lock",
            max_wait_seconds=1,
            busy_message="inner lock timed out",
        ):
            pass

    assert not hasattr(media._ACTIVE_FILE_LOCKS, "keys")
    assert len(media._FILE_LOCKS_BY_PATH) == before


def test_whisper_worker_emits_timestamped_segments_in_bounded_chunks(monkeypatch):
    import sys
    import types
    from envoy_cli import media

    chunks = []

    class FakeModel:
        def transcribe(self, audio, **_kwargs):
            chunks.append(len(audio))
            return {
                "language": "en",
                "segments": [{"start": 1, "end": 2, "text": " cue "}],
            }

    fake_whisper = types.SimpleNamespace(
        _MODELS={"tiny": "https://model.example/tiny.pt"},
        load_model=lambda *_args, **_kwargs: FakeModel(),
        load_audio=lambda *_args, **_kwargs: [0] * (130 * 16_000),
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    monkeypatch.setattr(media, "ASR_CHUNK_SECONDS", 60)

    class ResultConnection:
        def __init__(self):
            self.events = []

        def send(self, value):
            self.events.append(value)

        def close(self):
            pass

    result_connection = ResultConnection()
    media._whisper_worker(
        "/tmp/audio.m4a",
        "tiny",
        "/tmp/model-cache",
        0,
        130,
        result_connection,
    )

    chunks_events = [event for event in result_connection.events if event.get("event") == "chunk"]
    assert chunks == [60 * 16_000, 60 * 16_000, 10 * 16_000]
    assert [event["start_seconds"] for event in chunks_events] == [0, 60, 120]
    assert [event["end_seconds"] for event in chunks_events] == [60, 120, 130]
    assert chunks_events[-1]["segments"] == [{
        "start": 121.0,
        "end": 122.0,
        "text": "cue",
    }]
    assert result_connection.events[-1] == {
        "event": "complete",
        "end_seconds": 130,
        "language": "en",
    }
