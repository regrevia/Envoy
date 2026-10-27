"""`envoy collect` must hand each child read the budget it actually has left.

A3: the run budget covers discovery plus every per-item read, the remaining
allowance (not the schema default of 180s) goes into the child `ReadRequest`,
each item carries a stable idempotency key, and consumed work is charged back
to the manifest under a stable stage identity.
"""

import time

import pytest

from envoy_cli import collect as collect_module

PROFILE = "https://www.douyin.com/user/creator"


class _Block:
    def __init__(self, text):
        self._text = text

    def model_dump(self, mode="json"):
        return {"kind": "paragraph", "text": self._text}


class _Response:
    def __init__(self, result="ready", material_id="mat-1", text="原文内容"):
        self.result = result
        self.material_id = material_id
        self.source_revision = "rev-1"
        self.expires_at = None
        self.blocks = [_Block(text)]
        self.errors = []


def _candidates(count=4):
    urls = [
        f"https://www.douyin.com/video/70000000000000000{index:02d}"
        for index in range(count)
    ]
    return [
        {
            "candidate_id": f"douyin-{index}",
            "source_url": url,
            "platform": "douyin",
            "item_type": "video",
            "title": f"第 {index} 条",
            "creator": "creator",
            "duration_seconds": 90,
            "published_at": "2026-08-01",
            "position": index,
        }
        for index, url in enumerate(urls)
    ]


def _discovery(monkeypatch, count=4):
    def discover(profile_url, *, limit=20, cursor=None, max_work_seconds=60):
        return {
            "profile_url": profile_url,
            "platform": "douyin",
            "discovery_revision": "d" * 20,
            "cursor_mode": "yt_dlp_flat_metadata",
            "ordering": "source",
            "candidates": _candidates(count)[:limit],
            "has_more": False,
            "next_cursor": None,
            "stop_reason": "source_end_unknown",
            "discovered_at": "2026-10-24T00:00:00+00:00",
        }

    monkeypatch.setattr(collect_module.checkpoint, "discover_profile", discover)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    _discovery(monkeypatch)
    return tmp_path


def test_a_child_read_gets_the_remaining_allowance(data_dir, monkeypatch):
    budgets = []
    keys = []

    def read(request):
        budgets.append(request.budget.max_work_seconds)
        keys.append(request.idempotency_key)
        time.sleep(0.1)
        return _Response()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = data_dir / "notes.md"

    summary = collect_module.collect(PROFILE, out, limit=5, max_work_seconds=5)

    assert summary["completed"] == 4
    assert len(budgets) == 4
    assert all(1 <= budget <= 5 for budget in budgets), budgets
    assert budgets[-1] < 180, "the schema default must never leak into a child read"
    assert keys == [
        f"collect:{summary['collection_id']}:run-1:douyin-{index}" for index in range(4)
    ]


def test_an_exhausted_run_budget_leaves_the_rest_selected(data_dir, monkeypatch):
    calls = []

    def read(request):
        calls.append(request.source.url)
        time.sleep(1.2)
        return _Response()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = data_dir / "notes.md"

    # The child request floor is one second, so a run needs at least that much
    # left before another item is worth starting.
    summary = collect_module.collect(PROFILE, out, limit=4, max_work_seconds=2)

    assert summary["read_now"] == 1
    assert len(calls) == 1
    assert [item["state"] for item in summary["pending"]] == [
        "selected",
        "selected",
        "selected",
    ]


def test_consumed_work_is_charged_under_a_stable_stage_id(data_dir, monkeypatch):
    monkeypatch.setattr(collect_module, "read_public_page", lambda request: _Response())
    out = data_dir / "notes.md"

    summary = collect_module.collect(PROFILE, out, limit=2, max_work_seconds=5)
    manifest = collect_module.checkpoint.get_collection(summary["collection_id"], "run-1")

    assert manifest["usage"]["work_seconds"] >= 1
    stages = [
        operation
        for operation in manifest["applied_operations"]
        if operation.startswith("collect-work-run-1-")
    ]
    assert len(stages) == 3, "discovery plus two items are charged separately"


def test_a_retried_run_reuses_the_same_idempotency_key(data_dir, monkeypatch):
    keys = []

    def read(request):
        keys.append(request.idempotency_key)
        return _Response(result="partial", material_id="mat-partial", text="")

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = data_dir / "notes.md"

    first = collect_module.collect(PROFILE, out, limit=2, max_work_seconds=5)
    first_keys = list(keys)
    keys.clear()
    second = collect_module.collect(
        PROFILE, out, limit=2, max_work_seconds=5, retry_blocked=True
    )

    assert second["resumed"] is True
    assert first["read_now"] == 2 and second["read_now"] == 2
    assert keys == first_keys, "a resumed read must replay the same task identity"


def test_work_usage_survives_a_heartbeat_that_bumped_the_revision(
    data_dir, monkeypatch
):
    """A long item outlives the lease heartbeat; its seconds must still land."""

    real_update = collect_module.checkpoint.update_collection
    raced = []

    def racing_update(collection_id, *, run_id, expected_revision, operation_id,
                      action, **kwargs):
        if action == "record_usage" and not raced:
            raced.append(expected_revision)
            real_update(
                collection_id,
                run_id=run_id,
                expected_revision=expected_revision,
                operation_id="lease-race",
                action="touch_lease",
                candidate_id_value="douyin-0",
            )
            raise collect_module.checkpoint.CollectionRevisionConflict("stale revision")
        return real_update(
            collection_id,
            run_id=run_id,
            expected_revision=expected_revision,
            operation_id=operation_id,
            action=action,
            **kwargs,
        )

    monkeypatch.setattr(
        collect_module.checkpoint, "update_collection", racing_update
    )
    monkeypatch.setattr(collect_module, "read_public_page", lambda request: _Response())
    out = data_dir / "notes.md"

    summary = collect_module.collect(PROFILE, out, limit=2, max_work_seconds=5)
    manifest = collect_module.checkpoint.get_collection(summary["collection_id"], "run-1")

    assert raced, "the race was not exercised"
    assert manifest["usage"]["work_seconds"] >= 2, "both items were charged anyway"
