"""The collection checkpoint must stay reachable from the CLI.

The `core` MCP toolset hides ``envoy_collection_checkpoint``, so a host that
mounts only the core toolset still needs a way to persist a bounded collection
run and resume it. These tests pin the CLI face of that mechanism.
"""

import json

from envoy_cli.cli import main

PROFILE = "https://www.douyin.com/user/abc"


def _run(payload, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    code = main(
        ["collection-checkpoint", "--request-json", "-"],
        stdin=json.dumps(payload),
    )
    return code, json.loads(capsys.readouterr().out)


def test_create_then_get_reports_the_same_run(monkeypatch, tmp_path, capsys):
    create = {
        "action": "create",
        "collection_id": "notes",
        "run_id": "run-1",
        "profile_url": PROFILE,
        "operation_id": "op-create",
    }
    code, created = _run(create, monkeypatch, tmp_path, capsys)
    assert code == 0
    assert created["action"] == "create"
    assert created["profile_url"] == PROFILE
    assert created["manifest_revision"] == 1

    code, fetched = _run(
        {"action": "get", "collection_id": "notes", "run_id": "run-1"},
        monkeypatch,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert fetched["action"] == "get"
    assert fetched["manifest_revision"] == created["manifest_revision"]


def test_a_resume_reads_the_cursor_set_by_an_earlier_run(monkeypatch, tmp_path, capsys):
    _run(
        {
            "action": "create",
            "collection_id": "notes",
            "run_id": "run-1",
            "profile_url": PROFILE,
            "operation_id": "op-create",
        },
        monkeypatch,
        tmp_path,
        capsys,
    )
    resume_at = "candidate-42"
    code, updated = _run(
        {
            "action": "set_cursor",
            "collection_id": "notes",
            "run_id": "run-1",
            "expected_revision": 1,
            "operation_id": "op-cursor",
            "candidate_id": resume_at,
        },
        monkeypatch,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert updated["manifest_revision"] == 2

    code, fetched = _run(
        {"action": "get", "collection_id": "notes", "run_id": "run-1"},
        monkeypatch,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert fetched["cursor"] == {
        "discovery_revision": None,
        "value": resume_at,
    }


def test_a_mutation_without_a_revision_is_refused(monkeypatch, tmp_path, capsys):
    _run(
        {
            "action": "create",
            "collection_id": "notes",
            "run_id": "run-1",
            "profile_url": PROFILE,
            "operation_id": "op-create",
        },
        monkeypatch,
        tmp_path,
        capsys,
    )
    code, payload = _run(
        {
            "action": "set_cursor",
            "collection_id": "notes",
            "run_id": "run-1",
            "operation_id": "op-cursor",
            "candidate_id": "candidate-42",
        },
        monkeypatch,
        tmp_path,
        capsys,
    )
    assert code == 2
    assert payload["result"] == "blocked"
    assert payload["error"]["code"] == "collection_error"


def test_a_collection_needs_a_public_douyin_profile(monkeypatch, tmp_path, capsys):
    code, payload = _run(
        {
            "action": "create",
            "collection_id": "notes",
            "run_id": "run-1",
            "profile_url": "https://example.com/user/abc",
            "operation_id": "op-create",
        },
        monkeypatch,
        tmp_path,
        capsys,
    )
    assert code == 2
    assert payload["result"] == "blocked"
    assert "Douyin" in payload["error"]["message"]
