"""Knowledge packs: a pack is derived from the relations, never invented."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from envoy_cli import cli, evidence_pack, knowledge_pack, knowledge_units, skill_compiler, storage
from envoy_cli import extensions as extension_flags

MATERIAL = "mat-" + "1" * 24
REVISION = "rev-1"


@pytest.fixture(autouse=True)
def material_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    return root


def _seed_material(root: Path) -> list[dict]:
    record = {
        "source_url": "https://example.com/a",
        "source_revision": REVISION,
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


def _payload(block: dict, **overrides) -> dict:
    payload = {
        "type": "principle",
        "title": "Cite the block, not the impression",
        "summary": "A citation names a block and a place in the source.",
        "trigger": "citing a paragraph that must survive review",
        "inputs": ["a retained material", "the block id"],
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
        ],
        "tests": [{"kind": "positive", "question": "Does the quote carry a locator?"}],
    }
    payload.update(overrides)
    return payload


def _unit(root: Path, title: str, **overrides) -> dict:
    blocks = _seed_material(root)
    return knowledge_units.create_unit(_payload(blocks[0], title=title, **overrides))


def _promoted(root: Path, title: str, **overrides) -> dict:
    unit = _unit(root, title, **overrides)
    knowledge_units.verify_unit(unit["unit_id"])
    knowledge_units.promote_unit(unit["unit_id"])
    return knowledge_units.load_unit(unit["unit_id"])


def _evidence(blocks: list[dict]) -> list[dict]:
    return [
        {
            "material_id": MATERIAL,
            "evidence_id": blocks[0]["evidence_id"],
            "locator": blocks[0]["locator"],
        }
    ]


def _relate(left: dict, right: dict, relation: str) -> None:
    blocks = _seed_material(storage.resolve_material_root())
    knowledge_units.relate_units(left["unit_id"], relation, right["unit_id"], _evidence(blocks))


def test_a_dependency_is_pulled_into_the_pack_ahead_of_what_needs_it(material_root, tmp_path):
    whole = _promoted(material_root, "Assemble the report")
    part = _promoted(material_root, "Open the block")
    _relate(whole, part, "composed_of")

    built = knowledge_pack.build_pack(
        [whole["unit_id"]], target="codex", out=tmp_path / "pack"
    )

    assert built["roots"] == [whole["unit_id"]]
    assert [row["unit_id"] for row in built["members"]] == [part["unit_id"], whole["unit_id"]]
    assert built["manifest"]["relations"] == [
        {"from_unit_id": whole["unit_id"], "type": "composed_of", "to_unit_id": part["unit_id"]}
    ]
    body = (tmp_path / "pack" / "PACK.md").read_text(encoding="utf-8")
    assert body.index(part["unit_id"]) < body.index(whole["unit_id"])


def test_an_alternative_is_offered_but_never_pulled_into_the_pack(material_root, tmp_path):
    main = _promoted(material_root, "Read the page directly")
    spare = _promoted(material_root, "Read the cached copy")
    _relate(main, spare, "alternative_to")

    built = knowledge_pack.build_pack([main["unit_id"]], target="codex", out=tmp_path / "pack")

    assert [row["unit_id"] for row in built["members"]] == [main["unit_id"]]
    assert built["manifest"]["fallbacks"] == [
        {
            "of_unit_id": main["unit_id"],
            "unit_id": spare["unit_id"],
            "title": "Read the cached copy",
            "usable": True,
            "reason": "",
        }
    ]
    body = (tmp_path / "pack" / "PACK.md").read_text(encoding="utf-8")
    assert "If a Step Is Blocked" in body
    assert spare["unit_id"] in body


def test_a_candidate_dependency_is_refused_by_name(material_root, tmp_path):
    whole = _promoted(material_root, "Assemble the report")
    draft = _unit(material_root, "Open the block")
    _relate(whole, draft, "depends_on")

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_pack.build_pack([whole["unit_id"]], target="codex", out=tmp_path / "pack")
    assert refusal.value.code == "needs_promotion"
    assert draft["unit_id"] in str(refusal.value)


def test_a_dependency_that_does_not_resolve_stops_the_pack(material_root, tmp_path):
    whole = _promoted(material_root, "Assemble the report")
    part = _promoted(material_root, "Open the block")
    _relate(whole, part, "depends_on")
    (material_root / f"{MATERIAL}.json").unlink()

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_pack.build_pack([whole["unit_id"]], target="codex", out=tmp_path / "pack")
    assert refusal.value.code == "evidence_unresolved"
    assert not (tmp_path / "pack" / "PACK.md").exists()


def test_a_cycle_is_cut_and_reported_instead_of_hanging(material_root, tmp_path):
    # A two-unit loop cannot be stored: the pair is unordered, so ``b depends_on a``
    # after ``a depends_on b`` is the same fact written the other way round. A real
    # loop needs three ends.
    first = _promoted(material_root, "First step")
    second = _promoted(material_root, "Second step")
    third = _promoted(material_root, "Third step")
    _relate(first, second, "depends_on")
    _relate(second, third, "depends_on")
    _relate(third, first, "depends_on")

    built = knowledge_pack.build_pack([first["unit_id"]], target="codex", out=tmp_path / "pack")

    assert sorted(row["unit_id"] for row in built["members"]) == sorted(
        [first["unit_id"], second["unit_id"], third["unit_id"]]
    )
    cut = built["manifest"]["cycles"]
    assert cut, "a loop must be reported, not silently dropped"
    assert {row["reason"] for row in cut} == {"cycle"}
    assert {row["to_unit_id"] for row in cut} <= {row["unit_id"] for row in built["members"]}
    assert "closes a loop" in (tmp_path / "pack" / "PACK.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("target", skill_compiler.TARGETS)
def test_building_twice_writes_the_same_bytes_and_the_second_run_writes_nothing(
    target, material_root, tmp_path
):
    whole = _promoted(material_root, "Assemble the report")
    part = _promoted(material_root, "Open the block")
    _relate(whole, part, "composed_of")
    out = tmp_path / "pack"

    first = knowledge_pack.build_pack([whole["unit_id"]], target=target, out=out)
    second = knowledge_pack.build_pack([whole["unit_id"]], target=target, out=out)

    assert first["content_digest"] == second["content_digest"]
    assert second["written"] == [], "a second build rewrote files that had not changed"
    assert sorted(path.name for path in out.iterdir() if path.is_file()) == [
        "PACK.md",
        "manifest.json",
    ]


def test_every_member_also_ships_the_skill_it_compiles_to(material_root, tmp_path):
    whole = _promoted(material_root, "Assemble the report")
    part = _promoted(material_root, "Open the block")
    _relate(whole, part, "composed_of")

    built = knowledge_pack.build_pack([whole["unit_id"]], target="claude", out=tmp_path / "pack")

    for row in built["members"]:
        install = Path(row["install_path"])
        assert install.is_file()
        assert install.parent.name == row["skill_name"]
        assert row["skill_name"] in (tmp_path / "pack" / "PACK.md").read_text(encoding="utf-8")


def test_a_pack_over_other_units_is_not_replaced_without_force(material_root, tmp_path):
    one = _promoted(material_root, "First claim")
    other = _promoted(material_root, "Second claim")
    out = tmp_path / "pack"
    knowledge_pack.build_pack([one["unit_id"]], target="codex", out=out)

    with pytest.raises(knowledge_units.UnitError) as conflict:
        knowledge_pack.build_pack([other["unit_id"]], target="codex", out=out)
    assert conflict.value.code == "output_conflict"
    assert "pass --force" in str(conflict.value)

    replaced = knowledge_pack.build_pack([other["unit_id"]], target="codex", out=out, force=True)
    assert replaced["roots"] == [other["unit_id"]]


def test_an_unknown_target_is_refused_before_anything_is_read(material_root, tmp_path):
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_pack.build_pack(["ku-" + "0" * 16], target="copilot", out=tmp_path)
    assert refusal.value.code == "invalid_request"
    assert "codex, claude, dsh" in str(refusal.value)


def test_a_pack_needs_at_least_one_starting_unit():
    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_pack.resolve_pack([])
    assert refusal.value.code == "invalid_request"


def test_only_name_and_description_are_required_and_dsh_keeps_the_identity(
    material_root, tmp_path
):
    main = _promoted(material_root, "Read the page directly")
    codex = knowledge_pack.build_pack([main["unit_id"]], target="codex", out=tmp_path / "codex")
    dsh = knowledge_pack.build_pack([main["unit_id"]], target="dsh", out=tmp_path / "dsh")

    codex_head = (tmp_path / "codex" / "PACK.md").read_text(encoding="utf-8").split("---")[1]
    dsh_head = (tmp_path / "dsh" / "PACK.md").read_text(encoding="utf-8").split("---")[1]
    assert "name:" in codex_head and "description:" in codex_head
    assert "metadata" not in codex_head
    assert "pack_digest" in dsh_head
    assert codex["pack_name"] != "" and dsh["pack_name"] == codex["pack_name"]


def test_the_pack_limit_stops_a_library_from_becoming_a_pack(material_root, tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_pack, "PACK_LIMIT", 1)
    whole = _promoted(material_root, "Assemble the report")
    part = _promoted(material_root, "Open the block")
    _relate(whole, part, "composed_of")

    with pytest.raises(knowledge_units.UnitError) as refusal:
        knowledge_pack.resolve_pack([whole["unit_id"]])
    assert refusal.value.code == "invalid_request"
    assert "limit is 1" in str(refusal.value)


def test_relating_the_two_new_types_is_accepted_and_reads_left_to_right(material_root):
    blocks = _seed_material(material_root)
    one = _unit(material_root, "First claim")
    other = _unit(material_root, "Second claim", summary="A different claim.")

    for relation in knowledge_units.REQUIRING_RELATIONS + knowledge_units.FALLBACK_RELATIONS:
        related = knowledge_units.relate_units(
            one["unit_id"], relation, other["unit_id"], _evidence(blocks)
        )
        entry = next(row for row in related["relations"] if row["type"] == relation and not row["inverse_of"])
        assert entry["type"] == relation
        assert entry["from_unit_id"] == one["unit_id"]
        assert entry["to_unit_id"] == other["unit_id"]
        assert entry["content_hash"] == knowledge_units._relation_fingerprint(entry)

    mirrored = knowledge_units.list_relations(other["unit_id"])
    assert {row["type"] for row in mirrored} == set(
        knowledge_units.REQUIRING_RELATIONS + knowledge_units.FALLBACK_RELATIONS
    )
    assert all(row["inverse_of"] for row in mirrored)


def test_the_new_relation_types_are_a_partition_of_the_ones_that_move_a_pack():
    closure = set(knowledge_units.REQUIRING_RELATIONS)
    fallback = set(knowledge_units.FALLBACK_RELATIONS)

    assert closure & fallback == set()
    assert closure | fallback <= set(knowledge_units.RELATION_TYPES)
    assert "alternative_to" not in closure, "an alternative must never pull a unit into a pack"
    assert {"depends_on", "composed_of"} == closure


def test_the_cli_builds_a_pack_and_reports_it(monkeypatch, capsys, material_root, tmp_path):
    monkeypatch.setenv(
        extension_flags.ENV_FLAG,
        ",".join([extension_flags.KNOWLEDGE_UNITS, extension_flags.SKILL_DISTILLATION]),
    )
    main = _promoted(material_root, "Read the page directly")

    code = cli.main(
        [
            "knowledge",
            "pack",
            main["unit_id"],
            "--target",
            "codex",
            "--out",
            str(tmp_path / "pack"),
            "--json",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["roots"] == [main["unit_id"]]
    assert payload["target"] == "codex"
    assert (tmp_path / "pack" / "PACK.md").is_file()


def test_the_cli_refuses_to_build_a_pack_with_the_skill_layer_off(
    monkeypatch, capsys, material_root, tmp_path
):
    monkeypatch.setenv(extension_flags.ENV_FLAG, extension_flags.KNOWLEDGE_UNITS)
    main = _promoted(material_root, "Read the page directly")

    code = cli.main(
        ["knowledge", "pack", main["unit_id"], "--target", "codex", "--out", str(tmp_path / "pack")]
    )

    assert code == 1
    assert "skill_distillation" in capsys.readouterr().out
    assert not (tmp_path / "pack" / "PACK.md").exists()


def test_the_mcp_tool_exposes_pack_and_keeps_the_gate_order(
    monkeypatch, material_root, tmp_path
):
    from envoy_cli import mcp

    main = _promoted(material_root, "Read the page directly")
    monkeypatch.setenv(extension_flags.ENV_FLAG, extension_flags.KNOWLEDGE_UNITS)

    with pytest.raises(mcp.KnowledgeToolError) as off:
        mcp._knowledge_payload({"action": "pack", "unit_ids": [main["unit_id"]], "target": "codex"})
    assert off.value.code == "extension_disabled"
    assert extension_flags.SKILL_DISTILLATION in str(off.value)

    monkeypatch.setenv(
        extension_flags.ENV_FLAG,
        ",".join([extension_flags.KNOWLEDGE_UNITS, extension_flags.SKILL_DISTILLATION]),
    )
    with pytest.raises(mcp.KnowledgeToolError) as no_target:
        mcp._knowledge_payload({"action": "pack", "unit_ids": [main["unit_id"]]})
    assert no_target.value.code == "invalid_request"

    payload = mcp._knowledge_payload(
        {"action": "pack", "unit_ids": [main["unit_id"]], "target": "codex"}
    )
    assert payload["action"] == "pack"
    assert payload["pack"]["roots"] == [main["unit_id"]]
    assert payload["pack"]["target"] == "codex"


def test_the_mcp_tool_offers_pack_as_an_action():
    from envoy_cli import mcp

    assert "pack" in mcp.KNOWLEDGE_SCHEMA["properties"]["action"]["enum"]
    assert "unit_ids" in mcp.KNOWLEDGE_SCHEMA["properties"]
