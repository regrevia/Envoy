import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest


def _write_wav(path, seconds=1.0):
    frame = b"\x00\x00"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(frame * int(16_000 * seconds))


def test_whisper_cpp_is_discovered_when_the_interpreter_has_no_whisper(
    monkeypatch, tmp_path
):
    from envoy_cli import media

    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"ggml")
    monkeypatch.delenv("ENVOY_ASR_BACKEND", raising=False)
    monkeypatch.setenv("ENVOY_WHISPER_CPP", str(binary))
    monkeypatch.setenv("ENVOY_WHISPER_CPP_MODEL_CACHE", str(tmp_path))
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: None)

    assert media.asr_backend() == "whisper.cpp"
    assert media.whisper_cpp_model_path("base") == model
    assert media.whisper_cpp_models() == ["base"]


def test_runtime_status_offers_the_cpp_backend_and_its_model(monkeypatch, tmp_path):
    from envoy_cli import media

    binary = tmp_path / "whisper-cli"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "ggml-base.bin").write_bytes(b"ggml")
    monkeypatch.delenv("ENVOY_ASR_BACKEND", raising=False)
    monkeypatch.setenv("ENVOY_WHISPER_CPP", str(binary))
    monkeypatch.setenv("ENVOY_WHISPER_CPP_MODEL_CACHE", str(tmp_path))
    monkeypatch.setenv("ENVOY_ASR_MODEL", "base")
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(
        media, "shutil", SimpleNamespace(which=lambda _name: "/usr/bin/ffmpeg")
    )
    monkeypatch.setattr(media, "_load_yt_dlp", lambda: object)
    monkeypatch.setattr(media, "version", lambda _name: "2026.8.19")

    status = media.media_runtime_status()

    assert status["asr_backend"] == "whisper.cpp"
    assert status["asr_installed"] is True
    assert status["asr_model"] == "base"
    assert status["asr_model_available"] is True
    assert status["asr_cpp_model"] == str(tmp_path / "ggml-base.bin")
    assert status["asr_models_cached"] == ["base"]


def test_a_wav_chunk_is_transcribed_through_the_whisper_cpp_cli(monkeypatch, tmp_path):
    from envoy_cli import media

    wav_path = tmp_path / "chunk-00000.wav"
    _write_wav(wav_path)

    calls = []

    def fake_run(command, capture_output, timeout):
        calls.append(command)
        prefix = command[command.index("-of") + 1]
        Path(f"{prefix}.json").write_text(
            '{"result": {"language": "zh"}, "transcription": ['
            '{"offsets": {"from": 0, "to": 1200}, "text": " 你好。"}]}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    segments, language = media._transcribe_wav_with_whisper_cpp(
        wav_path,
        binary="/opt/homebrew/bin/whisper-cli",
        model_path=tmp_path / "ggml-base.bin",
        language="",
        workdir=tmp_path,
        chunk_index=0,
        timeout_seconds=60,
    )

    assert language == "zh"
    assert segments == [{"start": 0.0, "end": 1.2, "text": "你好。"}]
    assert calls[0][0] == "/opt/homebrew/bin/whisper-cli"
    assert "-oj" in calls[0]
    assert not wav_path.exists()


def test_a_failing_whisper_cpp_run_is_a_media_processing_error(monkeypatch, tmp_path):
    from envoy_cli import media

    wav_path = tmp_path / "chunk-00001.wav"
    _write_wav(wav_path)
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=3, stderr=b"failed to load model"),
    )

    try:
        media._transcribe_wav_with_whisper_cpp(
            wav_path,
            binary="/opt/homebrew/bin/whisper-cli",
            model_path=tmp_path / "ggml-base.bin",
            language="zh",
            workdir=tmp_path,
            chunk_index=1,
            timeout_seconds=60,
        )
    except media.MediaProcessingError as exc:
        assert "whisper.cpp exited 3" in str(exc)
        assert "failed to load model" in str(exc)
    else:  # pragma: no cover - the failure path is the behaviour under test
        raise AssertionError("a failed whisper.cpp run must not look like success")


ASR_CANONICAL_KEYS = {
    "segments",
    "language",
    "duration",
    "model",
    "backend",
    "processed_until",
    "resume_available",
    "status",
    "elapsed_ms",
    "cached",
    "checkpoint",
}
ASR_LEGACY_KEYS = {"next_seconds", "duration_seconds"}


def _isolated_audio(tmp_path, monkeypatch):
    from envoy_cli import media

    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(tmp_path / "models"))
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio-bytes")
    return media, source


def _install_worker(monkeypatch, media, result):
    seen = {}

    def fake_worker(
        source, model_name, model_root, start, end, duration, timeout, started, **extra
    ):
        seen.update(extra)
        seen["range"] = (start, end, duration)
        return dict(result)

    monkeypatch.setattr(media, "_transcribe_with_worker", fake_worker)
    return seen


def test_both_local_backends_return_the_same_envelope(monkeypatch, tmp_path):
    media, source = _isolated_audio(tmp_path, monkeypatch)
    shapes = []
    for backend in ("whisper.cpp", "openai-whisper"):
        monkeypatch.setattr(media, "asr_backend", lambda value=backend: value)
        worker_result = {
            "status": "success",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.0, "text": "你好"}],
            "next_seconds": 100.0,
            "duration_seconds": 100.0,
            "model": "base",
            "elapsed_ms": 5,
        }
        # Only the whisper.cpp worker reports its engine back; the python backend
        # relies on the parent resolving it.
        if backend == "whisper.cpp":
            worker_result["backend"] = backend
        _install_worker(monkeypatch, media, worker_result)

        result = media.transcribe_audio_file(source, end_seconds=100.0)

        assert set(result) == ASR_CANONICAL_KEYS | ASR_LEGACY_KEYS
        assert result["backend"] == backend
        assert result["status"] == "success"
        assert result["segments"] == worker_result["segments"]
        assert result["processed_until"] == result["next_seconds"] == 100.0
        assert result["duration"] == result["duration_seconds"] == 100.0
        assert result["resume_available"] is False
        assert result["cached"] is False
        assert result["checkpoint"].endswith(".json")
        shapes.append(set(result))
    assert shapes[0] == shapes[1]


def test_a_partial_run_leaves_resume_available(monkeypatch, tmp_path):
    media, source = _isolated_audio(tmp_path, monkeypatch)
    monkeypatch.setattr(media, "asr_backend", lambda: "whisper.cpp")
    _install_worker(
        monkeypatch,
        media,
        {
            "status": "partial",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 1.0, "text": "第一段"}],
            "next_seconds": 60.0,
            "duration_seconds": 100.0,
            "model": "base",
            "elapsed_ms": 3,
        },
    )

    result = media.transcribe_audio_file(source, end_seconds=100.0, max_work_seconds=5)

    assert set(result) == ASR_CANONICAL_KEYS | ASR_LEGACY_KEYS
    assert result["status"] == "partial"
    assert result["processed_until"] == result["next_seconds"] == 60.0
    assert result["resume_available"] is True


def test_a_cached_transcript_fills_the_same_envelope(monkeypatch, tmp_path):
    media, source = _isolated_audio(tmp_path, monkeypatch)
    identity = media._asr_identity(source.resolve(), "base", 0.0, 100.0)
    media._write_asr_checkpoint(
        media.asr_checkpoint_dir() / f"{identity}.json",
        {
            "audio": str(source.resolve()),
            "model": "base",
            "start": 0.0,
            "end": 100.0,
            "duration_seconds": 100.0,
            "next_seconds": 100.0,
            "status": "complete",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 2.0, "text": "整段"}],
        },
    )
    monkeypatch.setattr(media, "asr_backend", lambda: "openai-whisper")
    monkeypatch.setattr(
        media,
        "_transcribe_with_worker",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-run ASR")),
    )

    result = media.transcribe_audio_file(source, end_seconds=100.0)

    assert set(result) == ASR_CANONICAL_KEYS | ASR_LEGACY_KEYS
    assert result["cached"] is True
    assert result["status"] == "success"
    assert result["backend"] == "openai-whisper"
    assert result["segments"] == [{"start": 0.0, "end": 2.0, "text": "整段"}]
    assert result["processed_until"] == result["next_seconds"] == 100.0
    assert result["duration"] == result["duration_seconds"] == 100.0
    assert result["resume_available"] is False


def test_a_missing_backend_is_not_masked_by_the_envelope(monkeypatch, tmp_path):
    media, source = _isolated_audio(tmp_path, monkeypatch)
    monkeypatch.setattr(media, "asr_backend", lambda: None)
    monkeypatch.setattr(
        media,
        "_transcribe_with_worker",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run ASR")),
    )

    try:
        media.transcribe_audio_file(source)
    except media.MediaCapabilityMissing as exc:
        assert "envoy asr install --model base" in str(exc)
    else:  # pragma: no cover - the failure path is the behaviour under test
        raise AssertionError("an unavailable backend must not look like a transcript")


def _real_audio(path: Path, seconds: float) -> Path:
    """A real 16 kHz mono WAV, so FFmpeg/FFprobe run for real in the CLI path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is not installed")
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v", "error",
            "-f", "lavfi",
            "-i", "anullsrc=r=16000:cl=mono",
            "-t", f"{seconds}",
            "-y", str(path),
        ],
        check=True,
    )
    return path


def _fake_whisper_cli(tmp_path: Path) -> Path:
    binary = tmp_path / "whisper-cli"
    binary.write_text(
        "#!/bin/sh\n"
        "out=''\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = '-of' ]; then shift; out=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        "printf '%s' "
        "'{\"result\":{\"language\":\"zh\"},\"transcription\":"
        "[{\"offsets\":{\"from\":0,\"to\":900},\"text\":\" 你好\"}]}' "
        "> \"$out.json\"\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def test_the_whisper_cpp_cli_lands_in_the_same_envelope(monkeypatch, tmp_path):
    """The real CLI path (fake binary, real FFmpeg cut) yields the canonical shape."""
    from envoy_cli import media

    audio = _real_audio(tmp_path / "tone.wav", 2.0)
    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setenv("ENVOY_ASR_BACKEND", "whisper.cpp")
    monkeypatch.setenv("ENVOY_WHISPER_CPP", str(_fake_whisper_cli(tmp_path)))
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"ggml")
    monkeypatch.setenv("ENVOY_WHISPER_CPP_MODEL", str(model))

    result = media.transcribe_audio_file(audio, end_seconds=2.0, max_work_seconds=30)

    assert set(result) == ASR_CANONICAL_KEYS | ASR_LEGACY_KEYS
    assert result["backend"] == "whisper.cpp"
    assert result["status"] == "success"
    assert result["language"] == "zh"
    assert result["segments"] == [{"start": 0.0, "end": 0.9, "text": "你好"}]
    assert result["duration"] == result["duration_seconds"] == 2.0
    assert result["processed_until"] == result["next_seconds"] == 2.0
    assert result["resume_available"] is False
    assert result["cached"] is False
    assert result["checkpoint"].endswith(".json")


def test_the_cpp_cli_and_a_stubbed_python_whisper_agree_on_the_keys(monkeypatch, tmp_path):
    """Same envelope for both local engines, however each one produced its text."""
    from envoy_cli import media

    audio = _real_audio(tmp_path / "tone.wav", 2.0)
    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setenv("ENVOY_ASR_BACKEND", "whisper.cpp")
    monkeypatch.setenv("ENVOY_WHISPER_CPP", str(_fake_whisper_cli(tmp_path)))
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"ggml")
    monkeypatch.setenv("ENVOY_WHISPER_CPP_MODEL", str(model))
    cpp = media.transcribe_audio_file(audio, end_seconds=2.0, max_work_seconds=30)

    # Different source, so the cached whisper.cpp run above cannot answer it.
    python_audio = _real_audio(tmp_path / "tone-python.wav", 2.0)
    monkeypatch.setattr(media, "asr_backend", lambda: "openai-whisper")
    _install_worker(
        monkeypatch,
        media,
        {
            "status": "success",
            "language": "zh",
            "segments": [{"start": 0.0, "end": 0.9, "text": "你好"}],
            "next_seconds": 2.0,
            "duration_seconds": 2.0,
            "model": "base",
            "elapsed_ms": 2,
        },
    )
    python = media.transcribe_audio_file(
        python_audio, end_seconds=2.0, max_work_seconds=30
    )

    assert set(python) == set(cpp) == ASR_CANONICAL_KEYS | ASR_LEGACY_KEYS
    assert python["backend"] == "openai-whisper"
    assert cpp["backend"] == "whisper.cpp"
    assert python["segments"] == cpp["segments"]
    assert python["processed_until"] == cpp["processed_until"] == 2.0
    assert python["resume_available"] is cpp["resume_available"] is False
