import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "fixed-set-v1"
sys.path.insert(0, str(BENCH))

import freeze_manifest as fm  # noqa: E402

SHA = "a" * 64
SHA2 = "b" * 64


def _row(package_id, slot, url, sha=SHA, state="accepted", review="complete"):
    return {
        "package_id": package_id,
        "stratum": "wechat",
        "source_slot": str(slot),
        "source_class": "article",
        "canonical_url": url,
        "accepted_state": state,
        "revalidated_at": "2026-10-24",
        "source_revision_sha256": sha,
        "coverage_note": "ready",
        "reference_review": review,
    }


def _package(package_id, sources, stratum="wechat"):
    return {
        "schema_version": fm.SCHEMA_VERSION,
        "status": "frozen_reference_reviewed",
        "package_id": package_id,
        "stratum": stratum,
        "source_ids": [source_id for source_id, _, _ in sources],
        "source_revisions": {
            source_id: {"url": url, "sha256": sha}
            for source_id, url, sha in sources
        },
        "deliverable": "A source-grounded brief.",
        "rubric_version": "envoy-fixed-set-v1-rubric-1",
        "turns": [
            {"turn_id": turn_id, "text": f"{turn_id} text"}
            for turn_id in fm.REQUIRED_TURN_IDS
        ],
        "reference_facts": [
            {
                "fact_id": f"{package_id}-fact",
                "claim": "A claim.",
                "source_id": sources[0][0],
                "locator": "section 1",
            }
        ],
    }


def _clean_fixture():
    rows = [
        _row("pkg-a", 1, "https://a.example/one", SHA),
        _row("pkg-a", 2, "https://a.example/two", SHA2),
        _row("pkg-b", 1, "https://b.example/one", SHA),
        _row("pkg-b", 2, "https://b.example/two", SHA2),
    ]
    packages = [
        _package(
            "pkg-a",
            [("a1", "https://a.example/one", SHA), ("a2", "https://a.example/two", SHA2)],
        ),
        _package(
            "pkg-b",
            [("b1", "https://b.example/one", SHA), ("b2", "https://b.example/two", SHA2)],
        ),
    ]
    return rows, packages


def test_clean_rehearsal_set_is_freezable():
    rows, packages = _clean_fixture()

    assert fm.validate(rows, packages, rehearsal=True) == []


def test_unaccepted_source_blocks_the_freeze():
    rows, packages = _clean_fixture()
    rows[0]["accepted_state"] = "candidate"

    problems = fm.validate(rows, packages, rehearsal=True)

    assert any("not accepted" in problem for problem in problems)


def test_pending_reference_review_blocks_the_freeze():
    rows, packages = _clean_fixture()
    rows[1]["reference_review"] = "pending"

    problems = fm.validate(rows, packages, rehearsal=True)

    assert any("reference review" in problem for problem in problems)


def test_url_reused_across_packages_is_reported():
    rows, packages = _clean_fixture()
    rows[2]["canonical_url"] = "https://a.example/one/"

    problems = fm.validate(rows, packages, rehearsal=True)

    assert any("reused across packages" in problem for problem in problems)


def test_draft_without_recorded_revision_is_reported():
    rows, packages = _clean_fixture()
    packages[0]["source_revisions"].pop("a2")

    problems = fm.validate(rows, packages, rehearsal=True)

    assert any("no source revision recorded for a2" in problem for problem in problems)


def test_manifest_hash_is_deterministic_and_reacts_to_task_text():
    rows, packages = _clean_fixture()
    first = fm.build_manifest(rows, packages, rehearsal=True)
    second = fm.build_manifest(rows, packages, rehearsal=True)

    assert fm.manifest_sha256(first) == fm.manifest_sha256(second)

    packages[0]["turns"][0]["text"] = "different wording"
    changed = fm.build_manifest(rows, packages, rehearsal=True)
    assert fm.manifest_sha256(changed) != fm.manifest_sha256(first)


def test_cli_refuses_to_write_a_blocked_set(tmp_path):
    rows, packages = _clean_fixture()
    rows[0]["accepted_state"] = "candidate"
    sources = tmp_path / "source-intake.csv"
    sources.write_text(
        "\n".join([",".join(rows[0])] + [",".join(row.values()) for row in rows]),
        encoding="utf-8",
    )
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for package in packages:
        (drafts / f"{package['package_id']}.json").write_text(
            json.dumps(package), encoding="utf-8"
        )

    code = fm.main([
        "--sources", str(sources),
        "--packages", str(drafts),
        "--out", str(tmp_path / "manifest.json"),
        "--write",
    ])

    assert code == 1
    assert not (tmp_path / "manifest.json").exists()


def test_write_manifest_records_hash_and_timestamp(tmp_path):
    rows, packages = _clean_fixture()
    payload = fm.build_manifest(rows, packages, rehearsal=True)
    out = tmp_path / "manifest.json"

    digest = fm.write_manifest(payload, out)

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["manifest_sha256"] == digest
    assert document["frozen_at"]
    assert document["rehearsal"] is True
    assert fm.manifest_sha256({k: v for k, v in document.items() if k not in {"frozen_at", "manifest_sha256"}}) == digest


def test_repo_set_is_not_freezable_yet(capsys):
    """The checked-in set is deliberately mid-review; this pins the gate."""
    code = fm.main(["--check", "--rehearsal"])

    output = capsys.readouterr().out
    assert code == 1
    assert "not accepted" in output
