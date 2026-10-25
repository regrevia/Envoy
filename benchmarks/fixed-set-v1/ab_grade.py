#!/usr/bin/env python3
"""Score A/B answers against each package's frozen reference facts.

Automatic and deliberately conservative: a fact counts as covered when at least
60% of the distinctive words in its English ``claim`` appear in the answer, and
citations are counted by looking for each source's host or full URL. The host
agent still spot-checks samples; this script never rewrites an answer and never
fills in a fact that is absent from it.

Usage:
    python3 ab_grade.py --answers /tmp/AB3/s0/answers.jsonl ... --drafts task-drafts --out grade.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STOPWORDS = set(
    """about above after again against also among because been before being between both
    could does doing down during each either from further have having into itself just
    more most must neither once only other otherwise over same should since some such
    than that their them then there these they this those through under until very
    were what when where which while with within without would your article articles
    describes described according summary summarize argues argues""".split()
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{3,}")


def tokens(text: str) -> set[str]:
    return {word.lower().strip("'-") for word in WORD_RE.findall(text)} - STOPWORDS


def answer_tokens(answer: str) -> set[str]:
    return tokens(answer)


def fact_covered(claim: str, answer_words: set[str]) -> tuple[bool, float, list[str]]:
    claim_words = tokens(claim)
    if not claim_words:
        return False, 0.0, []
    present = sorted(claim_words & answer_words)
    ratio = len(present) / len(claim_words)
    return ratio >= 0.6, round(ratio, 3), present


def load_package_urls(manifest: Path | None) -> dict[str, list[str]]:
    """Map package_id to source URLs ordered by source slot."""
    if not manifest or not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    urls: dict[str, list[str]] = {}
    for source in data.get("sources", []):
        package_id = source.get("package_id")
        url = source.get("canonical_url") or source.get("url")
        if not package_id or not url:
            continue
        urls.setdefault(package_id, []).append((str(source.get("source_slot")), url))
    return {key: [url for _slot, url in sorted(value, key=lambda item: item[0])] for key, value in urls.items()}


def load_attribution(manifest: Path | None, materials: Path) -> dict[str, list[dict]]:
    """Package to ordered [{title, author}] hints taken from the frozen snapshot."""
    if not manifest or not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    rows: dict[str, list[tuple[str, dict]]] = {}
    for source in data.get("sources", []):
        package_id = source.get("package_id")
        sha = source.get("sha256")
        if not package_id or not sha:
            continue
        path = materials / f"{sha}.txt"
        if not path.is_file():
            continue
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        title = lines[0] if lines else ""
        # The snapshot repeats the headline; keep the first half as the match key.
        half = len(title) // 2
        if half and title[:half] == title[half:]:
            title = title[:half]
        author = ""
        for line in lines[1:6]:
            if len(line) <= 14 and not line.isdigit():
                author = line
                break
        rows.setdefault(package_id, []).append(
            (str(source.get("source_slot")), {"title": title, "author": author, "url": source.get("canonical_url") or ""})
        )
    return {key: [item for _slot, item in sorted(value, key=lambda pair: pair[0])] for key, value in rows.items()}


def attribution_hits(hints: list[dict], answer: str) -> list[int]:
    hits: list[int] = []
    lowered = answer.lower()
    for index, hint in enumerate(hints):
        title = (hint.get("title") or "")[:12]
        author = hint.get("author") or ""
        url = (hint.get("url") or "").lower()
        host = re.sub(r"^https?://", "", url).split("/")[0]
        if (title and title in answer) or (author and author in answer):
            hits.append(index + 1)
        elif host and host in lowered:
            hits.append(index + 1)
    return hits


def load_drafts(drafts: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(drafts.glob("*.json")):
        draft = json.loads(path.read_text(encoding="utf-8"))
        out[draft.get("package_id") or path.stem] = draft
    return out


def citation_hits(sources: list[dict], answer: str) -> list[str]:
    hits: list[str] = []
    lowered = answer.lower()
    for source in sources:
        url = (source.get("url") or "").strip()
        if not url:
            continue
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        if url.lower() in lowered or host in lowered:
            hits.append(source.get("source_id") or url)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", action="append", required=True)
    parser.add_argument("--drafts", default=str(Path(__file__).resolve().parent / "task-drafts"))
    parser.add_argument(
        "--manifest", default=str(Path(__file__).resolve().parent / "manifest.json")
    )
    parser.add_argument(
        "--materials", default=str(Path(__file__).resolve().parent / "materials")
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    package_urls_map = load_package_urls(Path(args.manifest))
    attribution_map = load_attribution(Path(args.manifest), Path(args.materials))
    drafts = load_drafts(Path(args.drafts))
    records: list[dict] = []
    for answer_file in args.answers:
        path = Path(answer_file)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            draft = drafts.get(row["package_id"])
            if not draft:
                continue
            words = answer_tokens(row.get("answer") or "")
            facts = draft.get("reference_facts") or []
            hit_ids: list[str] = []
            ratios: list[float] = []
            for fact in facts:
                covered, ratio, _present = fact_covered(fact.get("claim") or "", words)
                ratios.append(ratio)
                if covered:
                    hit_ids.append(fact.get("fact_id") or "")
            package_urls = package_urls_map.get(row["package_id"], [])
            sources = [
                {"source_id": source_id, "url": package_urls[index] if index < len(package_urls) else ""}
                for index, source_id in enumerate(draft.get("source_ids", []))
            ]
            records.append(
                {
                    "run_id": row.get("run_id"),
                    "package_id": row["package_id"],
                    "arm": row["arm"],
                    "role": row["role"],
                    "turn_correlation_id": row.get("turn_correlation_id"),
                    "is_error": row.get("is_error"),
                    "answer_chars": len(row.get("answer") or ""),
                    "facts_total": len(facts),
                    "facts_hit": len(hit_ids),
                    "fact_coverage": round(len(hit_ids) / len(facts), 3) if facts else None,
                    "mean_claim_overlap": round(sum(ratios) / len(ratios), 3) if ratios else None,
                    "fact_hits": hit_ids,
                    "host_mentions": citation_hits(sources, row.get("answer") or ""),
                    "attributed_sources": attribution_hits(
                        attribution_map.get(row["package_id"], []), row.get("answer") or ""
                    ),
                    "sources_total": len(sources),
                }
            )
    # One turn can be graded from the printed answer and, when the CLI printed
    # nothing, from the text recovered out of the transcript. Keep the longest
    # answer per turn so a recovered row can never overwrite a real one and a
    # turn is never counted twice.
    best: dict[tuple[str, str, str], dict] = {}
    for record in records:
        key = (record["package_id"], record["arm"], record["role"])
        current = best.get(key)
        if current is None or record["answer_chars"] > current["answer_chars"]:
            best[key] = record
    records = list(best.values())
    Path(args.out).write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"turns_graded": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
