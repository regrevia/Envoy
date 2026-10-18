import os
import sys
import tempfile
import types
import unittest
import importlib
import subprocess
from pathlib import Path
from queue import Empty
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def reload_media_service():
    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.media_service", None)
    services_pkg = sys.modules.get("app.services")
    if services_pkg is not None and hasattr(services_pkg, "media_service"):
        delattr(services_pkg, "media_service")
    return importlib.import_module("app.services.media_service")


class RuntimeSafetyTests(unittest.TestCase):
    def test_media_commands_have_timeout(self):
        from app.services import media_service

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            media_service.split_video("input.mp4", "tmp/test-segments")
            media_service.extract_frames("input.mp4", "tmp/test-frames")

        self.assertEqual(len(calls), 2)
        for _cmd, kwargs in calls:
            self.assertIn("timeout", kwargs)
            self.assertGreater(kwargs["timeout"], 0)

    def test_media_command_failure_is_reported_with_stderr(self):
        from app.services import media_service

        error = media_service.subprocess.CalledProcessError(
            returncode=1,
            cmd=["ffmpeg", "-i", "broken.mp4"],
            stderr=b"invalid media data",
        )

        with patch("app.services.media_service.subprocess.run", side_effect=error):
            with self.assertRaises(media_service.MediaProcessingError) as ctx:
                media_service.split_video("broken.mp4", "tmp/test-segments")

        self.assertIn("split_video failed", str(ctx.exception))
        self.assertIn("invalid media data", str(ctx.exception))

    def test_media_command_terminates_process_when_canceled(self):
        media_service = reload_media_service()

        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.wait_calls = []
                self.returncode = None

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                self.wait_calls.append(timeout)
                return self.returncode

            def communicate(self):
                return b"", b""

        process = FakeProcess()

        with patch("app.services.media_service.subprocess.Popen", return_value=process):
            with self.assertRaises(media_service.MediaProcessingError) as ctx:
                media_service.split_video("cancel-input.mp4", "tmp/test-cancel-segments", should_cancel=lambda: True)

        self.assertIn("split_video canceled", str(ctx.exception))
        self.assertTrue(process.terminated)
        self.assertEqual(process.wait_calls, [5])

    def test_media_command_kills_process_when_terminate_does_not_exit(self):
        media_service = reload_media_service()

        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.killed = False
                self.wait_calls = []
                self.returncode = None

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True
                self.returncode = -9

            def wait(self, timeout=None):
                self.wait_calls.append(timeout)
                if self.terminated and not self.killed:
                    raise media_service.subprocess.TimeoutExpired(["ffmpeg"], timeout)
                return self.returncode

        process = FakeProcess()

        with patch("app.services.media_service.subprocess.Popen", return_value=process):
            with self.assertRaises(media_service.MediaProcessingError) as ctx:
                media_service.split_video("stubborn-input.mp4", "tmp/test-stubborn-segments", should_cancel=lambda: True)

        self.assertIn("split_video canceled", str(ctx.exception))
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, [5, 5])

    def test_split_video_uses_configured_segment_duration(self):
        os.environ["MEDIA_SEGMENT_SECONDS"] = "600"
        media_service = reload_media_service()

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            media_service.split_video("long-input.mp4", "tmp/test-long-segments")

        self.assertEqual(len(calls), 1)
        cmd = calls[0][0]
        self.assertIn("-f", cmd)
        self.assertIn("segment", cmd)
        self.assertIn("-segment_time", cmd)
        self.assertIn("600", cmd)
        self.assertIn("-reset_timestamps", cmd)
        self.assertTrue(str(cmd[-1]).endswith("segment_%06d.mp4"))

    def test_split_audio_uses_configured_segment_duration(self):
        os.environ["MEDIA_SEGMENT_SECONDS"] = "600"
        media_service = reload_media_service()

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            media_service.split_audio("long-input.mp3", "tmp/test-long-audio")

        self.assertEqual(len(calls), 1)
        cmd = calls[0][0]
        self.assertIn("-f", cmd)
        self.assertIn("segment", cmd)
        self.assertIn("-segment_time", cmd)
        self.assertIn("600", cmd)
        self.assertIn("-reset_timestamps", cmd)
        self.assertIn("-vn", cmd)
        self.assertTrue(str(cmd[-1]).endswith("audio_segment_%06d.wav"))

    def test_media_commands_use_configured_ffmpeg_binary(self):
        previous = os.environ.get("FFMPEG_BIN")
        try:
            os.environ["FFMPEG_BIN"] = "/opt/envoy/bin/ffmpeg"
            media_service = reload_media_service()

            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return None

            with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
                media_service.split_video("input.mp4", "tmp/test-configured-video-bin")
                media_service.split_audio("input.mp3", "tmp/test-configured-audio-bin")
                media_service.extract_frames("input.mp4", "tmp/test-configured-frame-bin")

            self.assertEqual([cmd[0] for cmd, _kwargs in calls], ["/opt/envoy/bin/ffmpeg"] * 3)
        finally:
            if previous is None:
                os.environ.pop("FFMPEG_BIN", None)
            else:
                os.environ["FFMPEG_BIN"] = previous
            reload_media_service()

    def test_media_segmentation_limits_total_input_duration_from_config(self):
        previous = {key: os.environ.get(key) for key in ("MEDIA_SEGMENT_SECONDS", "MEDIA_MAX_SEGMENT_COUNT")}
        try:
            os.environ["MEDIA_SEGMENT_SECONDS"] = "600"
            os.environ["MEDIA_MAX_SEGMENT_COUNT"] = "2"
            media_service = reload_media_service()

            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return None

            with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
                media_service.split_video("long-input.mp4", "tmp/test-max-video-segments")
                media_service.split_audio("long-input.mp3", "tmp/test-max-audio-segments")

            self.assertEqual(len(calls), 2)
            for cmd, _kwargs in calls:
                self.assertIn("-t", cmd)
                duration_index = cmd.index("-t") + 1
                self.assertEqual(cmd[duration_index], "1200")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            reload_media_service()

    def test_extract_frames_uses_configured_sampling_interval(self):
        os.environ["MEDIA_FRAME_INTERVAL_SECONDS"] = "15"
        media_service = reload_media_service()

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            media_service.extract_frames("long-input.mp4", "tmp/test-long-frames")

        self.assertEqual(len(calls), 1)
        cmd = calls[0][0]
        self.assertIn("-vf", cmd)
        self.assertIn("fps=1/15", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("mjpeg", cmd)
        self.assertIn("-pix_fmt", cmd)
        self.assertIn("yuvj420p", cmd)
        self.assertTrue(str(cmd[-1]).endswith("frame_%06d.jpg"))

    def test_extract_frames_uses_configured_target_frame_rate_when_set(self):
        previous = os.environ.get("MEDIA_TARGET_FRAME_RATE")
        try:
            os.environ["MEDIA_TARGET_FRAME_RATE"] = "24"
            media_service = reload_media_service()

            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return None

            with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
                media_service.extract_frames("high-fps-input.mp4", "tmp/test-target-fps-frames")

            cmd = calls[0][0]
            self.assertIn("-vf", cmd)
            self.assertIn("fps=24", cmd)
            self.assertNotIn("fps=1/", cmd)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_TARGET_FRAME_RATE", None)
            else:
                os.environ["MEDIA_TARGET_FRAME_RATE"] = previous

    def test_extract_frames_falls_back_to_first_frame_for_short_media(self):
        media_service = reload_media_service()
        output_dir = Path(tempfile.mkdtemp(prefix="envoy-short-frames-"))
        source_path = output_dir / "short.mp4"
        source_path.write_bytes(b"video")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 2:
                (output_dir / "frame_000001.jpg").write_bytes(b"frame")
            return None

        with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
            frames = media_service.extract_frames(str(source_path), str(output_dir), frame_interval_seconds=5)

        assert frames == [str(output_dir / "frame_000001.jpg")]
        assert len(calls) == 2
        assert "select='eq(n,0)'" in calls[1]

    def test_extract_frames_limits_frame_count_and_returns_actual_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            previous = {key: os.environ.get(key) for key in ("MEDIA_FRAME_INTERVAL_SECONDS", "MEDIA_MAX_FRAME_COUNT")}
            try:
                os.environ["MEDIA_FRAME_INTERVAL_SECONDS"] = "10"
                os.environ["MEDIA_MAX_FRAME_COUNT"] = "2"
                media_service = reload_media_service()

                output_dir = Path(tmp_dir) / "frames"
                calls = []

                def fake_run(cmd, **kwargs):
                    calls.append((cmd, kwargs))
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "frame_000001.jpg").write_bytes(b"frame one")
                    (output_dir / "frame_000002.jpg").write_bytes(b"frame two")
                    return None

                with patch("app.services.media_service.subprocess.run", side_effect=fake_run):
                    frames = media_service.extract_frames("long-input.mp4", str(output_dir))

                self.assertEqual(len(calls), 1)
                cmd = calls[0][0]
                self.assertIn("-frames:v", cmd)
                self.assertIn("2", cmd)
                self.assertEqual([Path(path).name for path in frames], ["frame_000001.jpg", "frame_000002.jpg"])
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                reload_media_service()

    def test_media_ocr_can_be_disabled_from_environment(self):
        previous = os.environ.get("MEDIA_ENABLE_OCR")
        try:
            os.environ["MEDIA_ENABLE_OCR"] = "false"
            sys.modules.pop("app.core.config", None)
            config = importlib.import_module("app.core.config")

            self.assertFalse(config.MEDIA_ENABLE_OCR)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_ENABLE_OCR", None)
            else:
                os.environ["MEDIA_ENABLE_OCR"] = previous
            sys.modules.pop("app.core.config", None)

    def test_media_frame_extraction_can_be_disabled_from_environment(self):
        previous = os.environ.get("MEDIA_ENABLE_FRAME_EXTRACTION")
        try:
            os.environ["MEDIA_ENABLE_FRAME_EXTRACTION"] = "false"
            sys.modules.pop("app.core.config", None)
            config = importlib.import_module("app.core.config")

            self.assertFalse(config.MEDIA_ENABLE_FRAME_EXTRACTION)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_ENABLE_FRAME_EXTRACTION", None)
            else:
                os.environ["MEDIA_ENABLE_FRAME_EXTRACTION"] = previous
            sys.modules.pop("app.core.config", None)

    def test_whisper_transcription_helper_reuses_loaded_model(self):
        previous = os.environ.get("MEDIA_WHISPER_MODEL")
        calls = []

        class FakeModel:
            def transcribe(self, audio_path, **kwargs):
                return {"text": f"transcribed {audio_path}", "segments": [{"text": audio_path}]}

        fake_whisper = types.SimpleNamespace(load_model=lambda model_name: calls.append(model_name) or FakeModel())
        sys.modules["whisper"] = fake_whisper
        try:
            os.environ["MEDIA_WHISPER_MODEL"] = "base"
            media_service = reload_media_service()

            first = media_service._run_whisper_transcription("segment-1.wav")
            second = media_service._run_whisper_transcription("segment-2.wav")
        finally:
            sys.modules.pop("whisper", None)
            if previous is None:
                os.environ.pop("MEDIA_WHISPER_MODEL", None)
            else:
                os.environ["MEDIA_WHISPER_MODEL"] = previous
            reload_media_service()

        self.assertEqual(calls, ["base"])
        self.assertEqual(first["text"], "transcribed segment-1.wav")
        self.assertEqual(second["text"], "transcribed segment-2.wav")

    def test_whisper_transcription_uses_configured_model_name(self):
        previous = os.environ.get("MEDIA_WHISPER_MODEL")
        calls = []

        class FakeModel:
            def transcribe(self, audio_path, **kwargs):
                return {"text": f"tiny transcript {audio_path}", "segments": []}

        fake_whisper = types.SimpleNamespace(load_model=lambda model_name: calls.append(model_name) or FakeModel())
        sys.modules["whisper"] = fake_whisper
        try:
            os.environ["MEDIA_WHISPER_MODEL"] = "tiny"
            media_service = reload_media_service()

            result = media_service._run_whisper_transcription("segment-tiny.wav")
        finally:
            sys.modules.pop("whisper", None)
            if previous is None:
                os.environ.pop("MEDIA_WHISPER_MODEL", None)
            else:
                os.environ["MEDIA_WHISPER_MODEL"] = previous
            reload_media_service()

        self.assertEqual(calls, ["tiny"])
        self.assertEqual(result["text"], "tiny transcript segment-tiny.wav")

    def test_transcribe_audio_returns_isolated_worker_result(self):
        media_service = reload_media_service()

        class FakeQueue:
            def get_nowait(self):
                return {"status": "success", "text": "worker transcript", "segments": [{"text": "worker transcript"}], "error": None}

        class FakeProcess:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.join_calls = []
                self.started = False

            def start(self):
                self.started = True

            def join(self, timeout=None):
                self.join_calls.append(timeout)

            def is_alive(self):
                return False

        with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
            patch("app.services.media_service.multiprocessing.Process", FakeProcess):
            result = media_service.transcribe_audio("segment-1.wav")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], "worker transcript")
        self.assertEqual(result["segments"], [{"text": "worker transcript"}])

    def test_transcribe_audio_returns_diagnostic_error_when_worker_fails(self):
        media_service = reload_media_service()

        class FakeQueue:
            def get_nowait(self):
                return {"status": "failed", "text": "", "segments": [], "error": "whisper failed for broken-segment.wav"}

        class FakeProcess:
            def __init__(self, target, args, daemon):
                pass

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

        with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
            patch("app.services.media_service.multiprocessing.Process", FakeProcess):
            result = media_service.transcribe_audio("broken-segment.wav")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["segments"], [])
        self.assertIn("whisper failed for broken-segment.wav", result["error"])

    def test_transcribe_audio_returns_failed_result_when_timeout_expires(self):
        previous = os.environ.get("MEDIA_TRANSCRIPTION_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = "1"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeExecutor:
                def __init__(self, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    self.join_calls = []
                    self.terminated = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.terminated

                def terminate(self):
                    self.terminated = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeExecutor(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.transcribe_audio("slow-segment.wav")

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["text"], "")
            self.assertEqual(result["segments"], [])
            self.assertIn("transcription timeout after 1 sec", result["error"])
            self.assertEqual(processes[0].join_calls, [1, 5])
            self.assertTrue(processes[0].terminated)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_TRANSCRIPTION_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_transcribe_audio_terminates_worker_when_canceled(self):
        previous = os.environ.get("MEDIA_TRANSCRIPTION_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = "30"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeProcess:
                def __init__(self, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    self.join_calls = []
                    self.terminated = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.terminated

                def terminate(self):
                    self.terminated = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeProcess(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.transcribe_audio("cancel-segment.wav", should_cancel=lambda: True)

            self.assertEqual(result["status"], "canceled")
            self.assertEqual(result["text"], "")
            self.assertEqual(result["segments"], [])
            self.assertEqual(result["error"], "transcription canceled")
            self.assertEqual(processes[0].join_calls, [1, 5])
            self.assertTrue(processes[0].terminated)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_TRANSCRIPTION_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_transcribe_audio_kills_worker_when_terminate_does_not_exit(self):
        previous = os.environ.get("MEDIA_TRANSCRIPTION_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = "30"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeProcess:
                def __init__(self, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    self.join_calls = []
                    self.terminated = False
                    self.killed = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.killed

                def terminate(self):
                    self.terminated = True

                def kill(self):
                    self.killed = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeProcess(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.transcribe_audio("stubborn-segment.wav", should_cancel=lambda: True)

            self.assertEqual(result["status"], "canceled")
            self.assertEqual(result["error"], "transcription canceled")
            self.assertTrue(processes[0].terminated)
            self.assertTrue(processes[0].killed)
            self.assertEqual(processes[0].join_calls, [1, 5, 5])
        finally:
            if previous is None:
                os.environ.pop("MEDIA_TRANSCRIPTION_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_TRANSCRIPTION_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_ocr_returns_empty_results_when_timeout_expires(self):
        previous = os.environ.get("MEDIA_OCR_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_OCR_TIMEOUT_SEC"] = "1"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeProcess:
                def __init__(self, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    self.join_calls = []
                    self.terminated = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.terminated

                def terminate(self):
                    self.terminated = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeProcess(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.run_ocr_on_frames(["frame-1.jpg", "frame-2.jpg"])

            self.assertEqual(
                result,
                [
                    {"frame": "frame-1.jpg", "text": "", "confidence": 0.0},
                    {"frame": "frame-2.jpg", "text": "", "confidence": 0.0},
                ],
            )
            self.assertEqual(processes[0].join_calls, [1, 5])
            self.assertTrue(processes[0].terminated)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_OCR_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_OCR_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_ocr_terminates_worker_when_canceled(self):
        previous = os.environ.get("MEDIA_OCR_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_OCR_TIMEOUT_SEC"] = "30"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeProcess:
                def __init__(self, target, args, daemon):
                    self.join_calls = []
                    self.terminated = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.terminated

                def terminate(self):
                    self.terminated = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeProcess(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.run_ocr_on_frames(["frame-1.jpg"], should_cancel=lambda: True)

            self.assertEqual(result, [{"frame": "frame-1.jpg", "text": "", "confidence": 0.0}])
            self.assertEqual(processes[0].join_calls, [1, 5])
            self.assertTrue(processes[0].terminated)
        finally:
            if previous is None:
                os.environ.pop("MEDIA_OCR_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_OCR_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_ocr_kills_worker_when_terminate_does_not_exit(self):
        previous = os.environ.get("MEDIA_OCR_TIMEOUT_SEC")
        try:
            os.environ["MEDIA_OCR_TIMEOUT_SEC"] = "30"
            media_service = reload_media_service()

            class FakeQueue:
                def get_nowait(self):
                    raise Empty()

            class FakeProcess:
                def __init__(self, target, args, daemon):
                    self.join_calls = []
                    self.terminated = False
                    self.killed = False

                def start(self):
                    return None

                def join(self, timeout=None):
                    self.join_calls.append(timeout)

                def is_alive(self):
                    return not self.killed

                def terminate(self):
                    self.terminated = True

                def kill(self):
                    self.killed = True

            processes = []

            def make_process(target, args, daemon):
                process = FakeProcess(target, args, daemon)
                processes.append(process)
                return process

            with patch("app.services.media_service.multiprocessing.Queue", return_value=FakeQueue()), \
                patch("app.services.media_service.multiprocessing.Process", side_effect=make_process):
                result = media_service.run_ocr_on_frames(["frame-1.jpg"], should_cancel=lambda: True)

            self.assertEqual(result, [{"frame": "frame-1.jpg", "text": "", "confidence": 0.0}])
            self.assertTrue(processes[0].terminated)
            self.assertTrue(processes[0].killed)
            self.assertEqual(processes[0].join_calls, [1, 5, 5])
        finally:
            if previous is None:
                os.environ.pop("MEDIA_OCR_TIMEOUT_SEC", None)
            else:
                os.environ["MEDIA_OCR_TIMEOUT_SEC"] = previous
            reload_media_service()

    def test_ocr_worker_preserves_boxes_and_orders_text_top_left(self):
        media_service = reload_media_service()

        class FakePaddleOCR:
            def __init__(self, **_kwargs):
                pass

            def ocr(self, path, cls=True):
                return [[
                    [[[300, 120], [380, 120], [380, 150], [300, 150]], ("right second", 0.91)],
                    [[[20, 120], [180, 120], [180, 150], [20, 150]], ("left second", 0.95)],
                    [[[20, 40], [260, 40], [260, 80], [20, 80]], ("top first", 0.98)],
                ]]

        fake_module = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            result = media_service._run_ocr_worker_logic(["frame-1.jpg"])

        self.assertEqual(result[0]["text"], "top first\nleft second right second")
        self.assertEqual([item["text"] for item in result[0]["items"]], ["top first", "left second", "right second"])
        self.assertEqual(result[0]["items"][0]["bbox"], {"x": 20, "y": 40, "width": 240, "height": 40})
        self.assertEqual(result[0]["reading_order"], "top_left")

    def test_ocr_worker_falls_back_to_tesseract_when_paddleocr_is_unavailable(self):
        media_service = reload_media_service()
        tsv = "\n".join([
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t20\t40\t240\t40\t92\tRED KEY",
            "5\t1\t1\t1\t2\t1\t20\t120\t300\t40\t88\tVILLAGE BRIDGE",
        ])
        completed = subprocess.CompletedProcess([], 0, tsv, "")

        with tempfile.NamedTemporaryFile(suffix=".jpg") as frame, \
             patch.dict(sys.modules, {"paddleocr": None}), \
             patch("app.services.media_service.subprocess.run", return_value=completed):
            frame.write(b"image")
            frame.flush()
            result = media_service._run_ocr_worker_logic([frame.name])

        self.assertEqual(result[0]["text"], "RED KEY\nVILLAGE BRIDGE")
        self.assertEqual(result[0]["items"][0]["bbox"], {"x": 20, "y": 40, "width": 240, "height": 40})
        self.assertEqual(result[0]["reading_order"], "top_left")

    def test_ocr_worker_supports_paddleocr_predict_results(self):
        media_service = reload_media_service()

        class FakePredictResult(dict):
            pass

        class FakePaddleOCR:
            def __init__(self, **_kwargs):
                pass

            def ocr(self, path, cls=True):
                raise TypeError("predict() got an unexpected keyword argument 'cls'")

            def predict(self, path):
                return [
                    FakePredictResult({
                        "rec_texts": ["top first", "left second", "right second"],
                        "rec_scores": [0.98, 0.95, 0.91],
                        "rec_polys": [
                            [[20, 40], [260, 40], [260, 80], [20, 80]],
                            [[20, 120], [180, 120], [180, 150], [20, 150]],
                            [[300, 120], [380, 120], [380, 150], [300, 150]],
                        ],
                    })
                ]

        fake_module = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)
        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            result = media_service._run_ocr_worker_logic(["frame-1.jpg"])

        self.assertEqual(result[0]["text"], "top first\nleft second right second")
        self.assertEqual([item["text"] for item in result[0]["items"]], ["top first", "left second", "right second"])
        self.assertEqual(result[0]["confidence"], 0.98)
        self.assertEqual(result[0]["reading_order"], "top_left")

    def test_ocr_compresses_consecutive_duplicate_frame_text(self):
        media_service = reload_media_service()

        compressed = media_service.compress_consecutive_ocr_results([
            {"frame": "frame-1.jpg", "text": "same slide", "confidence": 0.9},
            {"frame": "frame-2.jpg", "text": "same slide", "confidence": 0.88},
            {"frame": "frame-3.jpg", "text": "new slide", "confidence": 0.91},
        ])

        self.assertEqual(len(compressed), 2)
        self.assertEqual(compressed[0]["text"], "same slide")
        self.assertEqual(compressed[0]["repeat_count"], 2)
        self.assertEqual(compressed[0]["frames"], ["frame-1.jpg", "frame-2.jpg"])
        self.assertEqual(compressed[1]["text"], "new slide")

    def test_worker_ocr_passes_task_cancel_callback_when_supported(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        calls = []

        def fake_ocr(frame_paths, should_cancel=None):
            calls.append({"frame_paths": frame_paths, "canceled": should_cancel() if should_cancel else None})
            return [{"frame": "frame-1.jpg", "text": "", "confidence": 0.0}]

        with patch("app.workers.jobs.run_ocr_on_frames", side_effect=fake_ocr), \
            patch("app.workers.jobs._task_is_canceled", return_value=True):
            result = jobs._run_ocr_for_task(object(), "task-cancel", ["frame-1.jpg"])

        self.assertEqual(result, [{"frame": "frame-1.jpg", "text": "", "confidence": 0.0}])
        self.assertEqual(calls, [{"frame_paths": ["frame-1.jpg"], "canceled": True}])

    def test_worker_ocr_keeps_one_argument_mock_compatibility(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        with patch("app.workers.jobs.run_ocr_on_frames", return_value=[]) as ocr:
            result = jobs._run_ocr_for_task(object(), "task-id", ["frame-1.jpg"])

        self.assertEqual(result, [])
        ocr.assert_called_once_with(["frame-1.jpg"])

    def test_worker_transcription_passes_task_cancel_callback_when_supported(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        calls = []

        def fake_transcribe(audio_path, should_cancel=None):
            calls.append({"audio_path": audio_path, "canceled": should_cancel() if should_cancel else None})
            return {"status": "canceled", "text": "", "segments": [], "error": "transcription canceled"}

        with patch("app.workers.jobs.transcribe_audio", side_effect=fake_transcribe), \
            patch("app.workers.jobs._task_is_canceled", return_value=True):
            result = jobs._transcribe_audio_for_task(object(), "task-cancel", "segment.wav")

        self.assertEqual(result["status"], "canceled")
        self.assertEqual(calls, [{"audio_path": "segment.wav", "canceled": True}])

    def test_worker_transcription_keeps_one_argument_mock_compatibility(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        with patch("app.workers.jobs.transcribe_audio", return_value={"status": "success", "text": "ok", "segments": []}) as transcribe:
            result = jobs._transcribe_audio_for_task(object(), "task-id", "segment.wav")

        self.assertEqual(result["status"], "success")
        transcribe.assert_called_once_with("segment.wav")

    def test_worker_media_split_passes_task_cancel_callback_when_supported(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        calls = []

        def fake_split(source_path, output_dir, should_cancel=None):
            calls.append({
                "source_path": source_path,
                "output_dir": output_dir,
                "canceled": should_cancel() if should_cancel else None,
            })
            return ["segment_000001.mp4"]

        with patch("app.workers.jobs.split_video", side_effect=fake_split), \
            patch("app.workers.jobs._task_is_canceled", return_value=True):
            result = jobs._split_video_for_task(object(), "task-cancel", "input.mp4", "out")

        self.assertEqual(result, ["segment_000001.mp4"])
        self.assertEqual(calls, [{"source_path": "input.mp4", "output_dir": "out", "canceled": True}])

    def test_worker_media_helpers_keep_legacy_mock_compatibility(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        with patch("app.workers.jobs.split_audio", return_value=["audio_segment_000001.wav"]) as split_audio, \
            patch("app.workers.jobs.split_video", return_value=["segment_000001.mp4"]) as split_video, \
            patch("app.workers.jobs.extract_frames", return_value=["frame_000001.jpg"]) as extract_frames:
            audio = jobs._split_audio_for_task(object(), "task-id", "input.mp3", "audio-out")
            video = jobs._split_video_for_task(object(), "task-id", "input.mp4", "video-out")
            frames = jobs._extract_frames_for_task(object(), "task-id", "input.mp4", "frame-out")

        self.assertEqual(audio, ["audio_segment_000001.wav"])
        self.assertEqual(video, ["segment_000001.mp4"])
        self.assertEqual(frames, ["frame_000001.jpg"])
        split_audio.assert_called_once_with("input.mp3", "audio-out")
        split_video.assert_called_once_with("input.mp4", "video-out")
        extract_frames.assert_called_once_with("input.mp4", "frame-out")

    def test_worker_reads_video_frame_sampling_from_task_config(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        task = types.SimpleNamespace(input_config={
            "video_target_frame_rate": 12,
            "video_frame_interval_seconds": 8,
            "video_max_frame_count": 96,
        })

        config = jobs._video_frame_config({"input_config": {}}, task)

        self.assertEqual(config["target_frame_rate"], 12)
        self.assertEqual(config["frame_interval_seconds"], 8)
        self.assertEqual(config["max_frame_count"], 96)
        self.assertEqual(config["mode"], "target_fps")

    def test_worker_extract_frames_ignores_frame_config_metadata_keys(self):
        for name in list(sys.modules):
            if name.startswith("app."):
                sys.modules.pop(name, None)

        from app.workers import jobs

        calls = []

        def fake_extract_frames(source_path, output_dir, should_cancel=None, target_frame_rate=None, frame_interval_seconds=None, max_frame_count=None):
            calls.append({
                "source_path": source_path,
                "output_dir": output_dir,
                "target_frame_rate": target_frame_rate,
                "frame_interval_seconds": frame_interval_seconds,
                "max_frame_count": max_frame_count,
                "canceled": should_cancel() if should_cancel else None,
            })
            return ["frame_000001.jpg"]

        frame_config = {
            "target_frame_rate": 12,
            "frame_interval_seconds": 8,
            "max_frame_count": 96,
            "mode": "target_fps",
        }
        with patch("app.workers.jobs.extract_frames", side_effect=fake_extract_frames), \
            patch("app.workers.jobs._task_is_canceled", return_value=False):
            frames = jobs._extract_frames_for_task(object(), "task-id", "input.mp4", "frame-out", frame_config=frame_config)

        self.assertEqual(frames, ["frame_000001.jpg"])
        self.assertEqual(calls, [{
            "source_path": "input.mp4",
            "output_dir": "frame-out",
            "target_frame_rate": 12,
            "frame_interval_seconds": 8,
            "max_frame_count": 96,
            "canceled": False,
        }])

    def test_ollama_returns_disabled_result_without_model(self):
        os.environ["OLLAMA_MODEL"] = ""
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        result = generate_with_ollama("Summarize this", "content")

        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["text"], "")

    def test_ollama_uses_configured_model_and_endpoint(self):
        os.environ["OLLAMA_BASE_URL"] = "http://ollama.local:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response": "better content"}

        class FakeClient:
            def __init__(self):
                self.calls = []

            def post(self, url, json, timeout):
                self.calls.append({"url": url, "json": json, "timeout": timeout})
                return FakeResponse()

        client = FakeClient()
        result = generate_with_ollama("Improve", "raw content", client=client)

        self.assertTrue(result["enabled"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["text"], "better content")
        self.assertEqual(client.calls[0]["url"], "http://ollama.local:11434/api/generate")
        self.assertEqual(client.calls[0]["json"]["model"], "qwen2.5:7b")
        self.assertIn("raw content", client.calls[0]["json"]["prompt"])

    def test_ollama_empty_response_is_reported_as_failed(self):
        os.environ["OLLAMA_BASE_URL"] = "http://ollama.local:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response": "   "}

        class FakeClient:
            def post(self, url, json, timeout):
                return FakeResponse()

        result = generate_with_ollama("Improve", "raw content", client=FakeClient())

        self.assertTrue(result["enabled"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["error"], "ollama returned empty response")

    def test_ollama_truncates_long_content_before_generation(self):
        os.environ["OLLAMA_BASE_URL"] = "http://ollama.local:11434"
        os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"
        os.environ["OLLAMA_MAX_PROMPT_CHARS"] = "40"
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import generate_with_ollama

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"response": "short review"}

        class FakeClient:
            def __init__(self):
                self.calls = []

            def post(self, url, json, timeout):
                self.calls.append({"url": url, "json": json, "timeout": timeout})
                return FakeResponse()

        client = FakeClient()
        result = generate_with_ollama("Review", "x" * 200, client=client)

        prompt = client.calls[0]["json"]["prompt"]
        self.assertLessEqual(len(prompt), 40)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["original_prompt_chars"], len("Review\n\n" + ("x" * 200)))
        self.assertEqual(result["sent_prompt_chars"], len(prompt))

    def test_ollama_json_parser_reports_incomplete_structured_output(self):
        sys.modules.pop("app.core.config", None)
        sys.modules.pop("app.services.ollama_service", None)
        from app.services.ollama_service import parse_ollama_json_object_with_status

        result = parse_ollama_json_object_with_status('{"keywords":["refund"]}', required_keys=("summary",))

        self.assertEqual(result["parsed"], {"keywords": ["refund"]})
        self.assertEqual(result["parse_status"], "incomplete")
        self.assertEqual(result["parse_error"], "missing structured field(s): summary")

    def test_storage_local_mode_persists_uploaded_bytes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["ENVOY_STORAGE_MODE"] = "local"
            os.environ["ENVOY_LOCAL_STORAGE_DIR"] = str(Path(tmp_dir) / "test-local-storage")
            sys.modules.pop("app.core.config", None)
            sys.modules.pop("app.core.storage", None)
            from app.core.storage import StorageClient

            storage = StorageClient()
            result = storage.upload_bytes("sample.txt", b"hello", "text/plain")

            self.assertIsNone(storage.client)
            self.assertEqual(result["storage"], "local")
            self.assertEqual(Path(result["file_path"]).read_bytes(), b"hello")
            self.assertEqual(storage.read_text(result["object_name"]), "hello")


if __name__ == "__main__":
    unittest.main()
