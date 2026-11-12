"""Knowledge units: a candidate never becomes verified without resolving evidence."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from envoy_cli import cli, evidence_pack, knowledge_units, storage

MATERIAL = "mat-" + "1" * 24
REVISION = "rev-1"


@pytest.fixture(autouse=True)
def material_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    return root


def _seed_material(root: Path, *, revision: str = REVISION) -> list[dict]:
    record = {
        "source_url": "https://example.com/a",
        "source_revision": revision,
        "session_id": "session-a",
        "source_kind": "web",
        "title_text": "A page worth citing",
        "text": "First paragraph. Second paragraph.",
        "blocks": [
            {
                "id": "blk-0001",
                "order": 1,
                "content": {"text": "First paragraph."},
                "locator": {"type": "web", "source_url": "https://example.com/a", "block_id": "blk-0001"},
            },
            {
                "id": "blk-0002",
                "order": 2,
                "content": {"text": "Second paragraph."},
                "locator": {"type": "web", "source_url": "https://example.com/a", "block_id": "blk-0002"},
            },
        ],
    }
    (root / f"{MATERIAL}.json").write_text(json.dumps(record), encoding="utf-8")
    (root / f"{MATERIAL}.expires").write_text(str(time.time() + 600), encoding="utf-8")
    stored = evidence_pack.load_material(MATERIAL, root=root)
    return evidence_pack.build_source_document(stored, MATERIAL)["blocks"]


def _payload(blocks: list[dict], **overrides) -> dict:
    payload = {
        "type": "principle",
        "title": "Cite the block, not the impression",
        "summary": "A citation names a block and a place in the source.",
        "conditions": ["the material is retained"],
        "steps": ["open the block", "quote it"],
        "limitations": ["only as good as the stored revision"],
        "source_refs": [
            {
                "material_id": MATERIAL,
                "evidence_id": block["evidence_id"],
                "locator": block["locator"],
                "source_revision": REVISION,
            }
            for block in blocks[:1]
        ],
        "tests": [
            {"kind": "positive", "question": "Does the quote carry a locator?"},
            {"kind": "boundary", "question": "Is the block still in the material?"},
        ],
    }
    payload.update(overrides)
    return payload


def test_a_candidate_carries_its_source_and_starts_unverified():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))

    assert knowledge_units.UNIT_ID.fullmatch(unit["unit_id"])
    assert unit["status"] == "candidate"
    assert unit["version"] == 1
    assert unit["verified_at"] is None
    assert unit["source_refs"][0]["material_id"] == MATERIAL
    assert knowledge_units.list_units() == [unit]


def test_identical_input_does_not_create_a_second_unit():
    blocks = _seed_material(storage.resolve_material_root())
    first = knowledge_units.create_unit(_payload(blocks))
    second = knowledge_units.create_unit(_payload(blocks))
    assert first["unit_id"] == second["unit_id"]
    assert len(knowledge_units.list_units()) == 1


def test_a_quote_without_a_place_is_refused():
    blocks = _seed_material(storage.resolve_material_root())
    ref = dict(_payload(blocks)["source_refs"][0])
    ref.pop("locator")
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.create_unit(_payload(blocks, source_refs=[ref]))
    assert refusal.value.code == "invalid_unit"
    assert "no locator" in str(refusal.value)


def test_verify_resolves_every_ref_against_the_material():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))

    verified = knowledge_units.verify_unit(unit["unit_id"])
    assert verified["status"] == "verified"
    assert verified["verified_at"]
    assert verified["verified_against"]
    assert [entry["change"] for entry in verified["history"]] == ["created", "verified"]


def test_verify_refuses_evidence_the_material_does_not_hold():
    blocks = _seed_material(storage.resolve_material_root())
    bogus = _payload(blocks)
    bogus["source_refs"][0]["evidence_id"] = "ev-" + "0" * 16
    unit = knowledge_units.create_unit(bogus)

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.verify_unit(unit["unit_id"])
    assert refusal.value.code == "evidence_unresolved"
    assert "is not in" in refusal.value.details["problems"][0]
    assert knowledge_units.load_unit(unit["unit_id"])["status"] == "candidate"


def test_verify_notices_that_the_material_moved():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    _seed_material(storage.resolve_material_root(), revision="rev-2")

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.verify_unit(unit["unit_id"])
    assert "revision moved" in refusal.value.details["problems"][0]


def test_verify_refuses_a_locator_that_no_longer_matches():
    blocks = _seed_material(storage.resolve_material_root())
    payload = _payload(blocks)
    payload["source_refs"][0]["locator"] = {
        "type": "web",
        "source_url": "https://example.com/a",
        "block_id": "blk-0002",
    }
    unit = knowledge_units.create_unit(payload)

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.verify_unit(unit["unit_id"])
    assert "locator does not match" in refusal.value.details["problems"][0]


def test_promote_needs_a_verified_unit_with_all_its_parts():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.promote_unit(unit["unit_id"])
    assert refusal.value.code == "needs_verification"

    knowledge_units.verify_unit(unit["unit_id"])
    promoted = knowledge_units.promote_unit(unit["unit_id"])
    assert promoted["status"] == "promoted"


def test_edits_version_forward_and_never_rewrite_the_file_away():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    edited = knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})

    assert edited["unit_id"] == unit["unit_id"]
    assert edited["version"] == 2
    assert edited["summary"] == "A sharper summary."
    assert edited["status"] == "candidate"
    assert [entry["change"] for entry in edited["history"]] == ["created", "updated"]
    snapshot = knowledge_units._snapshot_path(unit["unit_id"], 1)
    assert json.loads(snapshot.read_text(encoding="utf-8"))["summary"] == unit["summary"]


def test_verified_edits_lose_their_verification():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    knowledge_units.verify_unit(unit["unit_id"])
    edited = knowledge_units.update_unit(unit["unit_id"], {"title": "A different claim"})
    assert edited["status"] == "candidate"
    assert edited["verified_at"] is None
    assert edited["verified_against"] is None


def test_rollback_restores_an_older_body_as_a_new_version():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})

    rolled = knowledge_units.rollback_unit(unit["unit_id"], 1)
    assert rolled["version"] == 3
    assert rolled["summary"] == unit["summary"]
    assert rolled["history"][-1]["change"] == "rolled_back"
    assert knowledge_units.load_unit(unit["unit_id"])["version"] == 3


def test_rollback_to_a_version_that_was_never_written_is_refused():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.rollback_unit(unit["unit_id"], 7)
    assert refusal.value.code == "invalid_version"


def test_archive_keeps_the_record_and_stops_verification():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    archived = knowledge_units.archive_unit(unit["unit_id"], reason="superseded")

    assert archived["status"] == "archived"
    assert archived["history"][-1]["reason"] == "superseded"
    assert knowledge_units.list_units(status="archived") == [archived]
    assert knowledge_units.load_unit(unit["unit_id"])["status"] == "archived"
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.verify_unit(unit["unit_id"])
    assert refusal.value.code == "unit_archived"


def test_two_units_relate_only_through_evidence_one_already_cites():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(_payload([blocks[1]], title="A related claim"))

    related = knowledge_units.relate_units(
        unit["unit_id"],
        "supports",
        other["unit_id"],
        [{"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}],
    )
    assert related["relations"][0]["to"] == other["unit_id"]
    assert related["relations"][0]["type"] == "supports"

    with pytest.raises(knowledge_units.UnitError) as duplicate:
        knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], related["relations"][0]["evidence"])
    assert duplicate.value.code == "relation_exists"


def test_cli_writes_nothing_until_the_extension_is_on(tmp_path, capsys, monkeypatch):
    blocks = _seed_material(storage.resolve_material_root())
    body = tmp_path / "unit.json"
    body.write_text(json.dumps(_payload(blocks)), encoding="utf-8")
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))

    assert cli.main(["knowledge", "create", "--file", str(body)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "extension_disabled"
    assert knowledge_units.list_units() == []

    monkeypatch.setenv("ENVOY_EXTENSIONS", "knowledge_units")
    assert cli.main(["knowledge", "create", "--file", str(body)]) == 0
    assert "candidate" in capsys.readouterr().out
    assert cli.main(["knowledge", "list", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
