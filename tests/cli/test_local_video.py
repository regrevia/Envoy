from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))

from envoy_core.schemas.read_v1 import (
    ExecutionState,
    MaterialKind,
    MaterialOrigin,
    ReadResult,
)


def test_local_video_read_transcribes_file_and_uses_private_source_locator(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import local_video, reader
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    video_file = tmp_path / "candidate.mp4"
    video_file.write_bytes(b"local-video-fixture")
    digest = hashlib.sha256(video_file.read_bytes()).hexdigest()
    observed = {}

    monkeypatch.setattr(local_video, "probe_video_stream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(local_video, "probe_media_duration", lambda *_args, **_kwargs: 12.0)
    monkeypatch.setattr(local_video, "media_runtime_status", lambda: {
        "ffmpeg": "/usr/bin/ffmpeg",
        "ffprobe": "/usr/bin/ffprobe",
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })

    def transcribe(path, **kwargs):
        observed["path"] = path
        observed["kwargs"] = kwargs
        return {
            "status": "success",
            "language": "zh",
            "model": "base",
            "duration_seconds": 12,
            "segments": [{"start": 2.0, "end": 4.0, "text": "岗位信息在视频中。"}],
        }

    monkeypatch.setattr(local_video, "transcribe_audio_file", transcribe)
    monkeypatch.setattr(reader, "_material_root", lambda: tmp_path / "materials")
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = local_video.read_local_video(LocalVideoReadRequest.model_validate({
        "request_id": "local-video-read",
        "path": str(video_file),
        "goal": "Extract the spoken job details",
    }))

    transcript = next(block for block in response.blocks if block.origin == MaterialOrigin.ASR)
    locator = transcript.locator
    assert response.result == ReadResult.READY
    assert observed["path"] == video_file.resolve()
    assert transcript.content.text == "[00:02.000] 岗位信息在视频中。"
    assert locator.source_url == f"envoy-local-video://sha256/{digest}/candidate.mp4"
    assert str(video_file) not in response.model_dump_json()
    assert response.coverage.modalities["video"].details["input_kind"] == "local_file"


def test_local_video_idempotency_retries_without_running_asr_twice(tmp_path, monkeypatch):
    from envoy_cli import local_video, reader
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"same-video")
    calls = []
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setattr(local_video, "probe_video_stream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(local_video, "probe_media_duration", lambda *_args, **_kwargs: 8.0)
    monkeypatch.setattr(local_video, "media_runtime_status", lambda: {
        "ffmpeg": "/usr/bin/ffmpeg",
        "ffprobe": "/usr/bin/ffprobe",
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(local_video, "transcribe_audio_file", lambda *_args, **_kwargs: (
        calls.append("asr") or {
            "status": "success",
            "language": "en",
            "model": "base",
            "duration_seconds": 8,
            "segments": [{"start": 1, "end": 2, "text": "A role description."}],
        }
    ))
    monkeypatch.setattr(reader, "_material_root", lambda: tmp_path / "materials")

    first = LocalVideoReadRequest.model_validate({
        "request_id": "local-video-first",
        "idempotency_key": "operation-local-1",
        "session_id": "local-session-a",
        "path": str(video_file),
    })
    retry = first.model_copy(update={
        "request_id": "local-video-retry",
        "session_id": "local-session-b",
    })

    first_response = local_video.read_local_video(first)
    retry_response = local_video.read_local_video(retry)

    assert calls == ["asr"]
    assert retry_response.session_id == "local-session-b"
    assert first_response.material_id != retry_response.material_id
    assert first_response.blocks == retry_response.blocks
    assert reader._load_matching_material(
        retry_response.material_id,
        retry_response.source_revision,
        "local-session-b",
    ) is not None


def test_local_video_rejects_symlinks_and_files_over_input_budget(tmp_path):
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    from envoy_cli.local_video import validate_local_video_path

    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"12345")
    symlink = tmp_path / "linked.mp4"
    symlink.symlink_to(video_file)

    with pytest.raises(ValueError, match="symbolic links"):
        validate_local_video_path(symlink, max_bytes=10)
    with pytest.raises(ValueError, match="input byte budget"):
        validate_local_video_path(video_file, max_bytes=4)

    request = LocalVideoReadRequest.model_validate({
        "request_id": "local-video-protocol",
        "path": str(video_file),
    })
    assert request.path == str(video_file)


def test_local_video_rejects_audio_only_file_before_asr(tmp_path, monkeypatch):
    from envoy_cli import local_video
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"audio")
    monkeypatch.setattr(local_video, "probe_video_stream", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(local_video, "probe_media_duration", lambda *_args, **_kwargs: 12)
    monkeypatch.setattr(local_video, "media_runtime_status", lambda: {
        "ffmpeg": "/usr/bin/ffmpeg",
        "ffprobe": "/usr/bin/ffprobe",
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(local_video, "transcribe_audio_file", lambda *_args, **_kwargs: pytest.fail(
        "audio-only input must not be accepted by the local-video reader"
    ))

    response = local_video.read_local_video(LocalVideoReadRequest.model_validate({
        "request_id": "audio-rejected",
        "path": str(audio_file),
    }))

    assert response.result == ReadResult.BLOCKED
    assert response.errors[0].code.value == "unsupported_source"


def test_local_video_cli_accepts_protocol_request_and_returns_json(
    monkeypatch,
    capsys,
    tmp_path,
):
    import json
    from envoy_cli import cli
    from envoy_core.schemas.read_v1 import ReadResponse

    video_file = tmp_path / "cli-video.mp4"
    video_file.write_bytes(b"fixture")
    monkeypatch.setattr(cli, "read_local_video", lambda request: ReadResponse(
        request_id=request.request_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
    ))

    code = cli.main(
        ["read-local-video", "--request-json", "-"],
        stdin=json.dumps({
            "request_id": "cli-local-video",
            "path": str(video_file),
            "idempotency_key": "cli-op-1",
        }),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["request_id"] == "cli-local-video"
    assert payload["result"] == "partial"
