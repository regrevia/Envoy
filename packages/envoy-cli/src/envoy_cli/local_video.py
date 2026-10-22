from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

from envoy_core.schemas.read_v1 import (
    CoverageItem,
    ErrorCode,
    LocalVideoReadRequest,
    ReadRequest,
    ReadResponse,
    ReadResult,
)

from . import reader
from .media import (
    MediaBudgetExceeded,
    MediaCapabilityMissing,
    MediaNoSpeech,
    MediaProcessingError,
    asr_model_cache_dir,
    media_runtime_status,
    probe_media_duration,
    probe_video_stream,
    transcribe_audio_file,
)
from .operations import run_idempotent


def validate_local_video_path(path: str | Path, *, max_bytes: int) -> Path:
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("Local video input must not be a symbolic links")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Local video input must be a regular file")
    if resolved.stat().st_size <= 0:
        raise ValueError("Local video input is empty")
    if resolved.stat().st_size > int(max_bytes):
        raise ValueError("Local video input exceeds the input byte budget")
    return resolved


def _file_sha256(path: Path, max_work_seconds: int) -> str:
    import time

    deadline = time.monotonic() + max(int(max_work_seconds), 1)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if time.monotonic() >= deadline:
                raise MediaBudgetExceeded("Hashing the local video exceeded max_work_seconds")
            digest.update(chunk)
    return digest.hexdigest()


def _source_url(path: Path, digest: str) -> str:
    return f"envoy-local-video://sha256/{digest}/{quote(path.name, safe='')}"


def _as_read_request(request: LocalVideoReadRequest, source_url: str) -> ReadRequest:
    return ReadRequest.model_validate({
        "request_id": request.request_id,
        "idempotency_key": request.idempotency_key,
        "session_id": request.session_id,
        "source": {
            "url": source_url,
            "kind": "video",
            "label": Path(request.path).name,
        },
        "goal": request.goal,
        "scope": request.scope.model_dump(mode="json"),
        "budget": request.budget.model_dump(mode="json"),
        "retention": request.retention.model_dump(mode="json"),
        "host_capabilities": request.host_capabilities.model_dump(mode="json"),
    })


def _process_local_video(
    request: ReadRequest,
    path: Path,
    source_url: str,
    digest: str,
) -> ReadResponse:
    runtime = media_runtime_status()
    if not runtime.get("ffmpeg") or not runtime.get("ffprobe"):
        return reader._blocked(
            request,
            ErrorCode.MISSING_CAPABILITY,
            "Local video processing requires FFmpeg and FFprobe",
            action_required=True,
        )
    try:
        if not probe_video_stream(
            path,
            max_work_seconds=reader._remaining_work_seconds(request),
        ):
            return reader._blocked(
                request,
                ErrorCode.UNSUPPORTED_SOURCE,
                "The selected local file does not contain a video stream",
            )
        duration = probe_media_duration(
            path,
            max_work_seconds=reader._remaining_work_seconds(request),
        )
    except MediaCapabilityMissing as exc:
        return reader._blocked(request, ErrorCode.MISSING_CAPABILITY, str(exc), action_required=True)
    except MediaBudgetExceeded as exc:
        return reader._blocked(request, ErrorCode.BUDGET_EXCEEDED, str(exc))
    except MediaProcessingError:
        return reader._blocked(
            request,
            ErrorCode.UNSUPPORTED_SOURCE,
            "The selected local file is not a readable video",
        )

    response = None
    unresolved = []
    if runtime.get("asr_installed") and runtime.get("asr_model_available"):
        try:
            transcript = transcribe_audio_file(
                path,
                model_name=str(runtime["asr_model"]),
                download_root=asr_model_cache_dir(),
                max_work_seconds=reader._remaining_work_seconds(request),
                start_seconds=request.scope.start_seconds or 0,
                end_seconds=request.scope.end_seconds or duration,
            )
            cues = transcript.get("segments") or []
            if cues:
                response = reader._video_subtitle_response(
                    request,
                    source_url,
                    {
                        "status": "ready",
                        "source": "asr",
                        "title": path.name,
                        "duration": duration,
                        "language": transcript.get("language"),
                        "model": transcript.get("model"),
                        "transcription_status": transcript.get("status"),
                        "processed_until_seconds": transcript.get("next_seconds"),
                        "asr_duration_ms": transcript.get("elapsed_ms"),
                        "downloaded_bytes": 0,
                        "automatic": False,
                        "available_languages": [],
                        "cues": cues,
                    },
                )
            else:
                unresolved.append("ASR completed without speech segments")
        except MediaNoSpeech as exc:
            unresolved.append(str(exc))
        except (MediaBudgetExceeded, MediaCapabilityMissing, MediaProcessingError) as exc:
            unresolved.append(f"Local ASR did not complete ({type(exc).__name__})")
    elif not runtime.get("asr_installed"):
        unresolved.append("Local video ASR is unavailable because Whisper is not installed")
    else:
        unresolved.append("Local video ASR is unavailable because its Whisper model is not prepared")

    if response is None:
        if not request.scope.include_visual:
            code = (
                ErrorCode.BUDGET_EXCEEDED
                if any("budget" in item.lower() for item in unresolved)
                else ErrorCode.MISSING_CAPABILITY
                if any("unavailable" in item.lower() for item in unresolved)
                else ErrorCode.EMPTY_CONTENT
            )
            return reader._blocked(
                request,
                code,
                "; ".join(unresolved) or "No speech was extracted from the local video",
                action_required=code == ErrorCode.MISSING_CAPABILITY,
            )
        response = reader._video_frame_base_response(
            request,
            source_url,
            path.name,
            duration,
            unresolved or ["Local video did not contain extractable speech"],
        )

    details = dict((response.coverage.modalities.get("video") or CoverageItem()).details)
    details.update({
        "input_kind": "local_file",
        "filename": path.name,
        "sha256": digest,
        "duration_seconds": duration,
        "downloaded_bytes": 0,
    })
    video_coverage = response.coverage.modalities.get("video") or CoverageItem()
    modalities = dict(response.coverage.modalities)
    modalities["video"] = video_coverage.model_copy(update={"details": details})
    response = response.model_copy(update={
        "coverage": response.coverage.model_copy(update={"modalities": modalities}),
    })
    if request.scope.include_visual:
        response = reader._attach_video_frames(
            request,
            source_url,
            response,
            downloaded_media={
                "path": str(path),
                "duration": duration,
                "downloaded_bytes": 0,
            },
        )
    return response


def read_local_video(request: LocalVideoReadRequest) -> ReadResponse:
    try:
        path = validate_local_video_path(
            request.path,
            max_bytes=request.budget.max_download_bytes,
        )
        digest = _file_sha256(path, request.budget.max_work_seconds)
    except (OSError, ValueError, MediaBudgetExceeded) as exc:
        return ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            execution_state="completed",
            result=ReadResult.BLOCKED,
            errors=[{
                "code": (
                    ErrorCode.BUDGET_EXCEEDED
                    if isinstance(exc, MediaBudgetExceeded)
                    else ErrorCode.INVALID_REQUEST
                ),
                "message": (
                    str(exc)
                    if isinstance(exc, (ValueError, MediaBudgetExceeded))
                    else "Could not access the selected local video file"
                ),
            }],
        )
    source_url = _source_url(path, digest)
    internal = _as_read_request(request, source_url)
    fingerprint = hashlib.sha256("\0".join((
        digest,
        request.goal,
        request.scope.model_dump_json(),
        request.budget.model_dump_json(),
        request.retention.model_dump_json(),
        request.host_capabilities.model_dump_json(),
    )).encode()).hexdigest()

    def execute():
        response = _process_local_video(internal, path, source_url, digest)
        return response.model_dump(mode="json")

    if request.idempotency_key:
        try:
            payload = run_idempotent(
                request.idempotency_key,
                fingerprint=fingerprint,
                execute=execute,
                ttl_seconds=request.retention.derived_ttl_seconds,
            )
            response = ReadResponse.model_validate(payload)
            return reader._rebind_cached_response_session(
                response,
                request.session_id,
                request.request_id,
            )
        except ValueError as exc:
            return reader._blocked(internal, ErrorCode.INVALID_REQUEST, str(exc))
    return ReadResponse.model_validate(execute())
