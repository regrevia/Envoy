from pathlib import Path
from types import SimpleNamespace
import wave


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
