"""Long ASR runs keep one finished chunk instead of losing the whole transcript."""

import json

import pytest

from envoy_cli import media


@pytest.fixture()
def media_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(tmp_path / "asr"))
    monkeypatch.setattr(
        media.importlib.util, "find_spec", lambda name: object() if name == "whisper" else None
    )
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"fake-audio")
    return audio


def _checkpoint(audio, *, status, next_seconds, segments):
    path = media.asr_checkpoint_dir() / f"{media._asr_identity(audio, 'base', 0.0, 100.0)}.json"
    payload = {
        "audio": str(audio),
        "model": "base",
        "start": 0.0,
        "end": 100.0,
        "duration_seconds": 100.0,
        "next_seconds": next_seconds,
        "language": "zh",
        "segments": segments,
        "status": status,
        "updated_at": 0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_finished_transcript_is_reused_without_whisper(media_home, monkeypatch):
    _checkpoint(
        media_home,
        status="complete",
        next_seconds=100.0,
        segments=[{"start": 0.0, "end": 5.0, "text": "你好"}],
    )
    called = []
    monkeypatch.setattr(media, "_transcribe_with_worker", lambda *a, **k: called.append(a))

    result = media.transcribe_audio_file(media_home, end_seconds=100.0)

    assert result["cached"] is True
    assert result["segments"][0]["text"] == "你好"
    assert called == [], "a stored transcript must not start the worker"


def test_a_partial_checkpoint_resumes_at_its_last_chunk(media_home, monkeypatch):
    _checkpoint(
        media_home,
        status="partial",
        next_seconds=60.0,
        segments=[{"start": 0.0, "end": 5.0, "text": "第一段"}],
    )
    seen = {}

    def fake_worker(source, model_name, model_root, start, end, duration, timeout, started,
                    *, checkpoint_path=None, prior_segments=None, resume_from=None, **extra):
        seen.update(
            resume_from=resume_from,
            prior=prior_segments,
            checkpoint=str(checkpoint_path),
        )
        return {"status": "partial", "segments": list(prior_segments or []), "next_seconds": 90.0,
                "language": "zh", "duration_seconds": 100.0, "model": "base", "elapsed_ms": 1}

    monkeypatch.setattr(media, "_transcribe_with_worker", fake_worker)
    monkeypatch.setattr(media, "probe_media_duration", lambda *a, **k: 100.0)

    result = media.transcribe_audio_file(media_home, end_seconds=100.0, max_work_seconds=30)

    assert seen["resume_from"] == 60.0
    assert len(seen["prior"]) == 1
    assert result["cached"] is False
    assert result["segments"][0]["text"] == "第一段"
