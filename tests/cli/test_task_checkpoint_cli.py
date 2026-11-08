"""A long reading task must survive the loss of the model's context.

These tests pin the CLI face of the task checkpoint: create a task, fold in the
receipts a read actually produced, then ask for the next request from a fresh
process. No socket is opened and nothing is downloaded; every receipt is a
literal, so "resume" here means "hand back the same material without paying for
it twice", not "fetch again".
"""

import json

from envoy_cli import tasks as tasks_module
from envoy_cli.cli import main
from envoy_core import long_task as lt
from envoy_core.schemas.read_v1 import ExpandRequest, ReadRequest

SOURCE = "https://example.test/long.pdf"
OTHER = "https://example.test/other.pdf"
VIDEO = "https://example.com/talk.mp4"
VIDEO_MATERIAL = "mat-" + "a" * 24
TEXT_MATERIAL = "mat-" + "b" * 24


def _run(payload, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    code = main(
        ["task-checkpoint", "--request-json", "-"],
        stdin=json.dumps(payload),
    )
    out = capsys.readouterr().out
    return code, json.loads(out)


def _receipt(*, revision="rev-1", material_id="mat-1", returned=12000, total=60000,
             next_offset=12000, result="partial", errors=None):
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": result,
        "errors": errors or [],
        "blocks": [{"id": "blk-pdf-page-0001", "kind": "page"}],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "total_chars": total,
                        "returned_chars": returned,
                        "next_offset": next_offset,
                        "page_count": 120,
                    }
                }
            }
        },
    }


def _create(monkeypatch, tmp_path, capsys, *, sources=(SOURCE,)):
    code, payload = _run(
        {
            "action": "create",
            "task_id": "task-1",
            "goal": "read the whole document",
            "sources": list(sources),
        },
        monkeypatch, tmp_path, capsys,
    )
    assert code == 0
    return payload


def test_a_task_records_its_sources_and_starts_unread(monkeypatch, tmp_path, capsys):
    created = _create(monkeypatch, tmp_path, capsys)

    assert created["task_id"] == "task-1"
    assert created["state"] == "created"
    assert [item["url"] for item in created["materials"]] == [SOURCE]

    code, fetched = _run({"action": "get", "task_id": "task-1"}, monkeypatch, tmp_path, capsys)
    assert code == 0
    assert fetched["task_id"] == "task-1"


def test_creating_the_same_task_twice_is_refused(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)

    code, payload = _run(
        {"action": "create", "task_id": "task-1", "goal": "again", "sources": [SOURCE]},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 2
    assert payload["result"] == "blocked"
    assert "already exists" in payload["error"]["message"]


def test_a_real_receipt_becomes_the_resume_position(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )
    assert code == 0
    material = applied["materials"][0]
    assert material["state"] == "partial"
    assert material["material_id"] == "mat-1"
    assert material["delivered_ranges"] == [[0, 12000]]

    code, nxt = _run(
        {"action": "next", "task_id": "task-1", "source": SOURCE, "max_chars": 8000},
        monkeypatch, tmp_path, capsys,
    )
    assert code == 0
    assert nxt["result"] == "ready"
    assert nxt["request"]["material_id"] == "mat-1"
    assert nxt["source_revision"] == "rev-1"
    assert "source_revision" not in nxt["request"]
    assert nxt["request"]["offset"] == 12000
    assert "source" not in nxt["request"]


def test_a_second_receipt_only_adds_what_it_really_delivered(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 12000,
         "receipt": _receipt(returned=7977, next_offset=19989)},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    # Touching ranges are merged, so the record never counts the same text twice.
    assert applied["materials"][0]["delivered_ranges"] == [[0, 19977]]


def test_a_moved_source_revision_blocks_the_resume_instead_of_guessing(
    monkeypatch, tmp_path, capsys,
):
    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run(
        {"action": "next", "task_id": "task-1", "source": SOURCE,
         "observed_revision": "rev-2"},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert payload["result"] == "blocked"
    assert payload["error"]["code"] == "source_revision_changed"


def test_a_receipt_from_another_material_marks_the_source_blocked(
    monkeypatch, tmp_path, capsys,
):
    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 12000,
         "receipt": _receipt(material_id="mat-other")},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert applied["blocked"]["code"] == "material_mismatch"
    assert applied["materials"][0]["state"] == "blocked"


def test_one_unreadable_source_does_not_stop_the_other(
    monkeypatch, tmp_path, capsys,
):
    _create(monkeypatch, tmp_path, capsys, sources=(SOURCE, OTHER))
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt(material_id="mat-a")},
        monkeypatch, tmp_path, capsys,
    )

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": OTHER, "offset": 0,
         "receipt": _receipt(material_id="mat-b", returned=0, next_offset=0,
                             result="blocked",
                             errors=[{"code": "network_error"}])},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    by_url = {item["url"]: item for item in applied["materials"]}
    assert by_url[SOURCE]["delivered_ranges"] == [[0, 12000]]
    # A dropped connection is a pause, not an ending: the source stays
    # resumable instead of being written off as failed.
    assert by_url[OTHER]["state"] == "blocked"
    assert by_url[OTHER]["delivered_ranges"] == []


def test_an_unknown_task_is_reported_not_invented(monkeypatch, tmp_path, capsys):
    code, payload = _run({"action": "get", "task_id": "nope"}, monkeypatch, tmp_path, capsys)

    assert code == 2
    assert payload["result"] == "blocked"


def test_list_reports_progress_without_a_lease(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run({"action": "list"}, monkeypatch, tmp_path, capsys)

    assert code == 0
    assert payload["tasks"][0]["task_id"] == "task-1"
    assert payload["tasks"][0]["delivered_chars"] == 12000


def _video_receipt(*, revision="rev-v1", material_id=VIDEO_MATERIAL, returned=480,
                   next_offset=480, total_chars=24000, duration=3600.0,
                   processed_until=120.0, transcription_status="partial",
                   result="partial", text=True):
    """A video ASR receipt: the shape reader.py reports, with a literal timeline."""
    modalities = {}
    if text:
        modalities["text"] = {
            "details": {
                "source": "video_asr",
                "language": "en",
                "transcript_source": "video_asr",
                "total_chars": total_chars,
                "returned_chars": returned,
                "continuation_available": True,
                "next_offset": next_offset,
            }
        }
    modalities["video"] = {
        "details": {
            "video_id": "vid-talk",
            "duration_seconds": duration,
            "transcript_source": "video_asr",
            "requested_range": {"start_seconds": 0.0, "end_seconds": processed_until},
            "transcription_status": transcription_status,
            "processed_until_seconds": processed_until,
            "visual_inspection": "not_performed",
        }
    }
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": result,
        "errors": [],
        "blocks": [{"id": "blk-video-asr-0001", "kind": "transcript"}],
        "coverage": {
            "modalities": modalities,
            "sampled_ranges": [
                {"modality": "audio", "start_seconds": 0.0, "end_seconds": processed_until}
            ],
        },
        "usage": {"local": {"downloaded_bytes": 4096}},
    }


def test_a_partial_video_receipt_resumes_with_a_range_read(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(VIDEO,))
    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO, "offset": 0,
         "receipt": _video_receipt()},
        monkeypatch, tmp_path, capsys,
    )
    assert code == 0
    assert applied["materials"][0]["kind"] == "video"
    assert applied["materials"][0]["state"] == "partial"

    code, payload = _run(
        {"action": "next", "task_id": "task-1", "source": VIDEO,
         "request_id": "req-video-1", "max_chars": 8000},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert payload["result"] == "ready"
    assert payload["action"] == "read"
    assert payload["media"]["processed_seconds"] == 120.0
    assert payload["media"]["total_seconds"] == 3600.0
    assert payload["media"]["remaining_seconds"] == 3480.0
    assert payload["media"]["transcription_status"] == "partial"
    assert payload["media"]["completed_seconds"] == [[0.0, 120.0]]
    assert payload["media"]["downloaded_bytes"] == 4096
    assert payload["media"]["media_calls"] == 1

    request = payload["request"]
    assert request["goal"] == "read the whole document"
    assert request["source"] == {"url": VIDEO}
    assert request["scope"] == {"kind": "range", "start_seconds": 120.0,
                                "end_seconds": 3600.0}
    assert request["retention"]["raw_media"] == "persistent"
    assert request["budget"]["max_response_chars"] == 8000
    ReadRequest.model_validate(request)


def test_a_finished_video_is_blocked_by_the_core_not_the_cli(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(VIDEO,))
    _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO, "offset": 0,
         "receipt": _video_receipt(returned=0, next_offset=0, duration=3600.0,
                                   processed_until=3600.0,
                                   transcription_status="complete",
                                   result="complete")},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run(
        {"action": "next", "task_id": "task-1", "source": VIDEO},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert payload["result"] == "blocked"
    assert payload["error"]["code"] == "already_finished"
    # "action" here is the CLI's own verb; the refusal adds no resume action.
    assert payload["action"] == "next"
    assert "request" not in payload


def test_a_text_material_still_resumes_with_an_expand(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt(material_id=TEXT_MATERIAL)},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run(
        {"action": "next", "task_id": "task-1", "source": SOURCE},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert payload["result"] == "ready"
    assert payload["action"] == "expand"
    assert payload["media"] is None
    assert payload["remaining_seconds"] is None
    assert payload["request"]["material_id"] == TEXT_MATERIAL
    ExpandRequest.model_validate(payload["request"])


def test_get_shows_the_media_axis_and_nulls_it_for_text(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(SOURCE, VIDEO))
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt()},
        monkeypatch, tmp_path, capsys,
    )
    _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO, "offset": 0,
         "receipt": _video_receipt()},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run({"action": "get", "task_id": "task-1"},
                         monkeypatch, tmp_path, capsys)

    assert code == 0
    by_url = {item["url"]: item for item in payload["materials"]}
    assert by_url[SOURCE]["kind"] == "pdf"
    assert by_url[SOURCE]["media"] is None
    assert by_url[SOURCE]["remaining_seconds"] is None
    assert by_url[SOURCE]["is_finished"] is False
    assert by_url[SOURCE]["resume_kind"] == "expand"
    assert by_url[VIDEO]["kind"] == "video"
    assert by_url[VIDEO]["media"]["processed_seconds"] == 120.0
    assert by_url[VIDEO]["media"]["remaining_seconds"] == 3480.0
    assert by_url[VIDEO]["remaining_seconds"] == 3480.0
    assert by_url[VIDEO]["is_finished"] is False
    assert by_url[VIDEO]["resume_kind"] == "read"
    # The stored record carries no lease, so the derived view cannot either.
    assert "lease" not in payload["materials"][0]
    assert "expires_at" not in payload["materials"][0]
    # Nor do the two verdicts: they stay a view of the record, not new fields.
    _, task = tasks_module._load("task-1")
    stored = {item["url"]: item for item in lt.to_checkpoint(task)["materials"]}
    assert "is_finished" not in stored[VIDEO]
    assert "resume_kind" not in stored[VIDEO]
    assert "is_finished" not in stored[SOURCE]
    assert "resume_kind" not in stored[SOURCE]


def test_a_media_receipt_with_no_text_modality_still_folds(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(VIDEO,))

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO, "offset": 0,
         "receipt": _video_receipt(text=False)},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert applied["materials"][0]["state"] == "partial"
    assert applied["materials"][0]["kind"] == "video"

    code, fetched = _run({"action": "get", "task_id": "task-1"},
                         monkeypatch, tmp_path, capsys)
    assert fetched["materials"][0]["media"]["processed_seconds"] == 120.0

    code, payload = _run(
        {"action": "next", "task_id": "task-1", "source": VIDEO},
        monkeypatch, tmp_path, capsys,
    )
    assert code == 0
    assert payload["action"] == "read"
    ReadRequest.model_validate(payload["request"])


def test_apply_defaults_to_offset_zero_for_a_media_receipt(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(VIDEO,))

    code, applied = _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO,
         "receipt": _video_receipt()},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert applied["materials"][0]["delivered_ranges"] == [[0, 480]]

def test_get_says_a_video_is_finished_even_when_chars_remain(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys, sources=(VIDEO,))
    _run(
        {"action": "apply", "task_id": "task-1", "source": VIDEO, "offset": 0,
         "receipt": _video_receipt(result="complete", returned=0, next_offset=0,
                                   processed_until=3600.0,
                                   transcription_status="complete")},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run({"action": "get", "task_id": "task-1"},
                         monkeypatch, tmp_path, capsys)

    assert code == 0
    item = payload["materials"][0]
    assert item["kind"] == "video"
    # The timeline is fully transcribed: that verdict is is_finished, not the
    # state word and not the character count.
    assert item["is_finished"] is True
    assert item["remaining_seconds"] == 0.0
    assert item["resume_kind"] == "expand"


def test_budget_exhausted_receipt_stays_resumable(monkeypatch, tmp_path, capsys):
    """A budget stop must keep the next request reachable, not end the task."""

    _create(monkeypatch, tmp_path, capsys)
    _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": _receipt(returned=2000, next_offset=2000, result="partial",
                             errors=[{"code": "budget_exceeded"}])},
        monkeypatch, tmp_path, capsys,
    )

    code, payload = _run({"action": "get", "task_id": "task-1"},
                         monkeypatch, tmp_path, capsys)

    assert code == 0
    item = payload["materials"][0]
    # The characters that did arrive are still counted, and the pause is not
    # mistaken for the end of the material.
    assert item["state"] == "blocked"
    assert item["is_finished"] is False
    assert item["delivered_ranges"] == [[0, 2000]]

    code, nxt = _run(
        {"action": "next", "task_id": "task-1", "source": SOURCE, "max_chars": 8000},
        monkeypatch, tmp_path, capsys,
    )

    assert code == 0
    assert nxt["result"] == "ready"
    assert nxt["request"]["offset"] == 2000


def test_the_same_receipt_twice_does_not_advance_twice(monkeypatch, tmp_path, capsys):
    _create(monkeypatch, tmp_path, capsys)
    receipt = {**_receipt(), "receipt_id": "receipt-1"}
    first = _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": receipt},
        monkeypatch, tmp_path, capsys,
    )[1]
    second = _run(
        {"action": "apply", "task_id": "task-1", "source": SOURCE, "offset": 0,
         "receipt": receipt},
        monkeypatch, tmp_path, capsys,
    )[1]

    assert first["receipt_id"] == "receipt-1"
    assert "duplicate" not in first
    assert second["duplicate"] is True
    assert second["receipt_id"] == "receipt-1"
    by_url = {item["url"]: item for item in second["materials"]}
    # One delivery, one range: a retried apply must not pay twice.
    assert by_url[SOURCE]["delivered_ranges"] == [[0, 12000]]
    assert by_url[SOURCE]["next_offset"] == 12000
