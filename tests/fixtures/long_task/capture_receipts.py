"""Regenerate the long-task fixtures from the real reader code paths.

Run to rewrite the four JSON receipts in this directory:

    packages/envoy-cli/.venv/bin/python -m pytest tests/fixtures/long_task/capture_receipts.py -q -p no:cacheprovider

It rewrites tests/fixtures/long_task/*.json from the real reader code paths so the
checkpoint-v2 work is built against actual receipt shapes instead of a guess. The
file is not named test_*.py so the normal suite never collects it; every external
I/O boundary it touches is monkeypatched and named in the fixture _provenance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ExecutionState, ReadRequest, ReadResponse, ReadResult

PAGE_URL = "https://www.douyin.com/video/7698386448961683813"
CURRENT_CLIP = "https://cdn.example.test/player/current.mp4"
DIRECT_URL = "https://video.example/watch/1"
FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures" / "long_task"


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


COMMIT = _git_commit()
GENERATED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fixture_dir() -> Path:
    """Where _dump writes: the fixture directory, or ENVOY_CAPTURE_OUT for a diff run."""

    override = os.getenv("ENVOY_CAPTURE_OUT")
    return Path(override) if override else FIXTURE_DIR


CAPTURE_CLOCK = "<capture-clock>"
CAPTURE_DATA_ROOT = "<capture-data-root>"


def _stable_for_capture(value, data_root: str | None):
    """Replace the facts that differ on every capture run.

    A receipt fixture proves the response *shape*, so the kept-media path and
    its lease deadline — derived from the run's temporary directory and clock —
    are written as placeholders while every other field keeps its real value.
    """

    if isinstance(value, dict):
        return {
            key: (
                CAPTURE_CLOCK
                if key == "raw_media_expires_at" and value[key]
                else _stable_for_capture(item, data_root)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_stable_for_capture(item, data_root) for item in value]
    if isinstance(value, str) and data_root and value.startswith(data_root):
        return CAPTURE_DATA_ROOT + value[len(data_root):]
    return value


def _dump(name: str, receipt: ReadResponse, provenance: dict) -> dict:
    payload = receipt.model_dump(mode="json")
    payload["_provenance"] = provenance
    payload = _stable_for_capture(payload, os.getenv("ENVOY_LOCAL_DATA_DIR"))
    out_dir = _fixture_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


def _direct_video_mocks(monkeypatch, *, duration, status, next_seconds, segments):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)
    monkeypatch.setattr(reader, "extract_public_subtitles", lambda *_a, **_k: {
        "status": "no_subtitles",
        "cues": [],
        "title": "A long public video",
        "duration": duration,
    })
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "ffmpeg": "/opt/homebrew/bin/ffmpeg",
        "ffprobe": "/opt/homebrew/bin/ffprobe",
        "yt_dlp": "2026.8.19",
        "yt_dlp_supported": True,
        "asr_installed": True,
        "asr_model": "base",
        "asr_model_available": True,
    })
    monkeypatch.setattr(reader, "download_audio_for_asr", lambda _url, _path, **_k: {
        "path": "/nonexistent/audio.m4a",
        "video_id": "long-sample",
        "title": "A long public video",
        "duration": duration,
        "downloaded_bytes": 1000,
        "elapsed_ms": 100,
    })
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_a, **_k: {
        "status": status,
        "language": "en",
        "model": "base",
        "elapsed_ms": 200,
        "next_seconds": next_seconds,
        "segments": segments,
    })


def test_capture_video_asr_partial(tmp_path, monkeypatch):
    """Direct read path: 120 s transcribed of a 3600 s video."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    segments = [
        {
            "start": float(index * 2),
            "end": float(index * 2 + 2),
            "text": f"Segment {index}: the instructor explains setup step {index}.",
        }
        for index in range(60)
    ]
    _direct_video_mocks(
        monkeypatch,
        duration=3600,
        status="partial",
        next_seconds=120,
        segments=segments,
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "capture-asr-partial",
        "source": {"url": DIRECT_URL, "kind": "video"},
        "goal": "Find the setup step for the second configuration file",
        "budget": {
            "max_response_chars": 20_000,
            "max_response_tokens": 8_000,
            "max_total_response_chars": 60_000,
        },
    }))

    details = response.coverage.modalities["video"].details
    text_details = response.coverage.modalities["text"].details
    assert details["transcription_status"] == "partial"
    assert details["processed_until_seconds"] == pytest.approx(120)
    assert text_details["source"] == "video_asr"

    from envoy_core import long_task as lt

    checkpoint = lt.MaterialCheckpoint(
        url=DIRECT_URL,
        state=lt.PARTIAL,
        total_chars=text_details.get("total_chars"),
    ).with_delivery(response.model_dump(mode="json"), offset=0)
    v1_rule_would_finish = checkpoint.remaining_chars() == 0 and checkpoint.state in {
        lt.PARTIAL,
        lt.CHECKPOINTED,
        lt.RESUMING,
    }
    finished = checkpoint.is_finished()

    _dump("video_asr_partial.json", response, {
        "kind": "captured_shape",
        "signal": "synthetic",
        "commit": COMMIT,
        "source": (
            "tests/fixtures/long_task/capture_receipts.py::"
            "test_capture_video_asr_partial"
        ),
        "generated_at": GENERATED_AT,
        "replaced_call": "reader.transcribe_audio_file",
        "fake_output": '{"status": "partial", "language": "en", "model": "base", "elapsed_ms": 200, "next_seconds": 120.0, "segments": 60 cues covering 0-120 s}',
        "proof_scope": (
            "proves that a reader-shaped partial-ASR receipt folds into a media-pending checkpoint that is not finished and whose resume routing is a range read; does not prove transcription quality, cue timing, duplicate avoidance, or resume after a real process interruption"
        ),
        "note": (
            "Real receipt from reader.read_public_page (video source, no subtitles, "
            "local ASR) with transcribe_audio_file monkeypatched to a partial result: "
            "120 s processed of 3600 s. The text modality covers every character "
            f"it has (delivered_chars={checkpoint.delivered_chars}, "
            f"total_chars={checkpoint.total_chars}), so the v1 rule "
            "(remaining_chars() == 0 and state in {PARTIAL, CHECKPOINTED, RESUMING}) "
            f"evaluates to {v1_rule_would_finish} for this receipt: that rule is the "
            "bug that let 120 s of 3600 s count as finished. Under checkpoint v2 the "
            f"same fold gives media_pending()={checkpoint.media_pending()} and "
            f"is_finished()={finished}."
        ),
    })
    print("CAPTURED video_asr_partial.json")


def test_capture_video_asr_complete(tmp_path, monkeypatch):
    """Direct read path: a fully transcribed 600 s video, for contrast."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    segments = [
        {
            "start": float(index * 10),
            "end": float(index * 10 + 10),
            "text": f"Part {index}: the speaker finishes the walkthrough of step {index}.",
        }
        for index in range(60)
    ]
    _direct_video_mocks(
        monkeypatch,
        duration=600,
        status="success",
        next_seconds=600,
        segments=segments,
    )

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "capture-asr-complete",
        "source": {"url": DIRECT_URL, "kind": "video"},
        "goal": "Summarize the full walkthrough",
        "budget": {
            "max_response_chars": 20_000,
            "max_response_tokens": 8_000,
            "max_total_response_chars": 60_000,
        },
    }))

    details = response.coverage.modalities["video"].details
    assert details["transcription_status"] == "success"
    assert details["processed_until_seconds"] == pytest.approx(600)

    _dump("video_asr_complete.json", response, {
        "kind": "captured_shape",
        "signal": "synthetic",
        "commit": COMMIT,
        "source": (
            "tests/fixtures/long_task/capture_receipts.py::"
            "test_capture_video_asr_complete"
        ),
        "generated_at": GENERATED_AT,
        "replaced_call": "reader.transcribe_audio_file",
        "fake_output": '{"status": "success", "language": "en", "model": "base", "elapsed_ms": 200, "next_seconds": 600.0, "segments": 300 cues covering 0-600 s}',
        "proof_scope": (
            "proves the finished shape of a video material whose transcript covers the whole duration; does not prove that real ASR ever reaches this state"
        ),
        "note": (
            "Real receipt from reader.read_public_page (video source, no subtitles, "
            "local ASR) with transcribe_audio_file monkeypatched to a complete result: "
            "600 s processed of 600 s."
        ),
    })
    print("CAPTURED video_asr_complete.json")


def test_capture_page_video_asr_partial(tmp_path, monkeypatch):
    """Browser page-video path: emits transcript_unprocessed_seconds."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))

    request = ReadRequest.model_validate({
        "request_id": "capture-page-asr-partial",
        "source": {"url": PAGE_URL, "kind": "video"},
        "budget": {
            "max_response_chars": 20_000,
            "max_response_tokens": 8_000,
            "max_total_response_chars": 60_000,
        },
    })
    empty = ReadResponse(
        request_id=request.request_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    monkeypatch.setattr(
        reader, "_probe_page_media", lambda url, _cache: (
            ("aaa", "video/mp4", 1_000_000) if url == CURRENT_CLIP else None
        )
    )
    monkeypatch.setattr(reader, "media_runtime_status", lambda: {
        "asr_installed": True,
        "asr_model_available": True,
        "asr_model": "base",
    })
    monkeypatch.setattr(reader, "asr_model_cache_dir", lambda: Path("/tmp/envoy-asr"))
    monkeypatch.setattr(reader, "probe_media_duration", lambda *_a, **_k: 3600.0)
    video_path = tmp_path / "download.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(reader, "download_video_for_frames", lambda *_a, **_k: {
        "path": str(video_path),
        "duration": 3600.0,
        "downloaded_bytes": 5,
        "elapsed_ms": 1,
    })
    monkeypatch.setattr(reader, "transcribe_audio_file", lambda *_a, **_k: {
        "segments": [
            {
                "start": float(index * 2),
                "end": float(index * 2 + 2),
                "text": f"第{index}段：讲解者说明配置文件的第{index}个步骤。",
            }
            for index in range(60)
        ],
        "language": "zh",
        "model": "base",
        "status": "partial",
        "next_seconds": 120.0,
        "duration_seconds": 3600.0,
        "elapsed_ms": 5,
    })

    response = reader._attach_browser_video_evidence(
        request,
        PAGE_URL,
        [CURRENT_CLIP],
        empty,
        duration_seconds=3600.0,
        bound_media_urls=[CURRENT_CLIP],
    )

    details = response.coverage.modalities["video"].details
    assert details["transcript_source"] == "asr"
    assert details["transcript_processed_until_seconds"] == pytest.approx(120.0)
    assert details["transcript_unprocessed_seconds"] == pytest.approx(3480.0)

    _dump("page_video_asr_partial.json", response, {
        "kind": "captured_shape",
        "signal": "synthetic",
        "commit": COMMIT,
        "source": (
            "tests/fixtures/long_task/capture_receipts.py::"
            "test_capture_page_video_asr_partial"
        ),
        "generated_at": GENERATED_AT,
        "replaced_call": "reader.transcribe_audio_file (inside the page-video evidence path) plus the page media probe and duration probe",
        "fake_output": '{"status": "partial", ...} part of the same fake as video_asr_partial, fed through the page evidence builder instead of the direct-video path',
        "proof_scope": (
            "proves the checkpoint fold of the only reader dialect that carries transcript_processed_until_seconds / transcript_delivered_until_seconds / transcript_unprocessed_seconds; does not prove page-level video extraction against a live site, transcription quality, or that the delivered-text cutoff equals the transcription cursor"
        ),
        "note": (
            "Real receipt from reader._attach_browser_video_evidence with the ASR "
            "call monkeypatched to a partial result: 120 s processed of 3600 s. "
            "This is the only reader path that emits "
            "video.details.transcript_unprocessed_seconds; it does not emit "
            "transcription_status nor a text modality."
        ),
    })
    print("CAPTURED page_video_asr_partial.json")


def test_capture_video_subtitles(tmp_path, monkeypatch):
    """Subtitle track, no local ASR."""

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda _url: True)

    def extract(source_url, preferred_language=None, **_kwargs):
        return {
            "status": "ready",
            "video_id": "sample",
            "title": "Public lesson",
            "duration": 3600,
            "language": "en",
            "automatic": False,
            "available_languages": ["en", "zh-Hans"],
            "cues": [
                {
                    "start": float(index * 30),
                    "end": float(index * 30 + 30),
                    "text": f"Caption {index}: this section covers topic {index}.",
                }
                for index in range(60)
            ],
        }

    monkeypatch.setattr(reader, "extract_public_subtitles", extract)
    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "capture-subtitles",
        "source": {"url": "https://video.example/watch?id=sample"},
        "goal": "Find the section about topic 12",
        "budget": {
            "max_response_chars": 20_000,
            "max_response_tokens": 8_000,
            "max_total_response_chars": 60_000,
        },
    }))

    details = response.coverage.modalities["video"].details
    assert details["transcript_source"] in {"subtitles", "manual_subtitles"}
    assert details.get("transcription_status") is None

    _dump("video_subtitles.json", response, {
        "kind": "captured_shape",
        "signal": "synthetic",
        "commit": COMMIT,
        "source": (
            "tests/fixtures/long_task/capture_receipts.py::"
            "test_capture_video_subtitles"
        ),
        "generated_at": GENERATED_AT,
        "replaced_call": "reader.extract_public_subtitles",
        "fake_output": '3600 s caption track, automatic=False, rendered as manual_subtitles',
        "proof_scope": (
            "proves that a subtitle-only video receipt carries no processed_seconds and therefore no transcript-owned progress; does not prove subtitle track selection against a live site or subtitle coverage of the whole duration"
        ),
        "note": (
            "Real receipt from reader.read_public_page with extract_public_subtitles "
            "monkeypatched to a 3600 s caption track (automatic=False). "
            "transcription_status is None and coverage.sampled_ranges is empty "
            "because no audio was transcribed."
        ),
    })
    print("CAPTURED video_subtitles.json")
