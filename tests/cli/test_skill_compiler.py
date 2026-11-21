"""The compiler: same unit in, same bytes out, and only from evidence that resolves."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from envoy_cli import cli, evidence_pack, knowledge_units, skill_compiler, storage
from envoy_cli import extensions as extension_flags

MATERIAL = "mat-" + "1" * 24
REVISION = "rev-1"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "skills"


@pytest.fixture(autouse=True)
def material_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    return root


def _seed_material(root: Path, *, revision: str = REVISION) -> dict:
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
            }
        ],
    }
    (root / f"{MATERIAL}.json").write_text(json.dumps(record), encoding="utf-8")
    (root / f"{MATERIAL}.expires").write_text(str(time.time() + 600), encoding="utf-8")
    stored = evidence_pack.load_material(MATERIAL, root=root)
    return evidence_pack.build_source_document(stored, MATERIAL)["blocks"][0]


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


def _promoted(root: Path) -> dict:
    block = _seed_material(root)
    unit = knowledge_units.create_unit(_payload(block))
    knowledge_units.verify_unit(unit["unit_id"])
    knowledge_units.promote_unit(unit["unit_id"])
    return knowledge_units.load_unit(unit["unit_id"])


@pytest.mark.parametrize("target", skill_compiler.TARGETS)
def test_a_compiled_skill_matches_its_golden_file(target, material_root, tmp_path):
    unit = _promoted(material_root)
    out = tmp_path / "out"
    skill_compiler.compile_unit(unit["unit_id"], target=target, out=out)

    for name in ("SKILL.md", "manifest.json"):
        expected = (FIXTURES / target / name).read_text(encoding="utf-8")
        actual = (out / name).read_text(encoding="utf-8")
        assert actual == expected, f"{target}/{name} drifted from the golden file"


@pytest.mark.parametrize("target", skill_compiler.TARGETS)
def test_compiling_twice_writes_the_same_bytes_and_the_second_run_writes_nothing(
    target, material_root, tmp_path
):
    unit = _promoted(material_root)
    out = tmp_path / "out"
    first = skill_compiler.compile_unit(unit["unit_id"], target=target, out=out)
    second = skill_compiler.compile_unit(unit["unit_id"], target=target, out=out)

    assert first["content_digest"] == second["content_digest"]
    assert second["written"] == [], "a second compile rewrote files that had not changed"


def test_every_target_only_needs_name_and_description_in_its_frontmatter(material_root, tmp_path):
    unit = _promoted(material_root)
    out = tmp_path / "out"
    for target in skill_compiler.TARGETS:
        skill_compiler.compile_unit(unit["unit_id"], target=target, out=out / target)
        text = (out / target / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        frontmatter = text.split("---\n")[1]
        assert 'name: "' in frontmatter
        assert "description: " in frontmatter
        # Measured on the three runtimes: only these two keys are required.
        assert frontmatter.count("\nname:") <= 1


def test_the_install_path_names_the_directory_each_runtime_reads(material_root, tmp_path):
    unit = _promoted(material_root)
    out = tmp_path / "out"
    expected = {"codex": ".codex/skills", "claude": ".claude/skills", "dsh": ".dsh/skills"}
    for target, prefix in expected.items():
        result = skill_compiler.compile_unit(unit["unit_id"], target=target, out=out / target)
        assert result["manifest"]["install_path"].startswith(f"{prefix}/")
        assert result["manifest"]["install_path"].endswith("/SKILL.md")


def test_the_manifest_carries_the_material_revision_beside_the_unit_revision(material_root, tmp_path):
    unit = _promoted(material_root)
    result = skill_compiler.compile_unit(unit["unit_id"], target="codex", out=tmp_path / "out")
    manifest = result["manifest"]

    assert manifest["unit"]["unit_id"] == unit["unit_id"]
    assert manifest["unit"]["version"] == unit["version"]
    assert manifest["unit"]["content_hash"] == knowledge_units.unit_content_hash(unit)
    assert manifest["evidence"][0]["material_id"] == MATERIAL
    assert manifest["evidence"][0]["source_revision"] == REVISION
    assert manifest["evidence"][0]["locator"] == unit["source_refs"][0]["locator"]


def test_compiling_leaves_the_unit_untouched(material_root, tmp_path):
    unit = _promoted(material_root)
    before = (material_root / "knowledge-units" / f"{unit['unit_id']}.json").read_bytes()
    skill_compiler.compile_unit(unit["unit_id"], target="dsh", out=tmp_path / "out")
    after = (material_root / "knowledge-units" / f"{unit['unit_id']}.json").read_bytes()

    assert after == before


def test_a_candidate_does_not_compile(material_root, tmp_path):
    block = _seed_material(material_root)
    unit = knowledge_units.create_unit(_payload(block))

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="codex", out=tmp_path / "out")
    assert excinfo.value.code == "needs_promotion"


def test_an_archived_unit_does_not_compile(material_root, tmp_path):
    unit = _promoted(material_root)
    knowledge_units.archive_unit(unit["unit_id"], reason="superseded")

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="codex", out=tmp_path / "out")
    assert excinfo.value.code == "unit_archived"


def test_evidence_that_moved_after_verification_does_not_compile(material_root, tmp_path):
    unit = _promoted(material_root)
    _seed_material(material_root, revision="rev-2")

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="codex", out=tmp_path / "out")
    assert excinfo.value.code == "evidence_unresolved"
    assert excinfo.value.details["problems"]


def test_an_unknown_target_is_refused(material_root, tmp_path):
    unit = _promoted(material_root)

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="copilot", out=tmp_path / "out")
    assert excinfo.value.code == "invalid_request"


def test_an_output_directory_from_another_unit_is_refused_without_force(material_root, tmp_path):
    unit = _promoted(material_root)
    out = tmp_path / "shared"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({"unit": {"unit_id": "ku-" + "0" * 16}}), encoding="utf-8")

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="codex", out=out)
    assert excinfo.value.code == "output_conflict"

    skill_compiler.compile_unit(unit["unit_id"], target="codex", out=out, force=True)
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["unit"]["unit_id"] == unit["unit_id"]


def test_the_skill_name_is_readable_and_carries_the_frozen_identity(material_root, tmp_path):
    unit = _promoted(material_root)
    name = skill_compiler.skill_name(unit)

    # The directory name carries the first half of the frozen identity; the
    # manifest carries all of it.
    assert unit["unit_id"][3:11] in name
    assert name.replace("-", "").isalnum()
    assert name.lower() == name


def test_compile_says_the_extension_is_off_before_it_touches_a_unit(monkeypatch, capsys):
    monkeypatch.setenv("ENVOY_EXTENSIONS", "")
    monkeypatch.delenv("ENVOY_EXTENSIONS", raising=False)

    code = cli.main(["knowledge", "compile", "ku-" + "0" * 16, "--target", "codex", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["error"] == "extension_disabled"
    assert "skill_distillation" in payload["message"]


def test_compile_writes_the_skill_from_the_cli(monkeypatch, material_root, tmp_path, capsys):
    monkeypatch.setenv("ENVOY_EXTENSIONS", "knowledge_units,skill_distillation")
    unit = _promoted(material_root)
    out = tmp_path / "cli-out"

    code = cli.main(
        [
            "knowledge",
            "compile",
            unit["unit_id"],
            "--target",
            "claude",
            "--out",
            str(out),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["unit_id"] == unit["unit_id"]
    assert (out / "SKILL.md").is_file()


def test_a_unit_whose_verification_moved_does_not_compile(material_root, tmp_path):
    """Refs can still resolve while the fingerprint they were resolved into does not.

    Editing the record from outside the store is the only way to reach this: the
    public verbs either clear the fingerprint or move it with the evidence. It is
    still the state a restored backup or a hand-edited store can land in, and a
    skill cut from it would claim a verification nobody performed.
    """

    unit = _promoted(material_root)
    stored = knowledge_units._unit_path(unit["unit_id"], root=material_root)
    record = json.loads(stored.read_text(encoding="utf-8"))
    record["verified_against"] = "0" * 64
    stored.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(knowledge_units.UnitError) as excinfo:
        skill_compiler.compile_unit(unit["unit_id"], target="codex", out=tmp_path / "out")
    assert excinfo.value.code == "verification_stale"
    assert excinfo.value.details["expected"] == "0" * 64
    assert excinfo.value.details["actual"] != "0" * 64


def test_compiling_needs_its_own_flag_even_with_knowledge_units_on(
    material_root, monkeypatch, capsys
):
    """Reading and writing units is one capability; emitting a skill is another."""

    unit = _promoted(material_root)
    monkeypatch.setenv("ENVOY_EXTENSIONS", "knowledge_units")
    monkeypatch.delenv(extension_flags.ENV_FLAG, raising=False)

    assert cli.main(["knowledge", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["unit_id"] for item in listed] == [unit["unit_id"]]

    exit_code = cli.main(["knowledge", "compile", unit["unit_id"], "--target", "codex", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["error"] == "extension_disabled"
    assert extension_flags.SKILL_DISTILLATION in payload["message"]
