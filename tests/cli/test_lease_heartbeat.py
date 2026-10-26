"""A long read must hold its item lease; only a processing item may renew it."""

import time

import pytest

from envoy_cli import collect as collect_module
from envoy_cli import collections

PROFILE = "https://www.douyin.com/user/creator"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVOY_AUTH_DIR", str(tmp_path / "auth"))
    return tmp_path


def _candidates():
    return [
        {
            "candidate_id": "cand-1",
            "source_url": "https://www.douyin.com/video/1",
            "title": "one",
            "platform": "douyin",
            "profile_url": PROFILE,
        },
        {
            "candidate_id": "cand-2",
            "source_url": "https://www.douyin.com/video/2",
            "title": "two",
            "platform": "douyin",
            "profile_url": PROFILE,
        },
    ]


def _wire(monkeypatch, *, read_delay=0.0):
    class _Block:
        def model_dump(self, mode="json"):
            return {"kind": "paragraph", "text": "原文"}

    class _Resp:
        result = "ready"
        material_id = "mat-1"
        source_revision = "rev-1"
        expires_at = None
        errors = []

        def __init__(self):
            self.blocks = [_Block()]

    def fake_discover(*args, **kwargs):
        return {
            "profile_url": PROFILE,
            "platform": "douyin",
            "candidates": _candidates(),
            "discovery_revision": "disc-1",
        }

    def fake_read(request):
        if read_delay:
            time.sleep(read_delay)
        return _Resp()

    monkeypatch.setattr(collect_module.checkpoint, "discover_profile", fake_discover)
    monkeypatch.setattr(collect_module, "read_public_page", fake_read)
    return fake_read


def _collect(tmp_path, **kwargs):
    return collect_module.collect(
        PROFILE,
        str(tmp_path / "pack.md"),
        limit=2,
        collection_id="col-lease",
        run_id="run-lease",
        **kwargs,
    )


def test_the_heartbeat_renews_while_a_read_is_still_running(store, tmp_path, monkeypatch):
    _wire(monkeypatch, read_delay=0.2)
    calls = []
    monkeypatch.setattr(
        collect_module.checkpoint, "renew_lease",
        lambda *args: calls.append(args) or {},
    )
    monkeypatch.setattr(collect_module, "_LEASE_INTERVAL_SECONDS", 0.05)

    _collect(tmp_path)

    assert calls, "a read that outlives the interval must renew its lease"
    assert calls[0][:2] == ("col-lease", "run-lease")


def test_renewing_a_processing_item_extends_its_lease(store, tmp_path, monkeypatch):
    _wire(monkeypatch)
    summary = _collect(tmp_path)
    manifest = collections.get_collection("col-lease", "run-lease")
    candidate = "cand-1"
    manifest = collections.update_collection(
        "col-lease", run_id="run-lease",
        expected_revision=int(manifest["manifest_revision"]),
        operation_id="op-processing", action="item_state",
        candidate_id_value=candidate, item_state="processing",
    )
    before = float(manifest["items"][candidate]["lease_expires_at"])
    attempts = int(manifest["items"][candidate]["attempts"])
    time.sleep(0.01)

    renewed = collections.renew_lease("col-lease", "run-lease", candidate)

    assert float(renewed["items"][candidate]["lease_expires_at"]) > before
    assert renewed["items"][candidate]["state"] == "processing"
    assert int(renewed["items"][candidate]["attempts"]) == attempts
    assert summary["completed"] >= 1


def test_an_item_that_is_not_processing_cannot_be_renewed(store, tmp_path, monkeypatch):
    _wire(monkeypatch)
    _collect(tmp_path)

    with pytest.raises(collections.CollectionError):
        collections.renew_lease("col-lease", "run-lease", "cand-1")
