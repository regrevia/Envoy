#!/usr/bin/env python3
"""Measure how many ordinary links Envoy can actually read.

The corpus is grouped by page family so a regression can be attributed to a
family instead of to a single host. Each URL is read through the public
``envoy read --request-json`` entrypoint, exactly the way a host would, and the
outcome is reduced to one of:

- ``readable``  text was delivered (status ready/partial with body characters)
- ``empty``     the read succeeded but carried no usable body
- ``blocked``   Envoy refused for a stated reason (login, safety, budget)
- ``failed``    an error path (network, extraction, capability)

Usage:
    python scripts/acceptance/link_coverage.py --out /tmp/link-coverage.json
    python scripts/acceptance/link_coverage.py --only social --jobs 2
    python scripts/acceptance/link_coverage.py --compare /tmp/link-baseline.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

CORPUS: list[tuple[str, str, str]] = [
    # (family, label, url)
    ("reference", "example.com", "https://example.com/"),
    ("reference", "wikipedia", "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)"),
    ("reference", "mdn", "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API"),
    ("reference", "python-docs", "https://docs.python.org/3/library/asyncio.html"),
    ("reference", "rust-book", "https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html"),
    ("reference", "kubernetes", "https://kubernetes.io/docs/concepts/workloads/pods/"),
    ("reference", "gutenberg", "https://www.gutenberg.org/files/1342/1342-h/1342-h.htm"),
    ("news", "bbc", "https://www.bbc.com/news"),
    ("news", "reuters-tech", "https://www.reuters.com/technology/"),
    ("news", "nytimes", "https://www.nytimes.com/section/technology"),
    ("news", "guardian", "https://www.theguardian.com/international"),
    ("blog", "medium", "https://medium.com/@karpathy/a-recipe-for-training-neural-networks-9c4c9f7d4b6c"),
    ("blog", "substack", "https://platformer.news/"),
    ("blog", "simonwillison", "https://simonwillison.net/"),
    ("blog", "danluu", "https://danluu.com/"),
    ("code", "github-repo", "https://github.com/psf/requests"),
    ("code", "github-issue", "https://github.com/psf/requests/issues/1"),
    ("code", "github-blob", "https://github.com/psf/requests/blob/main/README.md"),
    ("code", "pypi", "https://pypi.org/project/requests/"),
    ("code", "npm", "https://www.npmjs.com/package/express"),
    ("code", "gist", "https://gist.github.com/kennethreitz/973705"),
    ("forum", "stackoverflow", "https://stackoverflow.com/questions/11227809/why-is-processing-a-sorted-array-faster-than-processing-an-unsorted-array"),
    ("forum", "hackernews", "https://news.ycombinator.com/item?id=1"),
    ("forum", "reddit", "https://www.reddit.com/r/Python/comments/1c0v7q/"),
    ("forum", "discourse", "https://meta.discourse.org/t/what-is-discourse/1"),
    ("forum", "lobsters", "https://lobste.rs/"),
    ("social", "x-post", "https://x.com/OpenAI/status/1796823709356314851"),
    ("social", "mastodon", "https://mastodon.social/@Gargron"),
    ("social", "bluesky", "https://bsky.app/profile/bsky.app"),
    ("social", "zhihu", "https://www.zhihu.com/question/19550224"),
    ("social", "weibo", "https://weibo.com/u/1642904381"),
    ("social", "xiaohongshu", "https://www.xiaohongshu.com/explore"),
    ("video", "youtube", "https://www.youtube.com/watch?v=aircAruvnKk"),
    ("video", "bilibili", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("video", "vimeo", "https://vimeo.com/76979871"),
    ("video", "tiktok", "https://www.tiktok.com/@scout2015/video/6718335390845095173"),
    ("audio", "soundcloud", "https://soundcloud.com/forss/flickermood"),
    ("image", "pinterest", "https://www.pinterest.com/pin/1139481143253437748/"),
    ("image", "unsplash", "https://unsplash.com/s/photos/ocean"),
    ("document", "arxiv-abs", "https://arxiv.org/abs/1706.03762"),
    ("document", "arxiv-pdf", "https://arxiv.org/pdf/1706.03762"),
    ("document", "w3c-pdf", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"),
    ("document", "github-raw-md", "https://raw.githubusercontent.com/psf/requests/main/README.md"),
    ("document", "rfc", "https://www.rfc-editor.org/rfc/rfc9110.html"),
    ("spa", "notion-public", "https://www.notion.so/product/ai"),
    ("spa", "figma-community", "https://www.figma.com/community"),
    ("spa", "airtable-share", "https://airtable.com/"),
    ("spa", "google-sheets", "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"),
    ("feed", "hn-rss", "https://news.ycombinator.com/rss"),
    ("feed", "github-atom", "https://github.com/psf/requests/releases.atom"),
    ("api", "json-api", "https://api.github.com/repos/psf/requests"),
    ("web", "archive-org", "https://archive.org/details/nasa"),
    ("web", "amazon", "https://www.amazon.com/dp/B08N5WRWNW"),
    ("web", "wikipedia-zh", "https://zh.wikipedia.org/wiki/%E5%A4%A7%E6%A8%A1%E5%9E%8B"),
    ("web", "36kr", "https://36kr.com/"),
    ("web", "github-trending", "https://github.com/trending"),
    ("web", "w3.org", "https://www.w3.org/TR/html52/"),
]


@dataclass
class Outcome:
    family: str
    label: str
    url: str
    verdict: str = "failed"
    status: str = ""
    source_kind: str = ""
    chars: int = 0
    blocks: int = 0
    code: str = ""
    message: str = ""
    seconds: float = 0.0
    material_id: str = ""
    detail: dict = field(default_factory=dict)


def reachable(url: str, timeout: float = 15.0) -> bool:
    """Tell an unreachable host apart from a page Envoy cannot read."""
    if not shutil.which("curl"):
        return True
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.stdout.strip() not in {"", "000"}


def classify(response: dict) -> tuple[str, str, int, int, str, str]:
    result = str(response.get("result") or "")
    state = str(response.get("execution_state") or "")
    kind = str((response.get("source") or {}).get("kind") or "")
    blocks = response.get("blocks") or []
    chars = 0
    for block in blocks:
        content = block.get("content") or {}
        text = content.get("text")
        if isinstance(text, str):
            chars += len(text)
    errors = response.get("errors") or []
    code = str((errors[0] or {}).get("code") or "") if errors else ""
    message = str((errors[0] or {}).get("message") or "") if errors else ""
    if result == "ready" and chars >= 200:
        return "readable", result, chars, len(blocks), code, message
    if result == "partial" and chars >= 200:
        return "partial", result, chars, len(blocks), code, message
    discovered = ((response.get("coverage") or {}).get("modalities") or {}).get("text") or {}
    total_chars = int(((discovered.get("details") or {}).get("total_chars") or 0))
    if chars < 200 and total_chars >= 1000:
        # the page was extracted but almost nothing was delivered: a delivery
        # defect, not a reachability one.
        return "truncated", result, chars, len(blocks), code, message
    if result in {"ready", "partial"}:
        return "empty", result, chars, len(blocks), code, message
    if result in {"blocked", "needs_host", "needs_action"} or state in {"waiting_for_host", "blocked"}:
        return "blocked", result, chars, len(blocks), code, message
    return "failed", result, chars, len(blocks), code, message


def run_one(family: str, label: str, url: str, timeout: float, args_envoy: str = "envoy") -> Outcome:
    outcome = Outcome(family=family, label=label, url=url)
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": f"coverage-{label}",
        "source": {"kind": "auto", "url": url},
        "goal": "Read this page so the content can be answered from later",
        "budget": {"max_response_chars": 8000},
        "retention": {"derived_ttl_seconds": 600},
    }
    if not reachable(url):
        outcome.verdict = "unreachable"
        outcome.code = "host_unreachable"
        outcome.message = "no TCP/HTTP answer from this machine within 15s"
        return outcome
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [args_envoy, "read", "--request-json", "-"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        outcome.verdict = "failed"
        outcome.code = "timeout"
        outcome.message = f"no answer within {timeout:.0f}s"
        outcome.seconds = time.monotonic() - started
        return outcome
    outcome.seconds = time.monotonic() - started
    if proc.returncode != 0 and not proc.stdout.strip():
        outcome.verdict = "failed"
        outcome.code = "crash"
        outcome.message = (proc.stderr or "").strip()[-400:]
        return outcome
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        outcome.verdict = "failed"
        outcome.code = "bad-json"
        outcome.message = (proc.stdout or proc.stderr or "")[-400:]
        return outcome
    verdict, status, chars, blocks, code, message = classify(response)
    outcome.verdict = verdict
    outcome.status = status
    outcome.source_kind = str((response.get("source") or {}).get("kind") or "")
    outcome.chars = chars
    outcome.blocks = blocks
    outcome.code = code
    outcome.message = message[:400]
    outcome.material_id = str(response.get("material_id") or "")
    return outcome


def summarize(outcomes: list[Outcome]) -> dict:
    total = len(outcomes)
    by_verdict: dict[str, int] = {}
    by_family: dict[str, dict[str, int]] = {}
    for item in outcomes:
        by_verdict[item.verdict] = by_verdict.get(item.verdict, 0) + 1
        family = by_family.setdefault(item.family, {})
        family[item.verdict] = family.get(item.verdict, 0) + 1
    usable = by_verdict.get("readable", 0) + by_verdict.get("partial", 0)
    return {
        "total": total,
        "usable": usable,
        "usable_rate": round(usable / total, 3) if total else 0.0,
        "by_verdict": by_verdict,
        "by_family": by_family,
    }


def render(outcomes: list[Outcome], summary: dict) -> str:
    lines = [
        f"links: {summary['total']} · usable: {summary['usable']} "
        f"({summary['usable_rate'] * 100:.1f}%)",
        "",
        "family       readable partial empty truncated blocked failed unreachable",
    ]
    order = ["reference", "news", "blog", "code", "forum", "social", "video", "audio", "image", "document", "spa", "feed", "api", "web"]
    for family in order:
        counts = summary["by_family"].get(family)
        if not counts:
            continue
        lines.append(
            f"{family:<12} "
            + " ".join(
                f"{counts.get(key, 0):>8}"
                for key in ("readable", "partial", "empty", "truncated", "blocked", "failed", "unreachable")
            )
        )
    lines.append("")
    lines.append("misses:")
    for item in outcomes:
        if item.verdict in {"readable", "partial", "unreachable"}:
            continue
        lines.append(
            f"- {item.verdict:<7} {item.family:<9} {item.label:<18} "
            f"{item.code or item.status:<16} {item.message[:90]}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="", help="comma-separated families")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--compare", default="")
    parser.add_argument("--envoy", default="envoy", help="envoy executable")
    parser.add_argument(
        "--skip-hosts",
        default="xiaohongshu.com,zhihu.com,douyin.com,weibo.com,xiaohongshu",
        help="comma-separated host fragments left out of an automated run; these sites treat "
        "repeated automated hits as risk control and can warn the user's account",
    )
    parser.add_argument(
        "--hit-risk-hosts",
        action="store_true",
        help="include the skipped hosts anyway (only for a deliberate one-off check, never in a loop)",
    )
    args = parser.parse_args()

    families = {item.strip() for item in args.only.split(",") if item.strip()}
    selected = [entry for entry in CORPUS if not families or entry[0] in families]
    skip = [] if args.hit_risk_hosts else [h.strip() for h in args.skip_hosts.split(",") if h.strip()]
    corpus, skipped = [], []
    for entry in selected:
        if any(fragment in entry[2] for fragment in skip):
            skipped.append(entry)
        else:
            corpus.append(entry)
    for family, label, url in skipped:
        print(f"skipped {family:<9} {label:<18} {url} (risk-control host, use --hit-risk-hosts)", flush=True)
    outcomes: list[Outcome] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [
            pool.submit(run_one, family, label, url, args.timeout, args.envoy)
            for family, label, url in corpus
        ]
        for future in concurrent.futures.as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            print(f"{outcome.verdict:<7} {outcome.family:<9} {outcome.label:<18} {outcome.seconds:5.1f}s", flush=True)
    outcomes.sort(key=lambda item: (item.family, item.label))
    summary = summarize(outcomes)
    report = render(outcomes, summary)
    print()
    print(report)
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "summary": summary,
                    "outcomes": [item.__dict__ for item in outcomes],
                    "skipped": [
                        {"family": family, "label": label, "url": url}
                        for family, label, url in skipped
                    ],
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    if args.compare:
        previous = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print(f"\nbaseline usable: {previous['summary']['usable']}/{previous['summary']['total']} "
              f"({previous['summary']['usable_rate'] * 100:.1f}%) → now {summary['usable']}/{summary['total']} "
              f"({summary['usable_rate'] * 100:.1f}%)")
    return 0 if summary["usable_rate"] >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
