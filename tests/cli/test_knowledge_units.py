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


def test_a_snapshot_shows_the_record_as_it_stood_at_that_version():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    verified = knowledge_units.verify_unit(unit["unit_id"])

    # Verification is an annotation on version 1: the stored version 1 has to say
    # so, or the snapshot and the record disagree about the same version.
    snapshot = json.loads(knowledge_units._snapshot_path(unit["unit_id"], 1).read_text(encoding="utf-8"))
    assert snapshot["version"] == 1
    assert snapshot["status"] == verified["status"] == "verified"
    assert snapshot["verified_against"] == verified["verified_against"]
    assert snapshot["history"] == verified["history"]
    assert snapshot["content_hash"] == verified["content_hash"]


def test_a_relation_appears_in_both_snapshots_at_the_version_it_annotates():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(
        _payload([blocks[1]], title="A related claim", summary="A second claim.")
    )
    evidence = [
        {"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}
    ]
    related = knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], evidence)

    for record in (related, knowledge_units.load_unit(other["unit_id"])):
        stored = json.loads(
            knowledge_units._snapshot_path(record["unit_id"], record["version"]).read_text(encoding="utf-8")
        )
        assert stored["relations"] == record["relations"]
        assert stored["content_hash"] == record["content_hash"]
    assert knowledge_units.load_unit(unit["unit_id"])["version"] == 1


def test_a_rollback_still_restores_only_the_body():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    knowledge_units.verify_unit(unit["unit_id"])
    knowledge_units.archive_unit(unit["unit_id"])
    knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})

    rolled = knowledge_units.rollback_unit(unit["unit_id"], 1)
    assert rolled["summary"] == unit["summary"]
    assert rolled["status"] == "candidate"
    assert rolled["verified_at"] is None
    assert rolled["relations"] == []


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

    evidence = [
        {"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}
    ]
    related = knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], evidence)

    entry = related["relations"][0]
    assert entry["from_unit_id"] == unit["unit_id"]
    assert entry["to_unit_id"] == other["unit_id"]
    assert entry["type"] == "supports"
    assert entry["from_version"] == unit["version"]
    assert entry["inverse_of"] is None
    assert knowledge_units.RELATION_ID.fullmatch(entry["relation_id"])
    assert entry["content_hash"] == knowledge_units._relation_fingerprint(entry)

    with pytest.raises(knowledge_units.UnitError) as duplicate:
        knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], entry["evidence"])
    assert duplicate.value.code == "relation_exists"


def test_a_relation_is_readable_from_both_ends_with_its_own_fingerprint():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(
        _payload([blocks[1]], title="A related claim", summary="A second claim.")
    )

    evidence = [
        {"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}
    ]
    related = knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], evidence)
    asserted = related["relations"][0]

    mirrored = knowledge_units.list_relations(other["unit_id"])[0]
    assert mirrored["to_unit_id"] == unit["unit_id"]
    assert mirrored["from_unit_id"] == other["unit_id"]
    assert mirrored["inverse_of"] == asserted["relation_id"]
    assert mirrored["relation_id"] != asserted["relation_id"]
    assert mirrored["content_hash"] != asserted["content_hash"]
    assert mirrored["content_hash"] == knowledge_units._relation_fingerprint(mirrored)

    # A relation is an annotation: it does not edit the body, so the mirror keeps
    # the target's body version and body fingerprint.
    target = knowledge_units.load_unit(other["unit_id"])
    assert target["version"] == 1
    assert target["content_hash"] == other["content_hash"]
    assert mirrored["from_version"] == target["version"]


def test_a_unit_still_carries_its_own_identity_after_a_relation_is_recorded():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(
        _payload([blocks[1]], title="A related claim", summary="A second claim.")
    )

    evidence = [
        {"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}
    ]
    related = knowledge_units.relate_units(unit["unit_id"], "supports", other["unit_id"], evidence)

    assert related["unit_id"] == unit["unit_id"]
    assert related["content_hash"] == unit["content_hash"]


def test_a_relation_written_before_v2_is_still_readable_and_never_refused():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(
        _payload([blocks[1]], title="A related claim", summary="A second claim.")
    )

    path = knowledge_units._unit_path(unit["unit_id"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["relations"] = [
        {
            "type": "supports",
            "to": other["unit_id"],
            "evidence": [
                {
                    "material_id": MATERIAL,
                    "evidence_id": blocks[0]["evidence_id"],
                    "locator": blocks[0]["locator"],
                }
            ],
            "at": "2026-01-01T00:00:00Z",
        }
    ]
    path.write_text(json.dumps(stored), encoding="utf-8")

    loaded = knowledge_units.load_unit(unit["unit_id"])
    view = knowledge_units.relation_view(loaded)
    assert len(view) == 1
    assert view[0]["legacy"] is True
    assert view[0]["to_unit_id"] == other["unit_id"]
    assert view[0]["from_unit_id"] == unit["unit_id"]
    assert view[0]["content_hash"] is None

    verified = knowledge_units.verify_unit(unit["unit_id"])
    assert verified["status"] == "verified"
    assert "relation supports ->" in knowledge_units.render_unit(loaded)


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


def test_a_write_aimed_at_a_version_that_has_moved_is_refused():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    stale = unit["version"]

    knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.update_unit(
            unit["unit_id"], {"summary": "Written from the stale copy."}, expected_version=stale
        )
    assert refusal.value.code == "version_conflict"
    assert refusal.value.details == {"expected": 1, "actual": 2}
    assert knowledge_units.load_unit(unit["unit_id"])["summary"] == "A sharper summary."


def test_every_writing_command_honours_the_expected_version():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    other = knowledge_units.create_unit(
        _payload([blocks[1]], title="A related claim", summary="A second claim.")
    )
    evidence = [
        {"material_id": MATERIAL, "evidence_id": blocks[0]["evidence_id"], "locator": blocks[0]["locator"]}
    ]
    knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})

    writers = {
        "update": lambda: knowledge_units.update_unit(
            unit["unit_id"], {"summary": "again"}, expected_version=1
        ),
        "archive": lambda: knowledge_units.archive_unit(unit["unit_id"], expected_version=1),
        "rollback": lambda: knowledge_units.rollback_unit(unit["unit_id"], 1, expected_version=1),
        "relate": lambda: knowledge_units.relate_units(
            unit["unit_id"], "supports", other["unit_id"], evidence, expected_version=1
        ),
        "verify": lambda: knowledge_units.verify_unit(unit["unit_id"], expected_version=1),
    }
    for name, writer in writers.items():
        with pytest.raises(knowledge_units.UnitError) as refusal:
            writer()
        assert refusal.value.code == "version_conflict", name

    promoted = knowledge_units.verify_unit(unit["unit_id"])
    with pytest.raises(knowledge_units.UnitError) as stale_promote:
        knowledge_units.promote_unit(unit["unit_id"], expected_version=promoted["version"] - 1)
    assert stale_promote.value.code == "version_conflict"


def test_two_writers_that_read_the_same_version_cannot_both_win(tmp_path):
    """A deterministic compare-and-set: the loser of every pair is refused."""

    from concurrent.futures import ThreadPoolExecutor

    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    version = unit["version"]

    def write_summary(index: int) -> str:
        try:
            knowledge_units.update_unit(
                unit["unit_id"],
                {"summary": f"Writer {index} saw version {version}."},
                expected_version=version,
            )
        except knowledge_units.UnitError as exc:
            return exc.code
        return "written"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(write_summary, range(4)))

    assert outcomes.count("written") == 1
    assert outcomes.count("version_conflict") == 3
    final = knowledge_units.load_unit(unit["unit_id"])
    assert final["version"] == version + 1
    assert final["summary"].startswith("Writer ")


def test_verify_reports_refs_that_carry_no_revision_without_refusing_them():
    blocks = _seed_material(storage.resolve_material_root())
    revisionless = dict(_payload(blocks)["source_refs"][0])
    revisionless.pop("source_revision")
    unit = knowledge_units.create_unit(_payload(blocks, source_refs=[revisionless]))
    assert "source_revision" not in knowledge_units.load_unit(unit["unit_id"])["source_refs"][0]

    verified = knowledge_units.verify_unit(unit["unit_id"])
    assert verified["status"] == "verified"
    assert verified["unpinned_refs"] == 1
    assert "no source_revision recorded" in verified["warnings"][0]


def test_pinned_refs_report_no_unpinned_warning():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    verified = knowledge_units.verify_unit(unit["unit_id"])
    assert verified["unpinned_refs"] == 0
    assert verified["warnings"] == []


def test_promote_refuses_a_unit_whose_evidence_moved_after_it_was_verified(tmp_path):
    root = storage.resolve_material_root()
    blocks = _seed_material(root)
    unit = knowledge_units.create_unit(_payload(blocks))
    verified = knowledge_units.verify_unit(unit["unit_id"])
    assert verified["status"] == "verified"

    # The material is re-fetched at a new revision: the refs still resolve, but
    # not against the revision the verification fingerprint names.
    record = json.loads((root / f"{MATERIAL}.json").read_text(encoding="utf-8"))
    record["source_revision"] = "rev-2"
    (root / f"{MATERIAL}.json").write_text(json.dumps(record), encoding="utf-8")
    ref = dict(verified["source_refs"][0])
    ref["source_revision"] = "rev-2"
    path = knowledge_units._unit_path(unit["unit_id"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["source_refs"] = [ref]
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.promote_unit(unit["unit_id"])
    assert refusal.value.code == "verification_stale"
    assert refusal.value.details["expected"] == verified["verified_against"]
    assert refusal.value.details["actual"] != verified["verified_against"]
    assert "verify it again" in str(refusal.value)
    assert knowledge_units.load_unit(unit["unit_id"])["status"] == "verified"


def test_promote_refuses_a_unit_whose_evidence_no_longer_resolves(tmp_path):
    root = storage.resolve_material_root()
    blocks = _seed_material(root)
    unit = knowledge_units.create_unit(_payload(blocks))
    knowledge_units.verify_unit(unit["unit_id"])

    (root / f"{MATERIAL}.json").unlink()
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_units.promote_unit(unit["unit_id"])
    assert refusal.value.code == "evidence_unresolved"


def test_a_unit_without_a_content_hash_loads_and_verifies_without_being_rewritten(tmp_path):
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    path = knowledge_units._unit_path(unit["unit_id"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored.pop("content_hash")
    stored["schema_version"] = "envoy.knowledge-unit/1"
    path.write_text(json.dumps(stored), encoding="utf-8")
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns

    loaded = knowledge_units.load_unit(unit["unit_id"])
    assert "content_hash" not in loaded
    listed = knowledge_units.list_units()
    assert [row["unit_id"] for row in listed] == [unit["unit_id"]]
    print(knowledge_units.render_unit(loaded))
    assert knowledge_units.unit_content_hash(loaded) == unit["content_hash"]

    # Reading is not writing: the file is byte-identical and untouched.
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_the_content_hash_moves_only_when_the_body_moves():
    blocks = _seed_material(storage.resolve_material_root())
    unit = knowledge_units.create_unit(_payload(blocks))
    assert unit["content_hash"] == knowledge_units.content_hash_of(unit)
    assert unit["unit_id"] == knowledge_units._unit_identity(unit)

    annotated = knowledge_units.relate_units(
        unit["unit_id"],
        "supports",
        knowledge_units.create_unit(
            _payload([blocks[1]], title="A related claim", summary="A second claim.")
        )["unit_id"],
        [dict(unit["source_refs"][0])],
    )
    assert annotated["content_hash"] == unit["content_hash"]

    edited = knowledge_units.update_unit(unit["unit_id"], {"summary": "A sharper summary."})
    assert edited["content_hash"] != unit["content_hash"]
    assert edited["version"] == unit["version"] + 1
    assert edited["unit_id"] == unit["unit_id"]


def test_the_cli_refuses_a_write_when_if_version_is_stale(tmp_path, capsys, monkeypatch):
    blocks = _seed_material(storage.resolve_material_root())
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ENVOY_EXTENSIONS", "knowledge_units")
    body = tmp_path / "unit.json"
    body.write_text(json.dumps(_payload(blocks)), encoding="utf-8")

    assert cli.main(["knowledge", "create", "--file", str(body), "--json"]) == 0
    unit_id = json.loads(capsys.readouterr().out)["unit_id"]
    patch = tmp_path / "patch.json"
    patch.write_text(json.dumps({"summary": "A sharper summary."}), encoding="utf-8")

    assert cli.main(["knowledge", "update", unit_id, "--file", str(patch), "--if-version", "1"]) == 0
    capsys.readouterr()

    assert cli.main(["knowledge", "update", unit_id, "--file", str(patch), "--if-version", "1"]) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["error"] == "version_conflict"
    assert failure["expected"] == 1
    assert failure["actual"] == 2

    assert cli.main(["knowledge", "archive", unit_id, "--if-version", "2"]) == 0
    assert "[archived]" in capsys.readouterr().out
