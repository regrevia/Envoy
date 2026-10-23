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


def test_local_video_reuses_response_persisted_before_operation_commit(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import reader
    from envoy_cli.local_video import _as_read_request, _source_url
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"recoverable-video")
    digest = hashlib.sha256(video_file.read_bytes()).hexdigest()
    source_url = _source_url(video_file, digest)
    request = LocalVideoReadRequest.model_validate({
        "request_id": "recover-request",
        "session_id": "recover-session",
        "path": str(video_file),
    })
    internal = _as_read_request(request, source_url)
    material_id = "mat-" + "d" * 24
    source_revision = "e" * 64
    response = {
        "protocol_version": "envoy.read/1",
        "request_id": request.request_id,
        "session_id": request.session_id,
        "material_id": material_id,
        "source_revision": source_revision,
        "execution_state": "completed",
        "result": "partial",
        "blocks": [],
        "operation_response": None,
    }
    response["operation_response"] = {
        **response,
        "operation_response": None,
    }
    reader._store_material(material_id, {
        "source_url": source_url,
        "source_revision": source_revision,
        "session_id": request.session_id,
        "operation_response": response["operation_response"],
    })

    recovered = reader.recover_operation_response(internal, source_url)

    assert recovered == response["operation_response"]


def test_local_video_retry_recovers_material_without_reprocessing(
    tmp_path,
    monkeypatch,
):
    from envoy_cli import local_video, reader
    from envoy_core.schemas.read_v1 import LocalVideoReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"recoverable-video")
    calls = []

    def interrupted_process(request, _path, source_url, _digest):
        calls.append("process")
        response = {
            "protocol_version": "envoy.read/1",
            "request_id": request.request_id,
            "session_id": request.session_id,
            "material_id": "mat-" + "f" * 24,
            "source_revision": "a" * 64,
            "execution_state": "completed",
            "result": "partial",
            "blocks": [],
            "resources": [],
            "coverage": {},
            "needs": [],
            "errors": [],
            "usage": {},
        }
        reader._store_material(response["material_id"], {
            "source_url": source_url,
            "source_revision": response["source_revision"],
            "session_id": request.session_id,
            "operation_response": response,
        })
        raise KeyboardInterrupt("crash after material persistence")

    monkeypatch.setattr(local_video, "_process_local_video", interrupted_process)
    first = LocalVideoReadRequest.model_validate({
        "request_id": "local-crash-first",
        "idempotency_key": "local-crash-operation",
        "session_id": "local-crash-session",
        "path": str(video_file),
    })

    with pytest.raises(KeyboardInterrupt):
        local_video.read_local_video(first)

    def unexpected_process(*_args):
        calls.append("unexpected-repeat")
        raise AssertionError("retry must recover the persisted response")

    monkeypatch.setattr(local_video, "_process_local_video", unexpected_process)
    retry = first.model_copy(update={"request_id": "local-crash-retry"})
    response = local_video.read_local_video(retry)

    assert response.material_id == "mat-" + "f" * 24
    assert response.request_id == "local-crash-retry"
    assert calls == ["process"]


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


def test_cli_observe_visual_accepts_observe_protocol_and_returns_json(
    monkeypatch,
    capsys,
    tmp_path,
):
    import json
    from envoy_cli import cli

    request = {
        "protocol_version": "envoy.observe/1",
        "request_id": "observe-frame-1",
        "material_id": "mat-" + "a" * 24,
        "session_id": "session-1",
        "source_url": "https://example.org/video",
        "resource_id": "resource-frame-1",
        "source_block_id": "blk-frame-1",
        "observation": "At 00:02 the slide shows a three-column architecture diagram.",
        "locator_quote": "00:02.000",
    }
    monkeypatch.setattr(
        cli,
        "record_visual_observation",
        lambda *args: {
            "persisted": True,
            "observation_count": 1,
            "request": args,
        },
    )

    code = cli.main(
        ["observe-visual", "--request-json", "-"],
        stdin=json.dumps(request),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["protocol_version"] == "envoy.observe/1"
    assert payload["result"] == "ready"
    assert payload["execution_state"] == "completed"
    assert payload["observation_persistence"]["persisted"] is True
    assert payload["observation_persistence"]["observation_count"] == 1
    assert payload["observation_persistence"]["request"] == [
        request["material_id"],
        request["source_url"],
        request["resource_id"],
        request["source_block_id"],
        request["observation"],
        request["locator_quote"],
        request["session_id"],
    ]


def test_cli_observe_visual_rejects_wrong_protocol_version(capsys):
    import json
    from envoy_cli import cli

    code = cli.main(
        ["observe-visual", "--request-json", "-"],
        stdin=json.dumps({
            "protocol_version": "envoy.read/1",
            "request_id": "wrong-protocol",
            "source_url": "https://example.org/video",
            "resource_id": "resource-1",
            "source_block_id": "block-1",
            "observation": "A frame observation.",
        }),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["error"] == "invalid_request"


def test_cli_observe_visual_rejects_credential_bearing_source_url(
    monkeypatch,
    capsys,
):
    import json
    from envoy_cli import cli

    monkeypatch.setattr(
        cli,
        "record_visual_observation",
        lambda *_args: pytest.fail("invalid source URLs must be rejected before persistence"),
    )

    code = cli.main(
        ["observe-visual", "--request-json", "-"],
        stdin=json.dumps({
            "protocol_version": "envoy.observe/1",
            "request_id": "credential-source",
            "source_url": "https://user:secret@example.org/video",
            "resource_id": "resource-1",
            "source_block_id": "block-1",
            "observation": "A frame observation.",
        }),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["error"] == "invalid_request"
    assert "credential-free" in payload["message"]
