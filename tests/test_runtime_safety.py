import os
import sys
import tempfile
import types
import unittest
import importlib
from pathlib import Path
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
        self.assertTrue(str(cmd[-1]).endswith("frame_%06d.jpg"))

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

    def test_transcribe_audio_reuses_loaded_whisper_model(self):
        calls = []

        class FakeModel:
            def transcribe(self, audio_path):
                return {"text": f"transcribed {audio_path}", "segments": [{"text": audio_path}]}

        fake_whisper = types.SimpleNamespace(load_model=lambda model_name: calls.append(model_name) or FakeModel())
        sys.modules["whisper"] = fake_whisper
        try:
            media_service = reload_media_service()

            first = media_service.transcribe_audio("segment-1.wav")
            second = media_service.transcribe_audio("segment-2.wav")
        finally:
            sys.modules.pop("whisper", None)

        self.assertEqual(calls, ["base"])
        self.assertEqual(first["text"], "transcribed segment-1.wav")
        self.assertEqual(second["text"], "transcribed segment-2.wav")

    def test_transcribe_audio_returns_diagnostic_error_when_whisper_fails(self):
        class BrokenModel:
            def transcribe(self, audio_path):
                raise RuntimeError(f"whisper failed for {audio_path}")

        fake_whisper = types.SimpleNamespace(load_model=lambda model_name: BrokenModel())
        sys.modules["whisper"] = fake_whisper
        try:
            media_service = reload_media_service()

            result = media_service.transcribe_audio("broken-segment.wav")
        finally:
            sys.modules.pop("whisper", None)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["segments"], [])
        self.assertIn("whisper failed for broken-segment.wav", result["error"])

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
