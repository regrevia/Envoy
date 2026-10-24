"""Validate and freeze the fixed-set package manifest.

The design freezes the source set, task text, and reference facts before
either arm runs, so a claimed manifest hash has to come from a checker
instead of from hand-edited JSON. This tool is the sanctioned producer of
`manifest.json`:

    python benchmarks/fixed-set-v1/freeze_manifest.py --check
    python benchmarks/fixed-set-v1/freeze_manifest.py --write [--rehearsal]

Hallucination controls are structural, not advisory:

- every accepted source must have a content-addressed snapshot in
  `materials/<sha256>.txt`, and the recorded revision hash is recomputed from
  that snapshot, so a source cannot be "accepted" on a hash nobody can check;
- every reference fact must carry a quote that occurs verbatim in that
  snapshot, so a fact cannot survive paraphrase or invention;
- a fact that asserts something about the world rather than about the source
  has to be `claim_basis: external_fact`, which requires an independent
  authoritative corroboration URL and quote.

`--check` reports every blocker and exits non-zero when the set is not
frozen-ready. `--rehearsal` relaxes set size for the 2-package instrumentation
run and marks the produced manifest as rehearsal-only so it can never be
mistaken for the confirmatory set.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_SCHEMA = "envoy.fixed-set-manifest/1"
SCHEMA_VERSION = "envoy.fixed-evaluation-package/1"
REQUIRED_TURN_IDS = ("initial", "followup-1", "followup-2")
DESIGN_PACKAGES = 25
DESIGN_UNIQUE_URLS = 50
DESIGN_PER_STRATUM = 5
STRATA = ("bilibili", "douyin", "xiaohongshu", "wechat", "cross_platform")
ACCEPTED_STATE = "已接受"
ACCEPTED_REVIEW = "complete"
CLAIM_BASES = ("source_claim", "external_fact")
MIN_QUOTE_CHARS = 12
REHEARSAL_MIN_PACKAGES = 2
REHEARSAL_MIN_URLS = 4


def read_sources(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_packages(directory: Path) -> list[dict]:
    packages = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = path.name
        packages.append(data)
    return packages


def load_snapshots(directory: Path) -> tuple[dict[str, str], list[str]]:
    """Material snapshots are content-addressed: <sha256>.txt."""
    texts: dict[str, str] = {}
    problems: list[str] = []
    if not directory.is_dir():
        return texts, [f"no materials directory at {directory}"]
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if path.stem != digest:
            problems.append(
                f"materials/{path.name}: content hashes to {digest[:12]}, not to its filename"
            )
        texts[digest] = text
    return texts, problems


def canonical_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def squash(text: str) -> str:
    """Whitespace-insensitive comparison: layout changes are not inventions."""
    return "".join(str(text).split())


def is_sha256(value: str) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def validate(
    rows: list[dict],
    packages: list[dict],
    *,
    rehearsal: bool = False,
    snapshots: dict[str, str] | None = None,
) -> list[str]:
    """Return every reason the set cannot be frozen; empty means ready."""
    snapshots = snapshots or {}
    problems: list[str] = []
    min_packages = REHEARSAL_MIN_PACKAGES if rehearsal else DESIGN_PACKAGES
    min_urls = REHEARSAL_MIN_URLS if rehearsal else DESIGN_UNIQUE_URLS

    if len(packages) < min_packages:
        problems.append(
            f"only {len(packages)} package drafts, design requires at least {min_packages}"
        )

    urls_by_package: dict[str, set[str]] = {}
    for row in rows:
        url = canonical_url(row.get("canonical_url", ""))
        if not url:
            problems.append(f"{row.get('package_id')} slot {row.get('source_slot')}: empty URL")
            continue
        urls_by_package.setdefault(row["package_id"], set()).add(url)

    owners: dict[str, set[str]] = {}
    for package_id, urls in urls_by_package.items():
        for url in urls:
            owners.setdefault(url, set()).add(package_id)
    for url, package_ids in sorted(owners.items()):
        if len(package_ids) > 1:
            problems.append(
                f"URL reused across packages ({', '.join(sorted(package_ids))}): {url}"
            )

    unique_urls = len(owners)
    if unique_urls < min_urls:
        problems.append(
            f"only {unique_urls} unique canonical URLs, design requires at least {min_urls}"
        )

    for row in rows:
        label = f"{row.get('package_id')} slot {row.get('source_slot')}"
        if row.get("accepted_state") != ACCEPTED_STATE:
            problems.append(f"{label}: source is {row.get('accepted_state') or 'unknown'}, not accepted")
        if row.get("reference_review") != ACCEPTED_REVIEW:
            problems.append(
                f"{label}: reference review is {row.get('reference_review') or 'unknown'}, not {ACCEPTED_REVIEW}"
            )
        digest = str(row.get("source_revision_sha256", "")).strip().lower()
        if not is_sha256(digest):
            problems.append(f"{label}: missing source revision SHA-256")
        elif row.get("accepted_state") == ACCEPTED_STATE and digest not in snapshots:
            problems.append(
                f"{label}: no material snapshot materials/{digest[:12]}...txt for the recorded revision"
            )

    rows_by_package: dict[str, dict[str, dict]] = {}
    for row in rows:
        rows_by_package.setdefault(row["package_id"], {})[row["source_slot"]] = row

    by_id = {package["package_id"]: package for package in packages}
    if len(by_id) != len(packages):
        problems.append("duplicate package_id across drafts")

    for package in packages:
        package_id = package.get("package_id")
        source_ids = list(package.get("source_ids") or [])
        revisions = package.get("source_revisions") or {}
        if len(set(source_ids)) != len(source_ids):
            problems.append(f"{package_id}: duplicate source_id in source_ids")
        if len(set(source_ids)) < 2:
            problems.append(f"{package_id}: fewer than two distinct sources")
        available = rows_by_package.get(package_id, {})
        for source_id in source_ids:
            revision = revisions.get(source_id)
            if not isinstance(revision, dict):
                problems.append(f"{package_id}: no source revision recorded for {source_id}")
                continue
            digest = str(revision.get("sha256", "")).strip().lower()
            if not is_sha256(digest):
                problems.append(f"{package_id}: {source_id} revision has no SHA-256")
            url = canonical_url(revision.get("url", ""))
            matching = [
                row
                for row in available.values()
                if canonical_url(row.get("canonical_url", "")) == url
            ]
            if not matching:
                problems.append(f"{package_id}: {source_id} URL is absent from source-intake.csv")
                continue
            if not any(
                is_sha256(row.get("source_revision_sha256", ""))
                and row["source_revision_sha256"].lower() == digest
                for row in matching
            ):
                problems.append(
                    f"{package_id}: {source_id} URL is recorded, but the draft's revision SHA-256 differs from the intake"
                )

        if package.get("schema_version") != SCHEMA_VERSION:
            problems.append(f"{package_id}: schema_version is not {SCHEMA_VERSION}")
        if not package.get("rubric_version"):
            problems.append(f"{package_id}: no rubric_version")
        status = str(package.get("status") or "")
        if not status.startswith(("frozen", "accepted")):
            problems.append(f"{package_id}: draft status is {status or 'missing'}, not frozen")
        turn_ids = [turn.get("turn_id") for turn in package.get("turns") or []]
        if tuple(turn_ids) != REQUIRED_TURN_IDS:
            problems.append(
                f"{package_id}: turn ids are {turn_ids}, expected {list(REQUIRED_TURN_IDS)}"
            )
        for turn in package.get("turns") or []:
            if not str(turn.get("text", "")).strip():
                problems.append(f"{package_id}: turn {turn.get('turn_id')} has no text")

        source_urls = {
            canonical_url(revision.get("url", ""))
            for revision in revisions.values()
            if isinstance(revision, dict)
        }
        anchors = package.get("authority_anchors") or []
        if not rehearsal:
            if not anchors:
                problems.append(f"{package_id}: no authority_anchors for its factual claims")
            for anchor in anchors:
                anchor_url = str(anchor.get("url", "")).strip()
                if not anchor_url.startswith("https://"):
                    problems.append(f"{package_id}: authority anchor is not an https URL")
                elif canonical_url(anchor_url) in source_urls:
                    problems.append(
                        f"{package_id}: authority anchor must be independent of the package sources"
                    )
                if not str(anchor.get("supports", "")).strip():
                    problems.append(f"{package_id}: authority anchor without a 'supports' note")

        facts = package.get("reference_facts") or []
        if not facts:
            problems.append(f"{package_id}: no reference facts")
        fact_ids: set[str] = set()
        for fact in facts:
            fact_id = str(fact.get("fact_id") or "?")
            if fact_id in fact_ids:
                problems.append(f"{package_id}: duplicate fact_id {fact_id}")
            fact_ids.add(fact_id)
            missing = [
                key
                for key in ("claim", "source_id", "locator", "quote")
                if not str(fact.get(key, "")).strip()
            ]
            if missing:
                problems.append(f"{package_id}: fact {fact_id} lacks {', '.join(missing)}")
                continue
            basis = str(fact.get("claim_basis") or "source_claim")
            if basis not in CLAIM_BASES:
                problems.append(f"{package_id}: fact {fact_id} has unknown claim_basis {basis}")
            if fact["source_id"] not in revisions:
                problems.append(f"{package_id}: fact {fact_id} cites unknown source {fact['source_id']}")
                continue
            quote = str(fact["quote"])
            if len(squash(quote)) < MIN_QUOTE_CHARS:
                problems.append(
                    f"{package_id}: fact {fact_id} quote is shorter than {MIN_QUOTE_CHARS} characters"
                )
            digest = str(revisions[fact["source_id"]].get("sha256", "")).strip().lower()
            text = snapshots.get(digest)
            if text is None:
                problems.append(
                    f"{package_id}: fact {fact_id} has no material snapshot for {fact['source_id']}"
                )
            elif squash(quote) not in squash(text):
                problems.append(
                    f"{package_id}: fact {fact_id} quote is not verbatim in {fact['source_id']} - paraphrase or invention"
                )
            if basis == "external_fact":
                corroboration = fact.get("corroboration") or {}
                if not str(corroboration.get("url", "")).startswith("https://"):
                    problems.append(
                        f"{package_id}: fact {fact_id} is an external_fact without an https corroboration URL"
                    )
                if len(squash(str(corroboration.get("quote", "")))) < MIN_QUOTE_CHARS:
                    problems.append(
                        f"{package_id}: fact {fact_id} is an external_fact without a corroboration quote"
                    )

    if not rehearsal:
        strata: dict[str, int] = {}
        for package in packages:
            stratum = package.get("stratum", "")
            strata[stratum] = strata.get(stratum, 0) + 1
        for stratum in STRATA:
            if strata.get(stratum, 0) < DESIGN_PER_STRATUM:
                problems.append(
                    f"stratum {stratum} has {strata.get(stratum, 0)} packages, expected {DESIGN_PER_STRATUM}"
                )

    return problems


def build_manifest(rows: list[dict], packages: list[dict], *, rehearsal: bool) -> dict:
    """Deterministic payload: same inputs always hash the same."""
    payload_packages = []
    for package in sorted(packages, key=lambda item: item["package_id"]):
        payload_packages.append({
            "package_id": package["package_id"],
            "stratum": package["stratum"],
            "status": "rehearsal_only" if rehearsal else "frozen",
            "rubric_version": package["rubric_version"],
            "deliverable": package.get("deliverable", ""),
            "authority_anchors": package.get("authority_anchors") or [],
            "source_revisions": {
                source_id: {
                    "url": canonical_url(package["source_revisions"][source_id]["url"]),
                    "sha256": package["source_revisions"][source_id]["sha256"].lower(),
                }
                for source_id in sorted(package["source_revisions"])
            },
            "turns": [
                {"turn_id": turn["turn_id"], "text": turn["text"]}
                for turn in package["turns"]
            ],
            "reference_facts": package.get("reference_facts") or [],
            "expected_source_attribution": package.get("expected_source_attribution") or [],
            "acceptable_partial_outcomes": package.get("acceptable_partial_outcomes") or [],
        })
    sources = sorted(
        (
            {
                "package_id": row["package_id"],
                "source_slot": row["source_slot"],
                "source_class": row.get("source_class", ""),
                "canonical_url": canonical_url(row["canonical_url"]),
                "sha256": str(row.get("source_revision_sha256", "")).lower(),
                "coverage_note": row.get("coverage_note", ""),
            }
            for row in rows
        ),
        key=lambda item: (item["package_id"], item["source_slot"]),
    )
    return {
        "schema_version": MANIFEST_SCHEMA,
        "design": "fixed-set-v1",
        "rehearsal": rehearsal,
        "packages": payload_packages,
        "sources": sources,
    }


def manifest_sha256(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_manifest(payload: dict, path: Path) -> str:
    digest = manifest_sha256(payload)
    document = dict(payload)
    document["frozen_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    document["manifest_sha256"] = digest
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", type=Path, default=ROOT / "source-intake.csv")
    parser.add_argument("--packages", type=Path, default=ROOT / "task-drafts")
    parser.add_argument("--materials", type=Path, default=ROOT / "materials")
    parser.add_argument("--out", type=Path, default=ROOT / "manifest.json")
    parser.add_argument("--check", action="store_true", help="validate only; do not write")
    parser.add_argument("--write", action="store_true", help="write the frozen manifest")
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="relax set-size rules for the 2-package instrumentation rehearsal",
    )
    parser.add_argument(
        "--only",
        default="",
        help=(
            "rehearsal only: comma-separated package ids to freeze, so accepted "
            "packages can be rehearsed while the rest of the set is still pending"
        ),
    )
    args = parser.parse_args(argv)
    only = [item.strip() for item in args.only.split(",") if item.strip()]
    if only and not args.rehearsal:
        parser.error("--only is rehearsal-only; a confirmatory manifest freezes the whole set")

    rows = read_sources(args.sources)
    packages = read_packages(args.packages)
    if only:
        known = {package["package_id"] for package in packages}
        unknown = sorted(set(only) - known)
        if unknown:
            parser.error(f"--only names packages with no draft: {', '.join(unknown)}")
        rows = [row for row in rows if row["package_id"] in only]
        packages = [package for package in packages if package["package_id"] in only]
    snapshots, snapshot_problems = load_snapshots(args.materials)
    problems = snapshot_problems + validate(
        rows, packages, rehearsal=args.rehearsal, snapshots=snapshots
    )
    if problems:
        print(f"{len(problems)} blocker(s); the set is not freezable:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    payload = build_manifest(rows, packages, rehearsal=args.rehearsal)
    if args.write:
        digest = write_manifest(payload, args.out)
        print(f"wrote {args.out} ({'rehearsal' if args.rehearsal else 'confirmatory'})")
        print(f"manifest_sha256: {digest}")
    else:
        print("manifest is freezable")
        print(f"manifest_sha256: {manifest_sha256(payload)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
