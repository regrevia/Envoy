"""Offline checks for the media axis of a long-task checkpoint (v2).

Every receipt here is a literal shaped like the one the reader returns for a
video: a coverage block that names the modalities it sampled, and a video
details block that says how far down the timeline the transcript actually got.
Nothing touches the network, so the promise under test is the one that was
broken: a partial transcription of a long video must never look finished.

Two decisions the frozen spec left open are pinned here:

* a refolded or duplicate receipt is counted twice in ``downloaded_bytes`` and
  ``media_calls``: the checkpoint folds receipts and has no call ids, so it
  cannot deduplicate them;
* a video that only delivered subtitles carries no per-second evidence, so it
  never becomes a range read: ``media_pending()`` stays False, ``resume_kind()``
  is ``expand`` and the text continues from the delivered character offset. Its
  ``remaining_seconds()`` is None rather than the whole duration.
"""

from __future__ import annotations

import json

import pytest

from envoy_core import long_task as lt
from envoy_core.schemas import read_v1

URL = "https://example.test/lecture.mp4"
MATERIAL_ID = "mat-%s" % ("a" * 24)


def _video_receipt(
    *,
    material_id=MATERIAL_ID,
    revision="rev-1",
    duration=3600.0,
    processed=120.0,
    start=0.0,
    status="partial",
    result="complete",
    unprocessed=None,
    continuation=None,
    media_downloaded_bytes=None,
    sampled=True,
    text_side=True,
):
    details = {
        "duration_seconds": duration,
        "processed_until_seconds": processed,
        "requested_start_seconds": start,
        "requested_end_seconds": duration,
        "transcription_status": status,
        "transcript_source": "asr",
        "asr_model": "fixture-asr",
    }
    if unprocessed is not None:
        details["transcript_unprocessed_seconds"] = unprocessed
    if continuation is not None:
        details["transcript_continuation_start_seconds"] = continuation
    if media_downloaded_bytes is not None:
        details["media_downloaded_bytes"] = media_downloaded_bytes
    coverage = {"modalities": {"video": {"details": details}}}
    if text_side:
        coverage["modalities"]["text"] = {
            "details": {
                "transcript_source": "asr",
                "total_chars": 5000,
                "returned_chars": 4000,
                "continuation_available": status == "partial",
            }
        }
    if sampled:
        coverage["sampled_ranges"] = [
            {"modality": "audio", "start_seconds": start, "end_seconds": processed}
        ]
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": result,
        "errors": [],
        "blocks": [],
        "coverage": coverage,
        "usage": {"local": {"asr_model": "fixture-asr", "downloaded_bytes": 1024}},
    }


def _text_receipt(*, material_id=MATERIAL_ID, revision="rev-1"):
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": "partial",
        "errors": [],
        "blocks": [{"id": "blk-0001", "kind": "text"}],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "total_chars": 60000,
                        "returned_chars": 12000,
                        "next_offset": 12000,
                        "continuation_available": True,
                    }
                }
            }
        },
    }


def _v1_checkpoint_payload():
    return {
        "checkpoint_version": 1,
        "protocol_version": lt.TASK_PROTOCOL_VERSION,
        "task_id": "task-v1",
        "goal": "read it",
        "state": lt.PARTIAL,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "errors": [],
        "materials": [
            {
                "url": URL,
                "state": lt.PARTIAL,
                "material_id": MATERIAL_ID,
                "source_revision": "rev-1",
                "total_chars": 60000,
                "next_offset": 12000,
                "delivered_ranges": [[0, 12000]],
                "page_count": None,
                "pages_seen": [],
                "errors": [],
            }
        ],
    }


def test_a_partial_video_transcript_never_looks_finished():
    material = lt.MaterialCheckpoint(url=URL)
    folded = material.with_delivery(_video_receipt(), offset=0)

    # The receipt says "complete" and only a fraction of the timeline carries a
    # transcript: the checkpoint has to downgrade itself, not trust the word.
    assert folded.kind == "video"
    assert folded.state == lt.PARTIAL
    assert folded.is_finished() is False
    assert folded.media_pending() is True
    assert folded.processed_seconds == 120.0
    assert folded.total_seconds == 3600.0
    assert folded.remaining_seconds() == 3480.0
    assert folded.completed_seconds == ((0.0, 120.0),)
    assert folded.transcription_status == "partial"


def test_a_partial_video_resumes_with_a_range_read():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(_video_receipt(), offset=0)

    request = folded.resume_request(
        request_id="resume-video", goal="summarise the lecture", max_chars=20000
    )

    parsed = read_v1.ReadRequest.model_validate(request)
    assert parsed.source.url == URL
    assert parsed.goal == "summarise the lecture"
    assert parsed.scope.kind == "range"
    assert parsed.scope.start_seconds == 120.0
    assert parsed.scope.end_seconds == 3600.0
    assert parsed.budget.max_response_chars == 20000
    assert parsed.retention.raw_media == "persistent"
    assert "material_id" not in request
    assert "source_revision" not in request
    assert folded.resume_kind() == "read"


def test_an_unknown_duration_omits_the_range_instead_of_guessing_it():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(
        _video_receipt(duration=None, processed=120.0, sampled=False, start=0.0),
        offset=0,
    )

    request = folded.resume_request(request_id="resume-unknown-length")
    parsed = read_v1.ReadRequest.model_validate(request)

    assert folded.total_seconds is None
    assert "scope" not in request
    assert parsed.scope.kind == "current_resource"
    assert folded.remaining_seconds() is None
    assert folded.is_finished() is False


def test_a_fully_transcribed_video_finishes_and_will_not_resume():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(
        _video_receipt(
            processed=3600.0,
            status="complete",
            unprocessed=0,
        ),
        offset=0,
    )

    assert folded.processed_seconds == 3600.0
    assert folded.remaining_seconds() == 0.0
    assert folded.media_pending() is False
    assert folded.is_finished() is True
    assert folded.state == lt.COMPLETED
    assert folded.resume_kind() == "expand"
    assert folded.media_progress()["remaining_seconds"] == 0.0

    with pytest.raises(lt.TaskBlocked) as caught:
        folded.resume_request(request_id="resume-after-finish")
    assert caught.value.code == "already_finished"


def test_adjacent_ranges_merge_and_each_call_is_counted():
    first = lt.MaterialCheckpoint(url=URL).with_delivery(
        _video_receipt(processed=120.0, media_downloaded_bytes=1000), offset=0
    )
    second = first.with_delivery(
        _video_receipt(
            processed=300.0,
            start=120.0,
            media_downloaded_bytes=2000,
            continuation=300.0,
        ),
        offset=0,
    )

    assert second.completed_seconds == ((0.0, 300.0),)
    assert second.processed_seconds == 300.0
    assert second.media_calls == 2
    assert second.downloaded_bytes == 3000
    assert second.continuation_start_seconds == 300.0
    assert second.remaining_seconds() == 3300.0
    # The second call already asked from 120, so the range grows without a gap.
    assert lt.coverage_seconds(second.completed_seconds) == 300.0


def test_a_v2_checkpoint_round_trips_through_json():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(
        _video_receipt(processed=120.0, media_downloaded_bytes=1000), offset=0
    )
    task = lt.LongTask(
        task_id="task-media",
        goal="transcribe",
        materials=(folded,),
        state=lt.PARTIAL,
    )

    payload = json.loads(json.dumps(lt.to_checkpoint(task)))
    restored = lt.from_checkpoint(payload).material(URL)

    assert payload["checkpoint_version"] == 2
    for field in (
        "kind",
        "total_seconds",
        "processed_seconds",
        "completed_seconds",
        "transcription_status",
        "transcript_source",
        "continuation_start_seconds",
        "unprocessed_seconds",
        "downloaded_bytes",
        "media_calls",
    ):
        assert getattr(restored, field) == getattr(folded, field)
    assert restored.media_progress() == folded.media_progress()


def test_a_v1_checkpoint_still_loads_with_empty_media_fields():
    restored = lt.from_checkpoint(_v1_checkpoint_payload()).material(URL)

    assert restored.kind == ""
    assert restored.total_seconds is None
    assert restored.processed_seconds is None
    assert restored.completed_seconds == ()
    assert restored.media_pending() is False
    assert restored.media_progress() is None
    # The v1 text promise is untouched: it resumes from the delivered offset.
    request = restored.resume_request(request_id="resume-v1")
    parsed = read_v1.ExpandRequest.model_validate(request)
    assert parsed.offset == 12000


def test_a_corrupt_media_range_is_refused_on_load():
    payload = lt.to_checkpoint(
        lt.LongTask(
            task_id="task-bad",
            goal="transcribe",
            materials=(lt.MaterialCheckpoint(url=URL),),
        )
    )
    payload["materials"][0]["completed_seconds"] = [[900.0, 100.0]]

    with pytest.raises(lt.TaskCheckpointError):
        lt.from_checkpoint(payload)


def test_a_text_material_keeps_its_character_axis():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(_text_receipt(), offset=0)

    assert folded.kind == "text"
    assert folded.media_progress() is None
    assert folded.remaining_seconds() is None
    assert folded.media_calls == 0
    assert folded.downloaded_bytes == 0
    assert folded.resume_kind() == "expand"
    assert folded.is_finished() is False

    request = folded.resume_request(request_id="resume-text")
    parsed = read_v1.ExpandRequest.model_validate(request)
    assert parsed.offset == 12000


def _subtitle_video_receipt(*, returned_chars, total_chars=3278, next_offset):
    """The subtitle/ASR dialect: characters delivered, no per-second evidence."""
    return {
        "protocol_version": "envoy.read/1",
        "material_id": MATERIAL_ID,
        "source_revision": "rev-1",
        "result": "ready",
        "errors": [],
        "blocks": [],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "source": "video_subtitles",
                        "transcript_source": "manual_subtitles",
                        "total_chars": total_chars,
                        "returned_chars": returned_chars,
                        "continuation_available": next_offset is not None,
                        "next_offset": next_offset,
                    }
                },
                "video": {
                    "details": {
                        "duration_seconds": 3600.0,
                        "transcript_source": "manual_subtitles",
                    }
                },
            }
        },
        "usage": {},
    }


def test_a_subtitle_only_video_never_resumes_as_a_range_read():
    receipt = _subtitle_video_receipt(returned_chars=2000, next_offset=2000)
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(receipt, offset=0)

    assert folded.kind == "video"
    assert folded.processed_seconds is None
    assert folded.transcription_status is None
    assert folded.completed_seconds == ()
    assert folded.media_pending() is False
    assert folded.resume_kind() == "expand"
    assert folded.remaining_seconds() is None
    assert folded.is_finished() is False

    request = folded.resume_request(request_id="resume-subtitles")
    parsed = read_v1.ExpandRequest.model_validate(request)
    assert parsed.offset == 2000


def test_a_finished_subtitle_only_video_reports_no_seconds_left():
    receipt = _subtitle_video_receipt(returned_chars=3278, next_offset=None)
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(receipt, offset=0)

    assert folded.kind == "video"
    assert folded.remaining_seconds() is None
    assert folded.media_pending() is False
    assert folded.is_finished() is True

    with pytest.raises(lt.TaskBlocked) as blocked:
        folded.resume_request(request_id="resume-subtitles-done")
    assert blocked.value.code == "already_finished"


def test_the_browser_video_processed_cutoff_beats_the_delivered_cutoff():
    receipt = {
        "protocol_version": "envoy.read/1",
        "material_id": MATERIAL_ID,
        "source_revision": "rev-1",
        "result": "partial",
        "errors": [],
        "blocks": [],
        "coverage": {
            "modalities": {
                "video": {
                    "details": {
                        "duration_seconds": 3600.0,
                        "transcript_processed_until_seconds": 300.0,
                        "transcript_delivered_until_seconds": 120.0,
                        "requested_start_seconds": 0.0,
                        "transcription_status": "partial",
                        "transcript_source": "video_asr",
                    }
                },
                "text": {
                    "details": {
                        "total_chars": 24000,
                        "returned_chars": 480,
                        "continuation_available": True,
                        "next_offset": 480,
                    }
                },
            }
        },
        "usage": {},
    }
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(receipt, offset=0)

    assert folded.processed_seconds == 300.0
    assert folded.completed_seconds == ((0.0, 300.0),)
    assert folded.remaining_seconds() == 3300.0
    assert folded.media_pending() is True


def test_a_finished_material_outranks_a_partial_result_word():
    # Mirrors tests/fixtures/long_task/video_asr_complete.json: the receipt says
    # result "partial" while ASR covered all 600 s and the text side delivered
    # every character. The material state keeps the word partial; the task state
    # must follow the evidence.
    receipt = _video_receipt(
        processed=600.0, duration=600.0, status="success", result="partial", sampled=False
    )
    receipt["coverage"]["modalities"]["text"]["details"].update(
        {"returned_chars": 5000, "next_offset": None, "continuation_available": False}
    )
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(receipt, offset=0)
    task = lt.LongTask(task_id="task-complete-video", goal="transcribe", materials=(folded,))

    assert folded.state == lt.PARTIAL
    assert folded.is_finished() is True
    assert lt.overall_state(task) == lt.COMPLETED
    assert task.finished() is True


def test_a_video_with_text_left_stays_partial():
    folded = lt.MaterialCheckpoint(url=URL).with_delivery(
        _video_receipt(processed=600.0, duration=600.0, status="success", result="partial", sampled=False),
        offset=0,
    )
    task = lt.LongTask(task_id="task-text-left", goal="transcribe", materials=(folded,))

    assert folded.media_pending() is False
    assert folded.is_finished() is False
    assert lt.overall_state(task) == lt.PARTIAL
    assert task.finished() is False
