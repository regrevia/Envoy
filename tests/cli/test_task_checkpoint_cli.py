"""A long reading task must survive the loss of the model's context.

These tests pin the CLI face of the task checkpoint: create a task, fold in the
receipts a read actually produced, then ask for the next request from a fresh
process. No socket is opened and nothing is downloaded; every receipt is a
literal, so "resume" here means "hand back the same material without paying for
it twice", not "fetch again".
"""

import json

from envoy_cli.cli import main

SOURCE = "https://example.test/long.pdf"
OTHER = "https://example.test/other.pdf"


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
    assert by_url[OTHER]["state"] == "failed"
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
