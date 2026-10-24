import hashlib
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "fixed-set-v1"
sys.path.insert(0, str(BENCH))

import freeze_manifest as fm  # noqa: E402

TEXT_A = "温度参数建议使用较低的取值，配合 top_p 一起调整，避免输出发散。"
TEXT_B = "在 softmax 之前，温度会缩放 logits，从而改变概率分布的尖锐程度。"
SHA_A = hashlib.sha256(TEXT_A.encode("utf-8")).hexdigest()
SHA_B = hashlib.sha256(TEXT_B.encode("utf-8")).hexdigest()


def _row(package_id, slot, url, sha=SHA_A, state="已接受", review="complete"):
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
        "authority_anchors": [
            {"url": "https://example.org/api-reference", "supports": "vendor defaults"}
        ],
        "turns": [
            {"turn_id": turn_id, "text": f"{turn_id} text"}
            for turn_id in fm.REQUIRED_TURN_IDS
        ],
        "reference_facts": [
            {
                "fact_id": f"{package_id}-fact",
                "claim": "The article recommends a low temperature.",
                "source_id": sources[0][0],
                "locator": "blk-body-0001 | section: temperature",
                "quote": TEXT_A[2:20],
                "claim_basis": "source_claim",
            }
        ],
    }


def _clean_fixture():
    rows = [
        _row("pkg-a", 1, "https://a.example/one", SHA_A),
        _row("pkg-a", 2, "https://a.example/two", SHA_B),
        _row("pkg-b", 1, "https://b.example/one", SHA_A),
        _row("pkg-b", 2, "https://b.example/two", SHA_B),
    ]
    packages = [
        _package(
            "pkg-a",
            [("a1", "https://a.example/one", SHA_A), ("a2", "https://a.example/two", SHA_B)],
        ),
        _package(
            "pkg-b",
            [("b1", "https://b.example/one", SHA_A), ("b2", "https://b.example/two", SHA_B)],
        ),
    ]
    snapshots = {SHA_A: TEXT_A, SHA_B: TEXT_B}
    return rows, packages, snapshots


def test_clean_rehearsal_set_is_freezable():
    rows, packages, snapshots = _clean_fixture()

    assert fm.validate(rows, packages, rehearsal=True, snapshots=snapshots) == []


def test_unaccepted_source_blocks_the_freeze():
    rows, packages, snapshots = _clean_fixture()
    rows[0]["accepted_state"] = "待复核"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("not accepted" in problem for problem in problems)


def test_pending_reference_review_blocks_the_freeze():
    rows, packages, snapshots = _clean_fixture()
    rows[1]["reference_review"] = "pending"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("reference review" in problem for problem in problems)


def test_url_reused_across_packages_is_reported():
    rows, packages, snapshots = _clean_fixture()
    rows[2]["canonical_url"] = "https://a.example/one/"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("reused across packages" in problem for problem in problems)


def test_draft_without_recorded_revision_is_reported():
    rows, packages, snapshots = _clean_fixture()
    packages[0]["source_revisions"].pop("a2")

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("no source revision recorded for a2" in problem for problem in problems)


def test_accepted_source_without_snapshot_is_reported():
    rows, packages, snapshots = _clean_fixture()

    problems = fm.validate(rows, packages, rehearsal=True, snapshots={})

    assert any("no material snapshot" in problem for problem in problems)


def test_paraphrased_quote_is_reported():
    rows, packages, snapshots = _clean_fixture()
    packages[0]["reference_facts"][0]["quote"] = "温度参数建议使用非常高的取值"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("not verbatim" in problem for problem in problems)


def test_quote_with_reflowed_whitespace_still_matches():
    rows, packages, snapshots = _clean_fixture()
    packages[0]["reference_facts"][0]["quote"] = "温度参数建议使用较低的取值，\n 配合 top_p 一起调整"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert problems == []


def test_external_fact_requires_corroboration():
    rows, packages, snapshots = _clean_fixture()
    fact = packages[0]["reference_facts"][0]
    fact["claim_basis"] = "external_fact"

    problems = fm.validate(rows, packages, rehearsal=True, snapshots=snapshots)

    assert any("external_fact without an https corroboration URL" in problem for problem in problems)

    fact["corroboration"] = {"url": "https://docs.example.org/api", "quote": "default temperature is 1.0"}
    assert fm.validate(rows, packages, rehearsal=True, snapshots=snapshots) == []


def test_formal_freeze_requires_authority_anchors():
    rows, packages, snapshots = _clean_fixture()
    for package in packages:
        package["authority_anchors"] = []

    problems = fm.validate(rows, packages, rehearsal=False, snapshots=snapshots)

    assert any("no authority_anchors" in problem for problem in problems)


def test_anchor_cannot_reuse_a_package_source():
    rows, packages, snapshots = _clean_fixture()
    packages[0]["authority_anchors"] = [
        {"url": "https://a.example/one", "supports": "self"}
    ]

    problems = fm.validate(rows, packages, rehearsal=False, snapshots=snapshots)

    assert any("independent of the package sources" in problem for problem in problems)


def test_manifest_hash_is_deterministic_and_reacts_to_task_text():
    rows, packages, _ = _clean_fixture()
    first = fm.build_manifest(rows, packages, rehearsal=True)
    second = fm.build_manifest(rows, packages, rehearsal=True)

    assert fm.manifest_sha256(first) == fm.manifest_sha256(second)

    packages[0]["turns"][0]["text"] = "different wording"
    changed = fm.build_manifest(rows, packages, rehearsal=True)
    assert fm.manifest_sha256(changed) != fm.manifest_sha256(first)


def test_cli_refuses_to_write_a_blocked_set(tmp_path):
    rows, packages, snapshots = _clean_fixture()
    rows[0]["accepted_state"] = "待复核"
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
    materials = tmp_path / "materials"
    materials.mkdir()
    for digest, text in snapshots.items():
        (materials / f"{digest}.txt").write_text(text, encoding="utf-8")

    code = fm.main([
        "--sources", str(sources),
        "--packages", str(drafts),
        "--materials", str(materials),
        "--out", str(tmp_path / "manifest.json"),
        "--write",
    ])

    assert code == 1
    assert not (tmp_path / "manifest.json").exists()


def test_write_manifest_records_hash_and_timestamp(tmp_path):
    rows, packages, _ = _clean_fixture()
    payload = fm.build_manifest(rows, packages, rehearsal=True)
    out = tmp_path / "manifest.json"

    digest = fm.write_manifest(payload, out)

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["manifest_sha256"] == digest
    assert document["frozen_at"]
    assert document["rehearsal"] is True
    stripped = {
        key: value for key, value in document.items()
        if key not in {"frozen_at", "manifest_sha256"}
    }
    assert fm.manifest_sha256(stripped) == digest


def _write_fixture(tmp_path, rows, packages, snapshots):
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
    materials = tmp_path / "materials"
    materials.mkdir()
    for digest, text in snapshots.items():
        (materials / f"{digest}.txt").write_text(text, encoding="utf-8")
    return sources, drafts, materials


def test_rehearsal_only_filter_freezes_an_accepted_subset(tmp_path):
    rows, packages, snapshots = _clean_fixture()
    blocked_rows = [
        _row("pkg-c", 1, "https://c.example/one", SHA_A, state="待复核"),
        _row("pkg-c", 2, "https://c.example/two", SHA_B, state="待复核"),
    ]
    rows = rows + blocked_rows
    packages = packages + [
        _package(
            "pkg-c",
            [("c1", "https://c.example/one", SHA_A), ("c2", "https://c.example/two", SHA_B)],
        )
    ]
    sources, drafts, materials = _write_fixture(tmp_path, rows, packages, snapshots)
    out = tmp_path / "manifest.json"

    blocked = fm.main([
        "--sources", str(sources), "--packages", str(drafts),
        "--materials", str(materials), "--out", str(out),
        "--rehearsal", "--write",
    ])
    assert blocked == 1
    assert not out.exists()

    subset = fm.main([
        "--sources", str(sources), "--packages", str(drafts),
        "--materials", str(materials), "--out", str(out),
        "--rehearsal", "--only", "pkg-a,pkg-b", "--write",
    ])
    assert subset == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert [package["package_id"] for package in document["packages"]] == ["pkg-a", "pkg-b"]


def test_only_filter_is_refused_outside_rehearsal(tmp_path):
    rows, packages, snapshots = _clean_fixture()
    sources, drafts, materials = _write_fixture(tmp_path, rows, packages, snapshots)

    try:
        fm.main([
            "--sources", str(sources), "--packages", str(drafts),
            "--materials", str(materials), "--only", "pkg-a", "--check",
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--only must be rehearsal-only")


def test_only_filter_rejects_unknown_package(tmp_path):
    rows, packages, snapshots = _clean_fixture()
    sources, drafts, materials = _write_fixture(tmp_path, rows, packages, snapshots)

    try:
        fm.main([
            "--sources", str(sources), "--packages", str(drafts),
            "--materials", str(materials), "--rehearsal", "--only", "pkg-missing",
        ])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unknown --only ids must fail loudly")
