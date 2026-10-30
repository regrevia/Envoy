"""Offline checks for durable long-task checkpoints.

Nothing here touches the network: every receipt is a literal, so the module's
promises (resume without re-downloading, no re-delivery, revision honesty) are
pinned without depending on a live source.
"""

from __future__ import annotations

import inspect

import pytest

from envoy_core import long_task as lt


def _read_receipt(
    *,
    material_id="mat-abc",
    revision="rev-1",
    total_chars=60000,
    returned_chars=12000,
    next_offset=12000,
    result="partial",
    blocks=None,
    errors=None,
):
    return {
        "protocol_version": "envoy.read/1",
        "material_id": material_id,
        "source_revision": revision,
        "result": result,
        "errors": errors or [],
        "blocks": blocks or [],
        "coverage": {
            "modalities": {
                "text": {
                    "details": {
                        "total_chars": total_chars,
                        "returned_chars": returned_chars,
                        "next_offset": next_offset,
                        "page_count": 120,
                        "continuation_available": next_offset < total_chars,
                    }
                }
            }
        },
    }


def _pdf_block(page, offset=None):
    suffix = "" if offset is None else "-range-%d" % offset
    return {"id": "blk-pdf-page-%04d%s" % (page, suffix), "kind": "page"}


def test_new_task_has_a_stable_identity_and_no_material_yet():
    task = lt.new_task("task-1", "read the fixture", ["https://example.test/a.pdf"])

    assert task.state == lt.CREATED
    assert task.task_id == "task-1"
    assert task.material("https://example.test/a.pdf").has_material is False
    assert lt.to_checkpoint(task)["task_id"] == "task-1"


def test_a_read_receipt_records_only_what_was_delivered():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf")

    delivered = material.with_delivery(
        _read_receipt(blocks=[_pdf_block(1), _pdf_block(2)]), offset=0
    )

    assert delivered.material_id == "mat-abc"
    assert delivered.source_revision == "rev-1"
    assert delivered.total_chars == 60000
    assert delivered.delivered_chars == 12000
    assert delivered.next_offset == 12000
    assert delivered.pages_seen == (1, 2)
    assert delivered.remaining_chars() == 48000
    assert delivered.state == lt.PARTIAL


def test_a_receipt_without_returned_chars_never_invents_a_range():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf")

    receipt = _read_receipt()
    receipt["coverage"]["modalities"]["text"]["details"].pop("returned_chars")
    delivered = material.with_delivery(receipt, offset=0)

    assert delivered.delivered_ranges == ()
    assert delivered.delivered_chars == 0


def test_checkpoint_round_trip_keeps_the_delivery_record():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(blocks=[_pdf_block(1)]), offset=0
    )
    task = task.with_material(material)

    restored = lt.from_checkpoint(lt.to_checkpoint(task))

    item = restored.material("https://example.test/a.pdf")
    assert restored.task_id == "task-1"
    assert restored.state == lt.PARTIAL
    assert item.material_id == "mat-abc"
    assert item.source_revision == "rev-1"
    assert item.delivered_ranges == ((0, 12000),)
    assert item.next_offset == 12000
    assert item.pages_seen == (1,)


def test_the_checkpoint_holds_no_lease_or_model_session():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    payload = lt.to_checkpoint(task)

    serialized = repr(payload)
    for borrowed in ("lease", "expires_at", "session_id", "token"):
        assert borrowed not in serialized


def test_resume_after_interrupt_expands_instead_of_downloading_again():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(returned_chars=11208, next_offset=11212), offset=0
    )
    task = task.with_material(material)

    restored = lt.from_checkpoint(lt.to_checkpoint(task))
    request = restored.material("https://example.test/a.pdf").resume_request(
        request_id="resume-1", max_chars=8000, max_total_response_chars=48000
    )

    assert request["material_id"] == "mat-abc"
    assert request["source_revision"] == "rev-1"
    assert request["offset"] == 11212
    assert request["max_chars"] == 8000
    assert "source" not in request


def test_resume_refuses_after_the_source_revision_moved():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(), offset=0
    )
    task = task.with_material(material)

    with pytest.raises(lt.TaskBlocked) as caught:
        task.material("https://example.test/a.pdf").resume_request(
            request_id="resume-1", observed_revision="rev-2"
        )

    assert caught.value.code == "source_revision_changed"


def test_a_receipt_from_another_material_cannot_join_this_one():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(), offset=0
    )

    with pytest.raises(lt.TaskBlocked) as caught:
        material.with_delivery(_read_receipt(material_id="mat-other"), offset=12000)

    assert caught.value.code == "material_mismatch"


def test_raising_the_budget_only_asks_for_unseen_ranges():
    material = lt.MaterialCheckpoint(
        url="https://example.test/a.pdf",
        state=lt.PARTIAL,
        material_id="mat-abc",
        source_revision="rev-1",
        total_chars=20000,
        delivered_ranges=((0, 5000), (8000, 11000)),
    )

    request = material.resume_request(
        request_id="resume-2", max_chars=9000, max_total_response_chars=40000
    )

    assert request["offset"] == 5000
    assert lt.unseen_ranges(0, 20000, material.delivered_ranges) == [
        [5000, 8000],
        [11000, 20000],
    ]


def test_unseen_ranges_handles_overlap_touching_and_edges():
    assert lt.unseen_ranges(0, 10, []) == [[0, 10]]
    assert lt.unseen_ranges(0, 10, [(0, 4), (4, 10)]) == []
    assert lt.unseen_ranges(0, 10, [(2, 5)]) == [[0, 2], [5, 10]]
    assert lt.unseen_ranges(3, 6, [(0, 4)]) == [[4, 6]]
    assert lt.unseen_ranges(5, 5, []) == []


def test_one_blocked_material_does_not_touch_the_others_delivery():
    task = lt.new_task(
        "task-1",
        "goal",
        ["https://example.test/a.pdf", "https://example.test/b.pdf"],
    )
    good = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(material_id="mat-a", revision="rev-a"), offset=0
    )
    bad = task.material("https://example.test/b.pdf").with_delivery(
        _read_receipt(
            material_id="mat-b",
            revision="rev-b",
            result="blocked",
            errors=[{"code": "network_error"}],
            returned_chars=0,
        ),
        offset=0,
    )

    task = task.with_material(good).with_material(bad)

    assert task.state == lt.FAILED
    assert task.material("https://example.test/a.pdf").delivered_chars == 12000
    assert task.material("https://example.test/b.pdf").delivered_chars == 0
    assert task.material("https://example.test/b.pdf").errors == ("network_error",)


def test_a_completed_material_cannot_be_resumed_again():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    material = task.material("https://example.test/a.pdf").with_delivery(
        _read_receipt(total_chars=500, returned_chars=500, next_offset=500, result="complete"),
        offset=0,
    )
    task = task.with_material(material)

    assert task.state == lt.COMPLETED
    with pytest.raises(lt.TaskBlocked) as caught:
        material.resume_request(request_id="resume-3")

    assert caught.value.code == "already_finished"


def test_an_unknown_checkpoint_version_is_refused():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    payload = lt.to_checkpoint(task)
    payload["checkpoint_version"] = lt.CHECKPOINT_VERSION + 1

    with pytest.raises(lt.TaskCheckpointError):
        lt.from_checkpoint(payload)


def test_a_corrupt_delivered_range_is_refused():
    task = lt.new_task("task-1", "goal", ["https://example.test/a.pdf"])
    payload = lt.to_checkpoint(task)
    payload["materials"][0]["delivered_ranges"] = [[900, 100]]

    with pytest.raises(lt.TaskCheckpointError):
        lt.from_checkpoint(payload)


def test_the_checkpoint_api_needs_no_lease_to_resume():
    source = inspect.getsource(lt)
    assert "httpx" not in source
    assert "socket" not in source
