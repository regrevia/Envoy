import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class DeliveryPackageValidatorTests(unittest.TestCase):
    def test_delivery_runtime_check_reports_missing_tools_as_json(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT_DIR / "scripts" / "check_delivery_runtime.py"),
            ],
            cwd=str(ROOT_DIR),
            env={"PATH": ""},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertIn("ffmpeg", payload["missing_required"])
        self.assertIn("ffprobe", payload["missing_required"])
        self.assertIn("pdfinfo", payload["missing_optional"])
        self.assertIn("pdftoppm", payload["missing_optional"])
        self.assertIn("tesseract", payload["missing_optional"])

    def test_delivery_runtime_check_honors_configured_media_binaries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-custom"
            ffprobe_bin = Path(tmp_dir) / "ffprobe-custom"
            fake_modules = Path(tmp_dir) / "python"
            fake_modules.mkdir()
            (fake_modules / "whisper.py").write_text("", encoding="utf-8")
            for tool in (ffmpeg_bin, ffprobe_bin):
                tool.write_text("#!/bin/sh\necho \"$0 custom version\"\n", encoding="utf-8")
                tool.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "check_delivery_runtime.py"),
                ],
                cwd=str(ROOT_DIR),
                env={
                    "PATH": "",
                    "PYTHONPATH": str(fake_modules),
                    "FFMPEG_BIN": str(ffmpeg_bin),
                    "FFPROBE_BIN": str(ffprobe_bin),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["missing_required"], [])
            required = {item["name"]: item for item in payload["required"]}
            self.assertEqual(required["ffmpeg"]["path"], str(ffmpeg_bin))
            self.assertEqual(required["ffprobe"]["path"], str(ffprobe_bin))
            self.assertTrue(payload["capabilities"]["long_video_delivery_e2e"])
            python_modules = {item["name"]: item for item in payload["python_modules"]}
            self.assertTrue(python_modules["whisper"]["available"])
            self.assertTrue(payload["capabilities"]["video_text_extraction"])

    def test_delivery_runtime_check_reports_missing_python_media_modules(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-custom"
            ffprobe_bin = Path(tmp_dir) / "ffprobe-custom"
            empty_python_path = Path(tmp_dir) / "python"
            empty_python_path.mkdir()
            (empty_python_path / "sitecustomize.py").write_text(
                "import importlib.util\n"
                "_original_find_spec = importlib.util.find_spec\n"
                "def _blocked_find_spec(name, *args, **kwargs):\n"
                "    if name in {'whisper', 'paddleocr'}:\n"
                "        return None\n"
                "    return _original_find_spec(name, *args, **kwargs)\n"
                "importlib.util.find_spec = _blocked_find_spec\n",
                encoding="utf-8",
            )
            for tool in (ffmpeg_bin, ffprobe_bin):
                tool.write_text("#!/bin/sh\necho \"$0 custom version\"\n", encoding="utf-8")
                tool.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "check_delivery_runtime.py"),
                ],
                cwd=str(ROOT_DIR),
                env={
                    "PATH": "",
                    "PYTHONPATH": str(empty_python_path),
                    "PYTHONNOUSERSITE": "1",
                    "FFMPEG_BIN": str(ffmpeg_bin),
                    "FFPROBE_BIN": str(ffprobe_bin),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["missing_required"], [])
            self.assertIn("whisper_or_paddleocr", payload["missing_python_required"])
            self.assertFalse(payload["capabilities"]["video_text_extraction"])
            self.assertFalse(payload["capabilities"]["long_video_delivery_e2e"])

    def test_delivery_runtime_check_requires_paddle_backend_for_paddleocr(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-custom"
            ffprobe_bin = Path(tmp_dir) / "ffprobe-custom"
            fake_python_path = Path(tmp_dir) / "python"
            fake_python_path.mkdir()
            (fake_python_path / "paddleocr.py").write_text("", encoding="utf-8")
            (fake_python_path / "sitecustomize.py").write_text(
                "import importlib.util\n"
                "_original_find_spec = importlib.util.find_spec\n"
                "def _blocked_find_spec(name, *args, **kwargs):\n"
                "    if name in {'whisper', 'paddle'}:\n"
                "        return None\n"
                "    return _original_find_spec(name, *args, **kwargs)\n"
                "importlib.util.find_spec = _blocked_find_spec\n",
                encoding="utf-8",
            )
            for tool in (ffmpeg_bin, ffprobe_bin):
                tool.write_text("#!/bin/sh\necho \"$0 custom version\"\n", encoding="utf-8")
                tool.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "check_delivery_runtime.py"),
                ],
                cwd=str(ROOT_DIR),
                env={
                    "PATH": "",
                    "PYTHONPATH": str(fake_python_path),
                    "PYTHONNOUSERSITE": "1",
                    "FFMPEG_BIN": str(ffmpeg_bin),
                    "FFPROBE_BIN": str(ffprobe_bin),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "failed")
            python_modules = {item["name"]: item for item in payload["python_modules"]}
            self.assertTrue(python_modules["paddleocr"]["available"])
            self.assertFalse(python_modules["paddle"]["available"])
            self.assertIn("whisper_or_paddleocr", payload["missing_python_required"])
            self.assertFalse(payload["capabilities"]["frame_ocr"])
            self.assertFalse(payload["capabilities"]["video_text_extraction"])

    def test_large_video_delivery_script_reports_missing_video(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_video = Path(tmp_dir) / "missing.mp4"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"),
                    "--video",
                    str(missing_video),
                    "--out-dir",
                    tmp_dir,
                ],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("missing video", completed.stderr or completed.stdout)

    def test_large_video_delivery_script_reports_missing_media_dependency(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_video = Path(tmp_dir) / "sample.mp4"
            fake_video.write_bytes(b"not a real video")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"),
                    "--video",
                    str(fake_video),
                    "--out-dir",
                    str(Path(tmp_dir) / "out"),
                    "--database-url",
                    f"sqlite:///{Path(tmp_dir) / 'video-missing-dependency.db'}",
                ],
                cwd=str(ROOT_DIR),
                env={"PATH": "", "PYTHONPATH": str(BACKEND_DIR)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["reason"], "missing_media_dependency")
            self.assertEqual(payload["dependency"], "ffmpeg")

    def test_media_probe_script_reports_missing_file_as_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_video = Path(tmp_dir) / "missing.mp4"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "probe_media_file.py"),
                    str(missing_video),
                ],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["schema_version"], "envoy.media.probe.v1")
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "missing_file")

    def test_media_probe_script_reports_decode_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "sample.mp4"
            video_path.write_bytes(b"not a real video")
            ffprobe_bin = Path(tmp_dir) / "ffprobe-json"
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-probe"
            ffprobe_bin.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '{\"format\":{\"duration\":\"125.5\",\"format_name\":\"mov,mp4\"},\"streams\":[{\"codec_type\":\"video\",\"codec_name\":\"h264\"},{\"codec_type\":\"audio\",\"codec_name\":\"aac\"}]}'\n",
                encoding="utf-8",
            )
            ffmpeg_bin.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *probe-audio.wav*) printf 'audio decode failed: Invalid NAL unit size\\n' >&2; exit 1 ;;\n"
                "  *probe-frame.jpg*) for last do :; done; printf 'frame' > \"$last\"; exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            ffprobe_bin.chmod(0o755)
            ffmpeg_bin.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "probe_media_file.py"),
                    str(video_path),
                ],
                cwd=str(ROOT_DIR),
                env={**os.environ, "FFMPEG_BIN": str(ffmpeg_bin), "FFPROBE_BIN": str(ffprobe_bin)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["duration_seconds"], 125.5)
            self.assertFalse(payload["checks"]["audio_decode"]["ok"])
            self.assertTrue(payload["checks"]["frame_decode"]["ok"])
            self.assertTrue(payload["can_extract_text"])
            self.assertEqual(payload["streams"]["video"][0]["codec_name"], "h264")
            self.assertEqual(payload["streams"]["audio"][0]["codec_name"], "aac")
            self.assertLessEqual(len(payload["checks"]["audio_decode"]["error"]), 600)

    def test_media_probe_script_fails_when_audio_and_frame_decode_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "sample.sz"
            video_path.write_bytes(b"not a real video")
            ffprobe_bin = Path(tmp_dir) / "ffprobe-json"
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-probe"
            ffprobe_bin.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '{\"format\":{\"duration\":\"310.3\",\"format_name\":\"mov,mp4\"},\"streams\":[{\"codec_type\":\"video\",\"codec_name\":\"h264\"},{\"codec_type\":\"audio\",\"codec_name\":\"aac\"}]}'\n",
                encoding="utf-8",
            )
            ffmpeg_bin.write_text(
                "#!/bin/sh\n"
                "printf 'Invalid NAL unit size\\n%.0s' 1 2 3 4 5 6 7 8 9 10 >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            ffprobe_bin.chmod(0o755)
            ffmpeg_bin.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "probe_media_file.py"),
                    str(video_path),
                ],
                cwd=str(ROOT_DIR),
                env={**os.environ, "FFMPEG_BIN": str(ffmpeg_bin), "FFPROBE_BIN": str(ffprobe_bin)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "media_text_unavailable")
            self.assertFalse(payload["can_extract_text"])
            self.assertIn("audio_decode", payload["failed_checks"])
            self.assertIn("frame_decode", payload["failed_checks"])

    def test_media_collection_probe_summarizes_usable_and_rejected_inputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            media_dir = Path(tmp_dir) / "media"
            media_dir.mkdir()
            usable = media_dir / "usable.mp4"
            rejected = media_dir / "cached.sz"
            ignored = media_dir / "notes.txt"
            usable.write_bytes(b"video")
            rejected.write_bytes(b"cached")
            ignored.write_text("not media", encoding="utf-8")
            ffprobe_bin = Path(tmp_dir) / "ffprobe-json"
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-probe"
            ffprobe_bin.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '{\"format\":{\"duration\":\"90\",\"format_name\":\"mov,mp4\"},\"streams\":[{\"codec_type\":\"video\",\"codec_name\":\"h264\"},{\"codec_type\":\"audio\",\"codec_name\":\"aac\"}]}'\n",
                encoding="utf-8",
            )
            ffmpeg_bin.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  *cached.sz*) printf 'Invalid NAL unit size\\n' >&2; exit 1 ;;\n"
                "  *probe-audio.wav*) for last do :; done; printf 'audio' > \"$last\"; exit 0 ;;\n"
                "  *probe-frame.jpg*) for last do :; done; printf 'frame' > \"$last\"; exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            ffprobe_bin.chmod(0o755)
            ffmpeg_bin.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "probe_media_collection.py"),
                    str(media_dir),
                ],
                cwd=str(ROOT_DIR),
                env={**os.environ, "FFMPEG_BIN": str(ffmpeg_bin), "FFPROBE_BIN": str(ffprobe_bin)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["schema_version"], "envoy.media.collection_probe.v1")
            self.assertEqual(payload["status"], "partial")
            self.assertEqual(payload["summary"]["total_files"], 2)
            self.assertEqual(payload["summary"]["usable_files"], 1)
            self.assertEqual(payload["summary"]["rejected_files"], 1)
            self.assertEqual(Path(payload["usable_inputs"][0]["path"]).name, "usable.mp4")
            self.assertEqual(Path(payload["rejected_inputs"][0]["path"]).name, "cached.sz")
            self.assertEqual(payload["rejected_inputs"][0]["reason"], "media_text_unavailable")
            self.assertEqual(payload["recommended_next_video"], payload["usable_inputs"][0]["path"])

    def test_large_video_delivery_script_reports_missing_python_media_dependency(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_video = Path(tmp_dir) / "sample.mp4"
            fake_video.write_bytes(b"not a real video")
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-custom"
            ffprobe_bin = Path(tmp_dir) / "ffprobe-custom"
            empty_python_path = Path(tmp_dir) / "python"
            empty_python_path.mkdir()
            (empty_python_path / "sitecustomize.py").write_text(
                "import importlib.util\n"
                "_original_find_spec = importlib.util.find_spec\n"
                "def _blocked_find_spec(name, *args, **kwargs):\n"
                "    if name in {'whisper', 'paddleocr'}:\n"
                "        return None\n"
                "    return _original_find_spec(name, *args, **kwargs)\n"
                "importlib.util.find_spec = _blocked_find_spec\n",
                encoding="utf-8",
            )
            for tool in (ffmpeg_bin, ffprobe_bin):
                tool.write_text("#!/bin/sh\necho \"$0 custom version\"\n", encoding="utf-8")
                tool.chmod(0o755)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"),
                    "--video",
                    str(fake_video),
                    "--out-dir",
                    str(Path(tmp_dir) / "out"),
                    "--database-url",
                    f"sqlite:///{Path(tmp_dir) / 'video-missing-python-dependency.db'}",
                ],
                cwd=str(ROOT_DIR),
                env={
                    **os.environ,
                    "PATH": "",
                    "PYTHONPATH": f"{BACKEND_DIR}{os.pathsep}{ROOT_DIR / 'scripts'}{os.pathsep}{empty_python_path}",
                    "FFMPEG_BIN": str(ffmpeg_bin),
                    "FFPROBE_BIN": str(ffprobe_bin),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["reason"], "missing_python_media_dependency")
            self.assertEqual(payload["dependency"], "whisper_or_paddleocr")

    def test_large_video_delivery_script_requires_paddle_backend_for_paddleocr(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e_python_deps", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        original_find_spec = module.importlib.util.find_spec

        def fake_find_spec(name, *args, **kwargs):
            if name == "paddleocr":
                return object()
            if name in {"whisper", "paddle"}:
                return None
            return original_find_spec(name, *args, **kwargs)

        with patch.object(module.importlib.util, "find_spec", side_effect=fake_find_spec):
            self.assertEqual(module._missing_python_media_dependencies(), ["whisper_or_paddleocr"])

    def test_large_video_delivery_script_preprocess_failure_payload_includes_error(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e_failure_payload", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        stored_task = type("Task", (), {"status": "failed", "error_message": "media text extraction unavailable: invalid data"})()
        payload = module._preprocess_failure_payload(
            stored_task,
            {"status": "failed", "error": "media text extraction unavailable: invalid data", "semantic": {"audio_extraction_error": "split_audio failed"}},
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "preprocess_failed")
        self.assertEqual(payload["task_error"], "media text extraction unavailable: invalid data")
        self.assertEqual(payload["result_error"], "media text extraction unavailable: invalid data")
        self.assertEqual(payload["semantic_error"]["audio_extraction_error"], "split_audio failed")

    def test_large_video_delivery_script_stops_when_media_probe_fails(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e_probe_gate", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        probe = {
            "schema_version": "envoy.media.probe.v1",
            "status": "failed",
            "reason": "media_text_unavailable",
            "can_extract_text": False,
            "failed_checks": ["audio_decode", "frame_decode"],
        }
        payload = module._media_probe_failure_payload(probe)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["reason"], "media_probe_failed")
        self.assertEqual(payload["probe"]["reason"], "media_text_unavailable")
        self.assertEqual(payload["probe"]["failed_checks"], ["audio_decode", "frame_decode"])

    def test_large_video_delivery_script_reports_video_below_minimum_duration(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e_duration", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "short.mp4"
            video_path.write_bytes(b"fake short video")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"),
                    "--video",
                    str(video_path),
                    "--out-dir",
                    str(Path(tmp_dir) / "out"),
                    "--database-url",
                    f"sqlite:///{Path(tmp_dir) / 'video-short.db'}",
                    "--min-duration-seconds",
                    "60",
                ],
                cwd=str(ROOT_DIR),
                env={
                    **os.environ,
                    "FFMPEG_BIN": sys.executable,
                    "FFPROBE_BIN": sys.executable,
                    "PYTHONPATH": str(BACKEND_DIR),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["reason"], "video_too_short")
            self.assertEqual(payload["minimum_duration_seconds"], 60)
            self.assertLess(payload["duration_seconds"], 60)

    def test_large_video_delivery_script_reads_duration_with_configured_ffprobe(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e_duration_probe", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp_dir:
            previous = os.environ.get("FFPROBE_BIN")
            ffprobe_bin = Path(tmp_dir) / "ffprobe-json"
            ffprobe_bin.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '{\"format\":{\"duration\":\"125.5\"}}'\n",
                encoding="utf-8",
            )
            ffprobe_bin.chmod(0o755)
            try:
                os.environ["FFPROBE_BIN"] = str(ffprobe_bin)
                self.assertEqual(module._video_duration_seconds(Path(tmp_dir) / "sample.mp4"), 125.5)
            finally:
                if previous is None:
                    os.environ.pop("FFPROBE_BIN", None)
                else:
                    os.environ["FFPROBE_BIN"] = previous

    def test_large_video_delivery_script_honors_configured_media_binaries(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_large_video_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_large_video_delivery_e2e", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp_dir:
            previous = {key: os.environ.get(key) for key in ("PATH", "FFMPEG_BIN", "FFPROBE_BIN")}
            ffmpeg_bin = Path(tmp_dir) / "ffmpeg-custom"
            ffprobe_bin = Path(tmp_dir) / "ffprobe-custom"
            for tool in (ffmpeg_bin, ffprobe_bin):
                tool.write_text("#!/bin/sh\necho \"$0 custom version\"\n", encoding="utf-8")
                tool.chmod(0o755)
            try:
                os.environ["PATH"] = ""
                os.environ["FFMPEG_BIN"] = str(ffmpeg_bin)
                os.environ["FFPROBE_BIN"] = str(ffprobe_bin)

                self.assertEqual(module._missing_media_dependencies(), [])
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_large_pdf_delivery_script_reports_missing_pdf(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_pdf = Path(tmp_dir) / "missing.pdf"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_pdf_delivery_e2e.py"),
                    "--pdf",
                    str(missing_pdf),
                    "--out-dir",
                    tmp_dir,
                ],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("missing pdf", completed.stderr or completed.stdout)

    def test_large_text_delivery_script_generates_and_validates_chunks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_large_text_delivery_e2e.py"),
                    "--out-dir",
                    str(Path(tmp_dir) / "out"),
                    "--database-url",
                    f"sqlite:///{Path(tmp_dir) / 'large-text-delivery.db'}",
                    "--repeat-count",
                    "80",
                ],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["task_status"], "success")
            self.assertEqual(payload["result_status"], "preprocessed")
            self.assertGreaterEqual(payload["unit_count"], 4)
            self.assertGreaterEqual(payload["validator"]["chunk_count"], 4)
            self.assertEqual(payload["validator"]["status"], "ok")
            self.assertTrue(Path(payload["package_root"]).is_dir())

    def test_delivery_e2e_script_generates_and_validates_package(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "run_delivery_package_e2e.py"),
                    "--out-dir",
                    tmp_dir,
                    "--task-id",
                    "delivery-e2e-test",
                ],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["validator"]["status"], "ok")
            self.assertEqual(payload["validator"]["target_profile_count"], 4)
            self.assertGreaterEqual(payload["validator"]["chunk_count"], 3)
            self.assertTrue(Path(payload["package_root"]).is_dir())

    def test_media_collection_delivery_script_builds_batch_plan(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "run_media_collection_delivery_e2e.py"
        spec = importlib.util.spec_from_file_location("run_media_collection_delivery_e2e_plan", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        collection_probe = {
            "status": "partial",
            "usable_inputs": [
                {"path": "/tmp/lesson-a.mp4", "duration_seconds": 120.0, "can_extract_text": True},
            ],
            "rejected_inputs": [
                {"path": "/tmp/private-cache.sz", "reason": "media_text_unavailable", "failed_checks": ["audio_decode", "frame_decode"]},
            ],
        }

        plan = module._build_batch_plan(collection_probe, max_videos=1)

        self.assertEqual(plan["schema_version"], "envoy.media.collection_delivery_plan.v1")
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["summary"]["planned_videos"], 1)
        self.assertEqual(plan["summary"]["rejected_videos"], 1)
        self.assertEqual(plan["planned_inputs"][0]["path"], "/tmp/lesson-a.mp4")
        self.assertEqual(plan["rejected_inputs"][0]["path"], "/tmp/private-cache.sz")

    def test_readiness_report_marks_real_video_blocker_without_failing_core_delivery(self):
        import importlib.util

        script_path = ROOT_DIR / "scripts" / "audit_delivery_readiness.py"
        spec = importlib.util.spec_from_file_location("audit_delivery_readiness", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        report = module._build_readiness_report(
            runtime={"status": "ok", "capabilities": {"long_video_delivery_e2e": True, "large_pdf_ocr_fallback": True}},
            delivery_validator={"status": "ok", "target_profile_count": 4, "chunk_count": 3},
            media_collection={
                "status": "failed",
                "summary": {"total_files": 7, "usable_files": 0, "rejected_files": 7},
                "recommended_next_video": None,
            },
            evidence={"delivery_package": "/tmp/pkg"},
        )

        self.assertEqual(report["schema_version"], "envoy.delivery.readiness.v1")
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["requirements"]["runtime"]["ok"])
        self.assertTrue(report["requirements"]["importable_delivery_package"]["ok"])
        self.assertFalse(report["requirements"]["real_video_acceptance"]["ok"])
        self.assertEqual(report["blockers"][0]["code"], "no_usable_real_video")

    def test_validator_accepts_generated_delivery_package(self):
        from app.services.train_export_service import export_delivery_package

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = export_delivery_package(
                [
                    {
                        "record_type": "semantic_unit",
                        "id": "unit-1",
                        "project_id": "project-1",
                        "asset_id": "asset-1",
                        "unit_type": "scene",
                        "title": "视频开场",
                        "content": json.dumps(
                            {
                                "transcript": {"text": "这里介绍课程目标和实验要求。"},
                                "ocr": [{"text": "课程目标"}],
                                "extraction_status": "partial_success",
                                "degraded_capabilities": ["partial_transcript"],
                                "transcription_failed_segments": [{"segment": "seg-2.mp4", "error": "timeout"}],
                            },
                            ensure_ascii=False,
                        ),
                        "labels": {
                            "source": "media_asset",
                            "source_name": "lesson.mp4",
                            "time_range": ["00:00:00", "00:03:00"],
                            "extraction_status": "partial_success",
                            "degraded_capabilities": ["partial_transcript"],
                            "transcription_failed_count": 1,
                        },
                    }
                ],
                tmp_dir,
                "task-1",
                "课程交付包",
                project_id="project-1",
            )

            completed = subprocess.run(
                [sys.executable, str(ROOT_DIR / "scripts" / "validate_delivery_package.py"), result["root"]],
                cwd=str(ROOT_DIR),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["schema_version"], "envoy.delivery.v1")
            self.assertEqual(payload["import_schema_version"], "envoy.agent.import.v1")
            self.assertGreaterEqual(payload["chunk_count"], 1)
            self.assertEqual(payload["partial_chunk_count"], 1)
            self.assertIn("generic_rag_jsonl", payload["compatible_targets"])
            self.assertEqual(payload["target_profile_count"], 4)


if __name__ == "__main__":
    unittest.main()
