"""The evaluator: a claim is a fact about a record, and a missing input is a blocker."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from envoy_cli import cli, evidence_pack, knowledge_eval, knowledge_units, skill_compiler, storage

MATERIAL = "mat-" + "2" * 24
REVISION = "rev-1"
TASK_COUNT = knowledge_eval.MIN_PAIRED_TASKS


@pytest.fixture(autouse=True)
def material_root(monkeypatch, tmp_path):
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(root))
    storage.reset_root_cache()
    return root


def _seed_material(root: Path) -> dict:
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


def _citation(block: dict) -> dict:
    return {
        "material_id": MATERIAL,
        "evidence_id": block["evidence_id"],
        "locator": block["locator"],
        "supported": True,
    }


def _record(
    unit: dict,
    *,
    artifact: Path | None = None,
    tasks: int = TASK_COUNT,
    cache: str = "verified_disabled",
    reviewers: tuple[str, ...] = ("r1", "r2"),
    verdicts: tuple[str, ...] = ("supported", "supported"),
    correct: tuple[bool, ...] = (True, True),
    bundle_better: bool = True,
    should_apply: bool = True,
    usage: bool = True,
    citation_supported: bool = True,
    mutated: callable | None = None,
) -> dict:
    """A record that is complete and claimable unless a keyword makes it not so."""

    block_like = _seed_reader_block()
    bundle_correct = correct
    plain_correct = tuple(False for _ in correct) if bundle_better else correct
    rows = []
    for index in range(tasks):
        task_id = f"t{index:02d}"
        arms = {}
        for name, flags in (
            ("plain", plain_correct),
            ("material", plain_correct),
            ("bundle", bundle_correct),
        ):
            body = {
                "correct": [
                    {"reviewer": reviewer, "correct": flag}
                    for reviewer, flag in zip(("r1", "r2"), flags)
                ],
                "claimed_facts": ["f1"],
                "supported_facts": ["f1"],
                "citations": [
                    {**_citation(block_like), "reviewer": "r1", "supported": citation_supported}
                ],
                "calls": 2,
                "elapsed_ms": 1000 + index,
                "first_evidence_ms": 400,
            }
            if usage:
                body["usage"] = {
                    "provider": "p",
                    "model": "m",
                    "input_tokens": 1000,
                    "output_tokens": 100,
                }
            if name == "bundle":
                body["applied_unit"] = unit["unit_id"] if should_apply else None
                body["should_apply"] = [
                    {"reviewer": reviewer, "applies": should_apply}
                    for reviewer in ("r1", "r2")
                ]
            arms[name] = body
        rows.append({"task_id": task_id, "reference_facts": [{"fact_id": "f1"}], "arms": arms})

    record = {
        "schema_version": knowledge_eval.SCHEMA,
        "run": {
            "envoy_commit": "08a40bd",
            "at": "2026-11-24T00:00:00Z",
            "host_agent": "codex",
            "model": "one-model-identity",
            "cache_isolation_status": cache,
            "budget": {"max_calls": 12, "max_seconds": 300},
        },
        "arms": [
            {"name": "plain", "tools": ["host_fetch"]},
            {"name": "material", "tools": ["envoy_read"]},
            {
                "name": "bundle",
                "tools": ["envoy_read"],
                "pack": {"unit_ids": [unit["unit_id"]], "target": "codex"},
                "members": [unit["unit_id"]],
            },
        ],
        "reviewers": [{"name": name, "independent": True} for name in reviewers],
        "units": [
            {
                "unit_id": unit["unit_id"],
                "artifact": {"target": "codex", "path": str(artifact or _artifact_for(unit))},
                "reviewers": [
                    {"reviewer": reviewer, "verdict": verdict}
                    for reviewer, verdict in zip(reviewers, verdicts)
                ],
            }
        ],
        "tasks": rows,
    }
    if mutated is not None:
        mutated(record)
    return record


_BLOCK: dict = {}


def _seed_reader_block() -> dict:
    return _BLOCK


def _artifact_for(unit: dict) -> Path:
    return _ARTIFACTS[unit["unit_id"]]


_ARTIFACTS: dict[str, Path] = {}


def _install_artifacts(root: Path, unit: dict, out: Path) -> Path:
    compiled = skill_compiler.compile_unit(unit["unit_id"], target="codex", out=out)
    _ARTIFACTS[unit["unit_id"]] = Path(compiled["output_dir"]) / "SKILL.md"
    return _ARTIFACTS[unit["unit_id"]]


def _ready(root: Path, tmp_path: Path) -> tuple[dict, dict]:
    """A promoted unit, its compiled skill on disk, and the block it cites."""

    unit = _promoted(root)
    block = evidence_pack.build_source_document(
        evidence_pack.load_material(MATERIAL, root=root), MATERIAL
    )["blocks"][0]
    _BLOCK.update(block)
    _install_artifacts(root, unit, tmp_path / "skills")
    return unit, block


def test_a_complete_run_is_claimable(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    gate = report["gate"]
    assert gate["gate_status"] == "passed", gate["claim_blockers"]
    assert gate["protocol_valid"] and gate["data_complete"] and gate["quality_reviewed"]
    assert report["claims"]["bundle_vs_material"]["supported"] is True
    assert report["claims"]["bundle_vs_material"]["delta"] == 1.0
    assert report["branches"]["evidence"]["clean"] is True
    assert report["branches"]["skill"]["promoted"] == 1
    assert report["branches"]["skill"]["artifacts_current"] == 1


def test_the_same_record_scores_to_the_same_interval(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit)

    first = knowledge_eval.evaluate_run(record, root=material_root)
    second = knowledge_eval.evaluate_run(json.loads(json.dumps(record)), root=material_root)

    assert first["claims"] == second["claims"]


def test_a_second_independent_reviewer_is_required_before_a_unit_counts(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, reviewers=("r1",))
    record["reviewers"] = [{"name": "r1", "independent": True}, {"name": "r2", "independent": True}]
    record["units"][0]["reviewers"] = [{"reviewer": "r1", "verdict": "supported"}]

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["branches"]["distillation"]["labeled"] == 0
    assert report["branches"]["distillation"]["precision"] is None, "no labels is a hole, not a zero"
    assert report["gate"]["gate_status"] == "incomplete"
    assert f"single_reviewer:{unit['unit_id']}" in report["gate"]["claim_blockers"]


def test_a_split_verdict_without_an_adjudication_blocks_the_claim(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, verdicts=("supported", "overreach"))

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert reason_blocked(report) == f"unadjudicated_disagreement:{unit['unit_id']}"
    assert report["branches"]["distillation"]["overreach_rate"] is None


def reason_blocked(report: dict) -> str:
    unit_blockers = [
        code
        for code in report["gate"]["claim_blockers"]
        if code.startswith(("unadjudicated", "single_reviewer"))
    ]
    assert len(unit_blockers) == 1, report["gate"]["claim_blockers"]
    return unit_blockers[0]


def test_an_adjudication_turns_a_split_into_one_verdict(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(
        unit,
        verdicts=("supported", "overreach"),
    )
    record["units"][0]["adjudication"] = {"by": "r3", "verdict": "overreach"}

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["branches"]["distillation"]["labeled"] == 1
    assert report["branches"]["distillation"]["overreach_rate"] == 1.0
    assert report["branches"]["distillation"]["precision"] == 0.0
    assert report["gate"]["gate_status"] == "passed"


def test_a_citation_that_names_no_evidence_blocks_every_claim(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    def break_it(row):
        row["tasks"][0]["arms"]["bundle"]["citations"][0]["evidence_id"] = "ev-" + "0" * 24

    record = _record(unit, mutated=break_it)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["branches"]["evidence"]["clean"] is False
    assert report["gate"]["gate_status"] == "incomplete"
    assert "evidence_unresolved" in report["gate"]["claim_blockers"]


def test_a_citation_locator_that_disagrees_with_the_material_is_a_problem(material_root, tmp_path):
    unit, block = _ready(material_root, tmp_path)
    record = _record(unit)
    record["tasks"][0]["arms"]["plain"]["citations"][0]["locator"] = {"type": "web"}

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["branches"]["evidence"]["citation_resolution_rate"] < 1.0
    assert any("locator does not match" in problem for problem in report["branches"]["evidence"]["problems"])


def test_an_artifact_that_is_not_what_the_unit_compiles_to_is_stale(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    artifact = _artifact_for(unit)
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n- a line nobody reviewed\n", encoding="utf-8")
    record = _record(unit)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert f"artifact_stale:{unit['unit_id']}" in report["gate"]["claim_blockers"]


def test_a_pack_that_no_longer_resolves_is_named_with_its_refusal(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit)
    # Re-opening to a candidate is not possible from promoted; archive instead,
    # which is exactly the state a shipped pack must never be in.
    knowledge_units.archive_unit(unit["unit_id"], reason="superseded")

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["gate"]["gate_status"] == "incomplete"
    assert any(code.startswith("pack_refused:") for code in report["gate"]["claim_blockers"])


def test_a_confounded_cache_never_yields_a_claim(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, cache="cache_confounded")

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["claims"]["bundle_vs_material"]["supported"] is True
    assert report["gate"]["claim_eligible"] is False
    assert "cache_not_isolated" in report["gate"]["claim_blockers"]


def test_missing_host_usage_is_a_hole_never_an_estimate(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, usage=False)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    for name in knowledge_eval.ARMS:
        arm = report["branches"]["runtime"][name]
        assert arm["host_tokens_total"] is None
        assert arm["usage_complete"] is False
    assert "usage_missing:plain" in report["gate"]["claim_blockers"]
    assert report["gate"]["gate_status"] == "incomplete"


def test_a_run_that_finds_no_improvement_is_complete_not_passed(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, bundle_better=False)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["claims"]["bundle_vs_material"]["delta"] == 0.0
    assert report["gate"]["gate_status"] == "complete"
    assert "no_success_signal" in report["gate"]["claim_blockers"]


def test_a_task_set_too_small_to_pair_is_named_as_such(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, tasks=knowledge_eval.MIN_PAIRED_TASKS - 1)

    report = knowledge_eval.evaluate_run(record, root=material_root)

    assert report["gate"]["protocol_valid"] is False
    assert any(code.startswith("too_few_tasks:") for code in report["gate"]["claim_blockers"])


def test_routing_is_measured_against_what_the_reviewer_said_should_apply(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    applied = _record(unit)
    unused = _record(unit, should_apply=False)

    applied_report = knowledge_eval.evaluate_run(applied, root=material_root)
    unused_report = knowledge_eval.evaluate_run(unused, root=material_root)

    assert applied_report["branches"]["skill"]["routing"]["recall"] == 1.0
    assert unused_report["branches"]["skill"]["routing"]["should_apply_tasks"] == 0
    assert unused_report["branches"]["skill"]["routing"]["recall"] is None


def test_an_unknown_field_is_rejected_instead_of_scoring_zero(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit)
    record["tasks"][0]["arms"]["bundle"]["citation"] = []

    with pytest.raises(knowledge_eval.EvalError) as caught:
        knowledge_eval.evaluate_run(record, root=material_root)
    assert caught.value.code == "invalid_record"
    assert "unknown fields" in str(caught.value)


def test_one_reviewer_cannot_make_a_run_claimable(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit)
    record["reviewers"] = [{"name": "r1", "independent": True}]

    with pytest.raises(knowledge_eval.EvalError) as caught:
        knowledge_eval.evaluate_run(record, root=material_root)
    assert "two independent reviewers" in str(caught.value)


def test_the_report_says_why_no_claim_was_made(material_root, tmp_path):
    unit, _block = _ready(material_root, tmp_path)
    record = _record(unit, cache="unknown")

    text = knowledge_eval.render_report(knowledge_eval.evaluate_run(record, root=material_root))

    assert "**Gate: incomplete**" in text
    assert "cache_not_isolated" in text
    assert "not measured" in text or "not supported" in text
    assert "host-reported usage" in text


def test_the_cli_scores_a_record_it_is_given(material_root, tmp_path, capsys):
    unit, _block = _ready(material_root, tmp_path)
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_record(unit)), encoding="utf-8")

    code = cli.main(["knowledge", "eval", "--run", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["schema_version"] == knowledge_eval.SCHEMA
    assert payload["gate"]["gate_status"] == "passed"


def test_the_cli_refuses_a_record_that_is_not_utf8_json(material_root, tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text("{not json", encoding="utf-8")

    code = cli.main(["knowledge", "eval", "--run", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["error"] == "invalid_record"
