"""The dispatch face of envoy_task: one payload in, one JSON answer out.

``handle`` is what the tool layer calls, so this file pins the round trip
create -> apply -> next for a video source. The receipt below is a literal: no
socket is opened and nothing is transcribed, so the test proves the checkpoint
can be resumed from what a video read reported, not that a video was read.
"""

import json

from envoy_cli.tasks import handle
from envoy_core.schemas.read_v1 import ReadRequest

VIDEO = "https://example.com/talk.mp4"
VIDEO_MATERIAL = "mat-" + "c" * 24


def _video_receipt(
    *,
    revision="rev-v1",
    material_id=VIDEO_MATERIAL,
    returned=480,
    next_offset=480,
    total_chars=24000,
    duration=3600.0,
    processed_until=120.0,
    transcription_status="partial",
    result="partial",
):
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": result,
        "errors": [],
        "blocks": [{"id": "blk-video-asr-0001", "kind": "transcript"}],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "source": "video_asr",
                        "language": "en",
                        "transcript_source": "video_asr",
                        "total_chars": total_chars,
                        "returned_chars": returned,
                        "continuation_available": True,
                        "next_offset": next_offset,
                    }
                },
                "video": {
                    "details": {
                        "video_id": "vid-talk",
                        "duration_seconds": duration,
                        "transcript_source": "video_asr",
                        "requested_range": {
                            "start_seconds": 0.0,
                            "end_seconds": processed_until,
                        },
                        "transcription_status": transcription_status,
                        "processed_until_seconds": processed_until,
                        "visual_inspection": "not_performed",
                    }
                },
            },
            "sampled_ranges": [
                {"modality": "audio", "start_seconds": 0.0, "end_seconds": processed_until}
            ],
        },
        "usage": {"local": {"downloaded_bytes": 4096}},
    }


def test_a_video_task_survives_create_apply_and_next(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))

    created = handle(
        {
            "action": "create",
            "task_id": "video-1",
            "goal": "transcribe the talk",
            "sources": [VIDEO],
        }
    )
    assert created["state"] == "created"
    assert created["materials"][0]["kind"] == ""

    applied = handle(
        {
            "action": "apply",
            "task_id": "video-1",
            "source": VIDEO,
            "offset": 0,
            "receipt": _video_receipt(),
        }
    )
    # The durable answer must be plain JSON: no dataclass leaks out of handle.
    json.dumps(applied)
    assert applied["materials"][0]["state"] == "partial"
    assert applied["materials"][0]["kind"] == "video"

    nxt = handle(
        {"action": "next", "task_id": "video-1", "source": VIDEO, "max_chars": 8000}
    )
    json.dumps(nxt)
    assert nxt["result"] == "ready"
    assert nxt["action"] == "read"
    assert nxt["media"]["processed_seconds"] == 120.0
    assert nxt["media"]["remaining_seconds"] == 3480.0
    assert nxt["request"]["source"] == {"url": VIDEO}
    assert nxt["request"]["goal"] == "transcribe the talk"
    assert nxt["request"]["scope"]["kind"] == "range"
    assert nxt["request"]["retention"]["raw_media"] == "persistent"
    ReadRequest.model_validate(nxt["request"])

    listed = handle({"action": "list"})
    assert listed["tasks"][0]["task_id"] == "video-1"
    assert listed["tasks"][0]["materials"][0]["kind"] == "video"
    assert listed["tasks"][0]["materials"][0]["remaining_seconds"] == 3480.0


def test_a_finished_video_is_not_resumed_again(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    handle(
        {
            "action": "create",
            "task_id": "video-2",
            "goal": "transcribe the talk",
            "sources": [VIDEO],
        }
    )
    handle(
        {
            "action": "apply",
            "task_id": "video-2",
            "source": VIDEO,
            "offset": 0,
            "receipt": _video_receipt(
                returned=0,
                next_offset=0,
                duration=3600.0,
                processed_until=3600.0,
                transcription_status="complete",
                result="complete",
            ),
        }
    )

    nxt = handle({"action": "next", "task_id": "video-2", "source": VIDEO})

    assert nxt["result"] == "blocked"
    assert nxt["error"]["code"] == "already_finished"
    assert "action" not in nxt
