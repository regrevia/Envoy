#!/usr/bin/env python3
"""Compose frozen package drafts and intake rows from capture specs.

Collection runs produce raw captures; this script turns a reviewed spec into the
two artifacts the freeze gate reads, so neither is hand-edited:

* ``task-drafts/<package_id>.json`` - the canonical package draft.
* ``source-intake.csv`` - the authoritative intake rows for that package.

Before writing anything it re-verifies the evidence chain: the snapshot file must
exist, its content must hash to the recorded SHA-256, and every reference-fact
quote must be a verbatim (whitespace-insensitive) substring of its snapshot.

Usage:
    python3 compose_packages.py --specs DIR [--dry-run]
    python3 compose_packages.py --specs DIR --from-draft task-drafts/wechat-01.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from freeze_manifest import ACCEPTED_REVIEW, ACCEPTED_STATE, SCHEMA_VERSION, canonical_url, squash

ROOT = Path(__file__).resolve().parent
INTAKE_COLUMNS = [
    "package_id",
    "stratum",
    "source_slot",
    "source_class",
    "canonical_url",
    "accepted_state",
    "revalidated_at",
    "source_revision_sha256",
    "coverage_note",
    "reference_review",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_snapshots(materials: Path) -> dict[str, str]:
    snapshots: dict[str, str] = {}
    for path in sorted(materials.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != path.stem:
            raise SystemExit(f"snapshot file name does not match its content: {path.name} != {digest}")
        snapshots[digest] = text
    return snapshots


def spec_from_draft(path: Path) -> dict:
    draft = json.loads(path.read_text(encoding="utf-8"))
    revisions = draft.get("source_revisions") or {}
    sources = []
    for slot, source_id in enumerate(draft["source_ids"], start=1):
        revision = revisions[source_id]
        sources.append(
            {
                "slot": slot,
                "source_id": source_id,
                "source_class": revision.get("source_class") or "public_web_source",
                "url": revision["url"],
                "sha256": revision["sha256"],
                "captured_at": revision.get("captured_at") or "",
                "result": revision.get("result") or "ok",
                "coverage": revision.get("coverage") or "",
            }
        )
    return {
        "package_id": draft["package_id"],
        "stratum": draft["stratum"],
        "topic": draft.get("topic") or draft["package_id"],
        "deliverable": draft["deliverable"],
        "sources": sources,
        "facts": draft.get("reference_facts") or [],
        "turns": {turn["turn_id"]: turn["text"] for turn in draft.get("turns") or []},
        "authority_anchors": draft.get("authority_anchors") or [],
    }


def verify_spec(spec: dict, snapshots: dict[str, str]) -> list[str]:
    problems: list[str] = []
    package_id = spec.get("package_id") or "?"
    sources = spec.get("sources") or []
    source_ids = [source_id_of(source) for source in sources]
    if len(set(url_of(source) for source in sources)) < 2:
        problems.append(f"{package_id}: fewer than two distinct source URLs")
    revisions = {}
    for source, source_id in zip(sources, source_ids):
        digest = str(source.get("sha256") or "").strip().lower()
        text = snapshots.get(digest)
        if text is None:
            problems.append(f"{package_id}: {source_id} has no snapshot materials/{digest[:12]}.txt")
        revisions[source_id] = source
    for fact in spec.get("facts") or []:
        fact_id = fact.get("fact_id") or "?"
        source_id = fact.get("source_id") or source_ids[int(fact.get("slot", 1)) - 1]
        source = revisions.get(source_id)
        if source is None:
            problems.append(f"{package_id}: fact {fact_id} cites unknown source {source_id}")
            continue
        text = snapshots.get(str(source.get("sha256", "")).strip().lower())
        if text is None:
            continue
        quote = str(fact.get("quote") or "")
        if len(squash(quote)) < 12:
            problems.append(f"{package_id}: fact {fact_id} quote is shorter than 12 characters")
        elif squash(quote) not in squash(text):
            problems.append(f"{package_id}: fact {fact_id} quote is not verbatim in {source_id}")
        if str(fact.get("claim_basis") or "source_claim") == "external_fact":
            corroboration = fact.get("corroboration") or {}
            if not str(corroboration.get("url", "")).startswith("https://"):
                problems.append(f"{package_id}: fact {fact_id} external_fact lacks https corroboration")
            if len(squash(str(corroboration.get("quote", "")))) < 12:
                problems.append(f"{package_id}: fact {fact_id} external_fact lacks corroboration quote")
    turn_ids = list((spec.get("turns") or {}).keys())
    if turn_ids != ["initial", "followup-1", "followup-2"]:
        problems.append(f"{package_id}: turn ids are {turn_ids}")
    if not spec.get("authority_anchors"):
        problems.append(f"{package_id}: no authority_anchors")
    source_urls = {canonical_url(url_of(source)) for source in sources}
    for anchor in spec.get("authority_anchors") or []:
        url = str(anchor.get("url", "")).strip()
        if not url.startswith("https://"):
            problems.append(f"{package_id}: authority anchor is not https: {url}")
        elif canonical_url(url) in source_urls:
            problems.append(f"{package_id}: authority anchor repeats a package source: {url}")
        if not str(anchor.get("supports", "")).strip():
            problems.append(f"{package_id}: authority anchor without supports note")
    return problems


def source_id_of(source: dict) -> str:
    return str(source.get("source_id") or f"s{source['slot']}")


def url_of(source: dict) -> str:
    return str(source.get("canonical_url") or source.get("url") or "").strip()


def build_draft(spec: dict, snapshots: dict[str, str]) -> dict:
    sources = spec["sources"]
    source_ids = [source_id_of(source) for source in sources]
    revisions = {}
    for source, source_id in zip(sources, source_ids):
        digest = str(source["sha256"]).strip().lower()
        revisions[source_id] = {
            "url": url_of(source),
            "sha256": digest,
            "snapshot": f"materials/{digest}.txt",
            "captured_at": source.get("captured_at") or "",
            "result": source.get("result") or "ok",
            "coverage": source.get("coverage") or "",
            "source_class": source.get("source_class") or "public_web_source",
        }
    facts = []
    for fact in spec["facts"]:
        entry = {
            "fact_id": fact["fact_id"],
            "claim": fact["claim"],
            "source_id": fact.get("source_id") or source_ids[int(fact.get("slot", 1)) - 1],
            "locator": fact["locator"],
            "quote": fact["quote"],
            "claim_basis": fact.get("claim_basis") or "source_claim",
        }
        if entry["claim_basis"] == "external_fact":
            entry["corroboration"] = fact["corroboration"]
        facts.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "frozen_reference_reviewed",
        "package_id": spec["package_id"],
        "stratum": spec["stratum"],
        "topic": spec.get("topic") or spec["package_id"],
        "source_ids": source_ids,
        "source_revisions": revisions,
        "deliverable": spec["deliverable"],
        "rubric_version": "envoy-fixed-set-v1-rubric-1",
        "turns": [{"turn_id": turn_id, "text": spec["turns"][turn_id]} for turn_id in
                  ("initial", "followup-1", "followup-2")],
        "reference_facts": facts,
        "expected_source_attribution": sorted(source_ids),
        "acceptable_partial_outcomes": spec.get("acceptable_partial_outcomes") or [],
        "frozen_at": None,
        "manifest_sha256": None,
        "reference_review": {
            "reviewed_at": spec.get("reviewed_at") or now_iso(),
            "reviewer": spec.get("reviewer") or "lead",
            "method": "captured snapshot re-read; every quote re-checked as a verbatim substring",
            "facts": [fact["fact_id"] for fact in facts],
        },
        "authority_anchors": spec["authority_anchors"],
    }


def intake_rows(spec: dict) -> list[dict]:
    rows = []
    for source in spec["sources"]:
        digest = str(source["sha256"]).strip().lower()
        rows.append(
            {
                "package_id": spec["package_id"],
                "stratum": spec["stratum"],
                "source_slot": str(source["slot"]),
                "source_class": source.get("source_class") or "public_web_source",
                "canonical_url": url_of(source),
                "accepted_state": ACCEPTED_STATE,
                "revalidated_at": source.get("captured_at") or now_iso(),
                "source_revision_sha256": digest,
                "coverage_note": source.get("coverage") or "",
                "reference_review": ACCEPTED_REVIEW,
            }
        )
    return rows


def update_intake(path: Path, specs: list[dict]) -> None:
    existing: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            existing = list(csv.DictReader(handle))
    replaced = {spec["package_id"] for spec in specs}
    kept = [row for row in existing if row.get("package_id") not in replaced]
    fresh: list[dict] = []
    for spec in specs:
        fresh.extend(intake_rows(spec))
    merged = kept + fresh
    merged.sort(key=lambda row: (row.get("package_id", ""), str(row.get("source_slot", ""))))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTAKE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in merged:
            writer.writerow({key: row.get(key, "") for key in INTAKE_COLUMNS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--specs", type=Path, help="directory of package spec JSON files")
    parser.add_argument("--from-draft", type=Path, action="append", default=[],
                        help="derive a spec from an existing draft (repeatable); print only")
    parser.add_argument("--materials", type=Path, default=ROOT / "materials")
    parser.add_argument("--drafts", type=Path, default=ROOT / "task-drafts")
    parser.add_argument("--intake", type=Path, default=ROOT / "source-intake.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    snapshots = load_snapshots(args.materials)
    specs: list[dict] = []
    if args.from_draft:
        for path in args.from_draft:
            spec = spec_from_draft(path)
            specs.append(spec)
            if args.dry_run or not args.specs:
                print(json.dumps(spec, ensure_ascii=False, indent=2))
    if args.specs:
        for path in sorted(args.specs.glob("*.json")):
            specs.append(json.loads(path.read_text(encoding="utf-8")))
    if not specs:
        parser.error("nothing to compose: pass --specs and/or --from-draft")

    problems: list[str] = []
    for spec in specs:
        problems.extend(verify_spec(spec, snapshots))
    if problems:
        print(f"{len(problems)} spec problem(s); nothing written:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"{len(specs)} spec(s) verified; dry run wrote nothing")
        return 0

    args.drafts.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        draft = build_draft(spec, snapshots)
        path = args.drafts / f"{spec['package_id']}.json"
        path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    update_intake(args.intake, specs)
    print(f"updated {args.intake.relative_to(ROOT)} for {len(specs)} package(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
