from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from envoy_cli import batch, cli, storage


class _Result:
    def __init__(self, value: str) -> None:
        self.value = value


class _Error:
    def __init__(self, code: str, message: str = "", details=None) -> None:
        self._payload = {"code": code, "message": message}
        if details is not None:
            self._payload["details"] = details

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _Response:
    def __init__(self, result="ready", errors=(), material_id=None, text="") -> None:
        self.result = _Result(result)
        self.errors = list(errors)
        self.material_id = material_id
        self.content = {"text": text}


def _login_response(action_id="auth-abc", resume_token="res-1") -> _Response:
    details = {
        "needs_user_action": {
            "action_id": action_id,
            "resume_token": resume_token,
            "kind": "authenticate",
        }
    }
    return _Response(
        result="blocked",
        errors=[_Error("auth_required", "sign in first", details)],
        text="",
    )


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ENVOY_EXTENSIONS", raising=False)
    storage.reset_root_cache()
    yield tmp_path
    storage.reset_root_cache()


def test_the_same_list_always_gets_the_same_batch_id():
    urls = ["https://example.com/a", "https://example.com/b"]
    first = batch.create_batch(urls)
    second = batch.create_batch(list(urls))

    assert first["batch_id"] == second["batch_id"] == batch.batch_id_for(urls)
    assert second["batch_id"].startswith("batch-")


def test_duplicate_links_collapse_and_order_does_not_matter_for_identity():
    one = batch.create_batch(["https://example.com/a", "https://example.com/a"])
    assert [item["url"] for item in one["items"]] == ["https://example.com/a"]

    other = batch.create_batch(["https://example.com/b", "https://example.com/a"])
    assert other["batch_id"] != one["batch_id"]


def test_a_non_http_link_is_refused_before_any_state_is_written():
    with pytest.raises(batch.BatchError):
        batch.create_batch(["file:///etc/passwd"])


def test_a_run_reads_every_item_and_a_second_run_reads_nothing(roots):
    calls = []

    def reader(request):
        calls.append(request.request_id)
        return _Response(material_id="mat-1", text="hello world")

    summary = batch.run_batch(
        urls=["https://example.com/a", "https://example.com/b"],
        reader=reader,
    )

    assert summary["status"] == "completed"
    assert summary["completed"] == 2
    assert summary["read_now"] == 2
    assert len(calls) == 2

    again = batch.run_batch(summary["batch_id"], reader=reader)
    assert again["read_now"] == 0
    assert again["skipped"] == 2
    assert len(calls) == 2


def test_a_crashing_link_is_recorded_and_the_rest_still_run(roots):
    def reader(request):
        if request.source.url.endswith("/bad"):
            raise RuntimeError("reader exploded")
        return _Response(material_id="mat-2")

    state = batch.create_batch(
        ["https://example.com/bad", "https://example.com/good"], max_attempts=1
    )
    summary = batch.run_batch(state["batch_id"], reader=reader, max_work_seconds=60)

    state = batch.load_batch(summary["batch_id"])
    items = {item["url"]: item for item in state["items"]}
    assert items["https://example.com/bad"]["status"] == "failed"
    assert "reader exploded" in items["https://example.com/bad"]["message"]
    assert items["https://example.com/good"]["status"] == "completed"
    assert summary["status"] == "paused"


def test_an_item_interrupted_by_a_crash_is_queued_again(roots):
    state = batch.create_batch(["https://example.com/a"])
    state["items"][0]["status"] = "in_progress"
    state["items"][0]["attempts"] = 1
    (batch.state_path(state["batch_id"])).write_text(
        json.dumps(state), encoding="utf-8"
    )

    summary = batch.run_batch(state["batch_id"], reader=lambda request: _Response())

    assert summary["recovered"] == 1
    assert summary["completed"] == 1


def test_a_spent_budget_pauses_and_resuming_does_not_reread_finished_items(roots):
    reads = []

    def slow_reader(request):
        reads.append(request.request_id)
        time.sleep(0.55)
        return _Response(material_id="mat-slow")

    summary = batch.run_batch(
        urls=["https://example.com/1", "https://example.com/2", "https://example.com/3"],
        reader=slow_reader,
        max_work_seconds=1,
    )
    assert summary["status"] == "paused"
    assert 1 <= summary["completed"] < 3

    before = len(reads)
    resumed = batch.run_batch(
        summary["batch_id"], reader=slow_reader, max_work_seconds=60
    )
    assert resumed["completed"] == 3
    assert len(reads) - before == 3 - summary["completed"]


def test_a_login_wall_pauses_the_host_and_resumes_after_login(roots):
    blocked = {"value": True}

    def reader(request):
        host = request.source.url.split("/")[2]
        if blocked["value"] and host == "walled.example.com":
            return _login_response()
        return _Response(material_id="mat-ok")

    summary = batch.run_batch(
        urls=["https://walled.example.com/post", "https://open.example.com/post"],
        reader=reader,
        max_work_seconds=60,
    )

    state = batch.load_batch(summary["batch_id"])
    items = {item["url"]: item for item in state["items"]}
    assert items["https://walled.example.com/post"]["status"] == "needs_login"
    assert items["https://open.example.com/post"]["status"] == "completed"
    assert state["login"]["host"] == "walled.example.com"
    assert state["login"]["action_id"] == "auth-abc"
    assert summary["status"] == "paused"
    assert "envoy batch login" in summary["next"]

    skipped = batch.run_batch(summary["batch_id"], reader=reader)
    assert skipped["read_now"] == 0

    blocked["value"] = False
    resumed = batch.run_batch(summary["batch_id"], reader=reader, retry_login=True)
    assert resumed["status"] == "completed"
    assert resumed["completed"] == 2


def test_blocked_without_a_login_handoff_is_not_treated_as_a_login_wall(roots):
    def reader(request):
        return _Response(result="blocked", errors=[_Error("source_unavailable", "404")])

    summary = batch.run_batch(urls=["https://example.com/missing"], reader=reader)

    state = batch.load_batch(summary["batch_id"])
    assert state["items"][0]["status"] == "blocked"
    assert state["login"] is None


def test_a_partial_read_is_kept_as_partial_not_as_complete():
    def reader(request):
        return _Response(result="partial", text="only the first half")

    summary = batch.run_batch(urls=["https://example.com/long"], reader=reader)
    state = batch.load_batch(summary["batch_id"])

    assert state["items"][0]["status"] == "partial"
    assert state["items"][0]["excerpt"] == "only the first half"
    assert summary["completed"] == 0


def test_list_show_report_and_cancel_round_trip(roots):
    summary = batch.run_batch(
        urls=["https://example.com/a"], reader=lambda request: _Response()
    )
    rows = batch.list_batches()
    assert [row["batch_id"] for row in rows] == [summary["batch_id"]]
    assert rows[0]["completed"] == 1

    state = batch.load_batch(summary["batch_id"])
    report = batch.render_report(state)
    assert summary["batch_id"] in report
    assert "https://example.com/a" in report

    batch.cancel_batch(summary["batch_id"])
    assert batch.load_batch(summary["batch_id"])["status"] == "canceled"


def test_unknown_batch_id_is_an_explicit_error(roots):
    with pytest.raises(batch.BatchError):
        batch.load_batch("batch-000000000000")


def test_login_first_opens_the_sign_in_page_and_re_reads_after_signing_in(monkeypatch):
    started = {}
    reads = []

    def fake_create(source_url, **kwargs):
        started["url"] = source_url
        return {"action_id": "auth-test"}

    def fake_start(url, **kwargs):
        started["start"] = kwargs
        return {"status": "screenshot_ready", "action_id": "auth-test", "host": "walled.example.com"}

    monkeypatch.setattr(batch.authflow, "create_auth_action", fake_create)
    monkeypatch.setattr(batch.auth_session, "start", fake_start)
    monkeypatch.setattr(batch.auth_session, "status", lambda action_id: {"status": "signed_in"})
    monkeypatch.setattr(batch.authflow, "update_auth_action", lambda *a, **k: None)

    wall = {"value": True}

    def reader(request):
        reads.append(request.request_id)
        if wall["value"]:
            wall["value"] = False
            return _login_response()
        return _Response(material_id="mat-after-login")

    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest(request_id="r1", source={"url": "https://walled.example.com/x"})
    outcome = batch.login_first(request, reader=reader, timeout_seconds=5)

    assert started["url"] == "https://walled.example.com/x"
    assert outcome["handoff"]["status"] == "screenshot_ready"
    assert outcome["handoff"]["action_id"] == "auth-test"
    assert outcome["signed_in"] is True
    assert outcome["retried"] is True
    assert outcome["response"].material_id == "mat-after-login"


def test_login_first_leaves_a_normal_read_untouched(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("no sign-in page for a normal read")

    monkeypatch.setattr(batch.auth_session, "start", fail)

    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest(request_id="r2", source={"url": "https://example.com/plain"})
    outcome = batch.login_first(request, reader=lambda req: _Response(material_id="mat-plain"))

    assert outcome["handoff"] is None
    assert outcome["signed_in"] is False
    assert outcome["response"].material_id == "mat-plain"


def test_cli_lists_and_shows_a_batch(roots, capsys):
    summary = batch.run_batch(
        urls=["https://example.com/a"], reader=lambda request: _Response()
    )

    assert cli.main(["batch", "--list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["batches"][0]["batch_id"] == summary["batch_id"]

    assert cli.main(["batch", "--show", summary["batch_id"], "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["items"][0]["url"] == "https://example.com/a"


def test_cli_batch_without_a_link_explains_what_is_missing(roots, capsys):
    assert cli.main(["batch", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_request"


def test_cli_batch_resume_of_an_unknown_batch_is_an_error(roots, capsys):
    assert cli.main(["batch", "--resume", "batch-000000000000", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert "batch-000000000000" in payload["message"]


# The audit contract: every item carries these fields, and they must survive a
# round trip through state.json, not just live in the returned dict.
AUDIT_FIELDS = (
    "batch_id",
    "item_id",
    "attempt_id",
    "material_id",
    "status",
    "started_at",
    "finished_at",
    "work_seconds",
    "error_code",
)


def _raw_state(batch_id: str) -> dict:
    """Read state.json as it is on disk, bypassing the load-time normalisation."""

    return json.loads(batch.state_path(batch_id).read_text(encoding="utf-8"))


def test_a_new_batch_writes_every_audit_field_into_the_state_file(roots):
    state = batch.create_batch(["https://example.com/a", "https://example.com/b"])

    raw = _raw_state(state["batch_id"])

    assert len(raw["items"]) == 2
    for item in raw["items"]:
        for field in AUDIT_FIELDS:
            assert field in item, field
        assert item["batch_id"] == raw["batch_id"]
        assert item["item_id"] == batch.item_id_for(raw["batch_id"], item["index"])
        assert item["attempt_id"] is None
        assert item["status"] == "pending"
        assert item["material_id"] is None
        assert item["error_code"] is None
        assert item["started_at"] is None
        assert item["finished_at"] is None
        assert item["work_seconds"] == 0.0
        assert item["attempts_log"] == []
        # The attempt counter stays a plain int; other tooling reads it directly.
        assert isinstance(item["attempts"], int) and item["attempts"] == 0


def test_item_and_attempt_ids_are_derived_from_position_not_generated(roots):
    urls = ["https://example.com/a", "https://example.com/b"]

    first = batch.create_batch(urls, fresh=True)
    second = batch.create_batch(urls, fresh=True)

    assert [item["item_id"] for item in first["items"]] == [
        item["item_id"] for item in second["items"]
    ]
    assert first["items"][0]["item_id"] == batch.item_id_for(first["batch_id"], 0)
    assert first["items"][1]["item_id"] == batch.item_id_for(first["batch_id"], 1)
    assert batch.attempt_id_for(first["batch_id"], 1, 3) == (
        f"{batch.item_id_for(first['batch_id'], 1)}:3"
    )


def test_every_attempt_is_logged_in_the_state_file(roots):
    calls = []

    def flaky(request):
        calls.append(request.request_id)
        if len(calls) == 1:
            raise RuntimeError("first try exploded")
        return _Response(material_id="mat-9", text="ok")

    state = batch.create_batch(["https://example.com/a"], max_attempts=2)
    first = batch.run_batch(state["batch_id"], reader=flaky)
    assert first["status"] == "paused"

    second = batch.run_batch(state["batch_id"], reader=flaky)
    assert second["status"] == "completed"

    item = _raw_state(state["batch_id"])["items"][0]
    assert item["attempts"] == 2
    assert item["attempt_id"] == batch.attempt_id_for(state["batch_id"], 0, 2)
    log = item["attempts_log"]
    assert [entry["attempt"] for entry in log] == [1, 2]
    assert [entry["attempt_id"] for entry in log] == [
        batch.attempt_id_for(state["batch_id"], 0, 1),
        batch.attempt_id_for(state["batch_id"], 0, 2),
    ]
    assert log[0]["status"] == "pending"
    assert log[0]["error_code"] == "reader_error"
    assert log[1]["status"] == "completed"
    assert log[1]["material_id"] == "mat-9"
    for entry in log:
        assert entry["started_at"] and entry["finished_at"]
        assert entry["work_seconds"] >= 0.0
    # Timestamps are ISO-8601 with a timezone, so two machines agree on them.
    assert datetime.fromisoformat(log[0]["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(item["finished_at"]).tzinfo is not None
    # The flattened fields describe the latest attempt.
    assert item["status"] == "completed"
    assert item["finished_at"] == log[1]["finished_at"]


def test_an_oversized_attempt_log_is_trimmed_when_the_batch_is_loaded(roots):
    state = batch.create_batch(["https://example.com/a"])
    raw = _raw_state(state["batch_id"])
    raw["items"][0]["attempts_log"] = [
        {"attempt_id": f"a{n}", "attempt": n}
        for n in range(batch.MAX_ATTEMPT_LOG_ENTRIES + 5)
    ]
    batch.state_path(state["batch_id"]).write_text(json.dumps(raw), encoding="utf-8")

    loaded = batch.load_batch(state["batch_id"])

    log = loaded["items"][0]["attempts_log"]
    assert len(log) == batch.MAX_ATTEMPT_LOG_ENTRIES
    # The newest attempts are the ones kept.
    assert log[-1]["attempt"] == batch.MAX_ATTEMPT_LOG_ENTRIES + 4


def test_a_state_file_from_an_older_build_loads_and_gains_the_audit_fields(roots):
    state = batch.create_batch(["https://example.com/a"])
    raw = _raw_state(state["batch_id"])
    for field in AUDIT_FIELDS + ("attempts_log",):
        raw["items"][0].pop(field, None)
    raw["items"][0]["status"] = "completed"
    raw["items"][0]["message"] = "kept as written"
    batch.state_path(state["batch_id"]).write_text(json.dumps(raw), encoding="utf-8")

    loaded = batch.load_batch(state["batch_id"])

    item = loaded["items"][0]
    # Missing fields are filled; the values the file does carry are untouched.
    assert item["status"] == "completed"
    assert item["message"] == "kept as written"
    assert item["item_id"] == batch.item_id_for(loaded["batch_id"], 0)
    assert item["batch_id"] == loaded["batch_id"]
    assert item["attempt_id"] is None
    assert item["attempts_log"] == []
    assert item["work_seconds"] == 0.0
