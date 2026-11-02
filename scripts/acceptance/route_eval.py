#!/usr/bin/env python3
"""Route-correctness evaluation for the Envoy link reader.

This is an *offline* evaluator. By default it reads the fixture at
``tests/fixtures/routing/links.json`` and answers one question per link:

    Which reader should this URL go to, and which rule decided that?

It never opens a socket in ``--dry-run`` (the default). ``--live`` is a stub and
returns exit code 2 without touching the network; see ``LIVE_PLAN`` below.

Why routing and not "how many times Envoy was called"
-----------------------------------------------------
A route is correct when the URL reaches the reader that can actually return the
evidence at the lowest cost:

    host_fetch   plain HTTP fetch   - server-rendered HTML/text
    gh_git       git / gh API       - source-code hosts (dedicated GitHub path)
    envoy        one-shot browser   - JS, cookies, scroll; no retention
    envoy_task   long task          - retention, page windows, ASR, resume

Sending a blob page to Envoy yields a title and never the source. Sending a
static page to a long task burns a browser session and a checkpoint for nothing.
Counting Envoy invocations rewards the second mistake.

Rules are read from the product code when it is importable, and degrade to a
labelled local fallback when it is not:

    hooks.is_code_host_diff_url      exists at HEAD; classifies diff URLs
    hooks.inspect_prompt             the only URL interception point in hooks.py;
                                     a URL is gh_git when its .urls list is non-empty
    envoy_cli.github_source          optional adapter module; may be absent
    SKILL.md routing table           parsed for its documented route rows

A missing module is reported as ``unavailable`` with a reason; it never raises.

Usage
-----
    packages/envoy-cli/.venv/bin/python scripts/acceptance/route_eval.py --dry-run
    packages/envoy-cli/.venv/bin/python scripts/acceptance/route_eval.py --json-only

Exit codes: 0 clean, 1 schema or route mismatch, 2 live mode unavailable.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "routing" / "links.json"
SKILL = ROOT / "integrations" / "skills" / "envoy" / "SKILL.md"
CLI_SRC = ROOT / "packages" / "envoy-cli" / "src"
CORE_SRC = ROOT / "packages" / "envoy-core" / "src"

ROUTES = ("host_fetch", "gh_git", "envoy", "envoy_task")
CATEGORIES = (
    "simple_static",
    "github_repo",
    "github_nav_file",
    "complex_js",
    "long_doc_pdf",
    "video",
)
PER_CATEGORY = 10
TOTAL_LINKS = len(CATEGORIES) * PER_CATEGORY

# Documented per-route ceilings for the *target* first-evidence seconds. A
# fixture value above its route ceiling is a schema error, not a slow link.
SECONDS_CEILING = {
    "host_fetch": 10.0,
    "gh_git": 15.0,
    "envoy": 30.0,
    "envoy_task": 180.0,
}

# --- live mode -----------------------------------------------------------
# TODO(route_eval/live): implement real requests. Requirements before enabling:
#   * per-link timeout from LIVE_PLAN and no retry storm (one attempt per link);
#   * per-route budget caps, counted in HTTP requests and wall-clock seconds;
#   * never fetch a fixture link more than once per run, and never in parallel
#     across the same host;
#   * record first_evidence_seconds as a *measurement*, written to a separate
#     artifact, never back into expect_first_evidence_seconds (that field is a
#     target and stays a target);
#   * skip entries whose expected_route needs a browser when no browser is
#     available, and report them as not_run instead of failing the run.
LIVE_PLAN = {
    "max_links_per_run": 12,
    "per_link_timeout_seconds": 20.0,
    "total_budget_seconds": 180.0,
    "max_http_requests_per_link": 4,
    "network": "disabled",
}

_CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
_VIDEO_HOSTS = {
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "bilibili.com",
    "www.bilibili.com",
    "douyin.com",
    "www.douyin.com",
    "vimeo.com",
    "ted.com",
    "www.ted.com",
    "xiaohongshu.com",
    "www.xiaohongshu.com",
    "xhslink.com",
}
_VIDEO_PATH_RE = re.compile(r"^/(?:watch|playlist|video|talks|explore|shorts)/", re.I)

# Hosts that serve a client-rendered shell: the first correct reader is a
# browser, and one goal fits one read, so the route is `envoy` not `envoy_task`.
_BROWSER_HOSTS = {
    "vercel.com",
    "linear.app",
    "figma.com",
    "notion.so",
    "web.telegram.org",
    "reddit.com",
    "x.com",
    "twitter.com",
    "discord.com",
    "open.spotify.com",
    "canva.com",
}
_LONG_TEXT_PATH_RULES = (
    ("w3.org", re.compile(r"^/TR/", re.I)),
    ("docs.python.org", re.compile(r"^/3/reference/", re.I)),
)
_LONG_RFC_MIN = 5000  # heuristic: RFCs numbered >= 5000 are the multi-hundred-page ones


@dataclass
class Rule:
    name: str
    source: str
    status: str = "available"  # available | unavailable
    detail: str = ""
    hits: int = 0

    def hit(self) -> None:
        self.hits += 1


@dataclass
class Ruleset:
    rules: dict[str, Rule] = field(default_factory=dict)
    hooks_module: object | None = None
    routing_rule_name: str | None = None
    github_source: bool = False
    skill_table: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, rule: Rule) -> Rule:
        self.rules[rule.name] = rule
        return rule


def _host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# A bare repository page (owner/repo, optionally .git) is the one code-host URL
# the product hands to the shell route instead of Envoy.
_REPO_SHELL_RE = re.compile(r"^/[^/]+/[^/]+(?:\.git)?/?$")


def _path(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


def _on_code_host(url: str) -> bool:
    host = _host(url)
    return any(host == item or host.endswith("." + item) for item in _CODE_HOSTS)


# --- rule loading --------------------------------------------------------


def load_rules() -> Ruleset:
    rs = Ruleset()
    for src in (CLI_SRC, CORE_SRC):
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

    diff = rs.add(Rule("hooks.is_code_host_diff_url", "packages/envoy-cli/src/envoy_cli/hooks.py"))
    try:
        hooks = importlib.import_module("envoy_cli.hooks")
        rs.hooks_module = hooks
        if not callable(getattr(hooks, "is_code_host_diff_url", None)):
            diff.status = "unavailable"
            diff.detail = "module imported but is_code_host_diff_url is not callable"
    except Exception as exc:  # noqa: BLE001 - any import failure must not stop the eval
        diff.status = "unavailable"
        diff.detail = f"{type(exc).__name__}: {exc}"

    routing = rs.add(Rule("hooks.inspect_prompt", "packages/envoy-cli/src/envoy_cli/hooks.py"))
    if rs.hooks_module is not None and callable(
        getattr(rs.hooks_module, "inspect_prompt", None)
    ):
        rs.routing_rule_name = "inspect_prompt"
    else:
        routing.status = "unavailable"
        routing.detail = (
            "hooks module imported but inspect_prompt is not callable"
            if rs.hooks_module is not None
            else "hooks module unavailable"
        )

    gh = rs.add(Rule("envoy_cli.github_source", "packages/envoy-cli/src/envoy_cli/github_source.py"))
    try:
        spec = importlib.util.find_spec("envoy_cli.github_source")
    except Exception as exc:  # noqa: BLE001
        spec, gh.detail = None, f"{type(exc).__name__}: {exc}"
    if spec is None:
        gh.status = "unavailable"
        gh.detail = gh.detail or "module not present at this revision"
    else:
        rs.github_source = True

    for name, source, detail in (
        ("route_eval.code_host_repo_shell", "scripts/acceptance/route_eval.py",
         "local pattern: repo root, releases, tags, branches, org repo list"),
        ("route_eval.video_or_audio", "scripts/acceptance/route_eval.py",
         "local host/path list; media evidence needs ASR and retention"),
        ("route_eval.pdf_document", "scripts/acceptance/route_eval.py",
         "local .pdf path or arxiv /pdf/ prefix; page windows plus retention"),
        ("route_eval.long_text_document", "scripts/acceptance/route_eval.py",
         "local long-RFC / W3C TR / Python reference rules"),
        ("route_eval.browser_required", "scripts/acceptance/route_eval.py",
         "local known-SPA host list; page needs JS, answer needs no retention"),
        ("route_eval.plain_static", "scripts/acceptance/route_eval.py",
         "default: server-rendered page, one bounded fetch"),
    ):
        rs.add(Rule(name, source, detail=detail))

    rs.skill_table = parse_skill_table()
    return rs


def parse_skill_table() -> dict:
    """Parse the `## Decide before you call` table into route hints."""

    result = {
        "path": "integrations/skills/envoy/SKILL.md",
        "status": "unavailable",
        "rows_parsed": 0,
        "route_rows": 0,
        "hints": {},
        "errors": [],
    }
    if not SKILL.is_file():
        result["errors"].append("SKILL.md not found")
        return result
    try:
        lines = SKILL.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result

    start = next((i for i, line in enumerate(lines) if line.startswith("## Decide before you call")), None)
    if start is None:
        result["errors"].append("section '## Decide before you call' not found")
        return result

    rows, hints = 0, {}
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= {"-", " ", ":"}:
            continue
        rows += 1
        do_cell = cells[-1].lower()
        route = None
        # Order matters: a row may offer a git-based tool *and* envoy_read as the
        # fallback ("with gh available ... otherwise envoy_read"). The git-based
        # tool is the preferred cell, so it is tested before the fallback.
        if any(token in do_cell for token in ("gh api", "gh repo", "git show", "git ls-remote")):
            route = "gh_git"
        elif "envoy_read" in do_cell:
            route = "envoy"
        elif "plain fetch" in do_cell:
            route = "host_fetch"
        if route:
            hints[route] = hints.get(route, 0) + 1
    result.update(status="available", rows_parsed=rows, route_rows=sum(hints.values()), hints=hints)
    return result


# --- classification ------------------------------------------------------


def _is_video(url: str) -> bool:
    host = _host(url)
    if host in _VIDEO_HOSTS:
        return True
    return any(host.endswith("." + item) for item in ("youtube.com", "bilibili.com", "douyin.com"))


def _is_pdf(url: str) -> bool:
    path = _path(url).lower()
    host = _host(url)
    return path.endswith(".pdf") or (host == "arxiv.org" and path.startswith("/pdf/"))


def _is_long_text(url: str) -> bool:
    host, path = _host(url), _path(url)
    if any(host == h or host.endswith("." + h) for h, pat in _LONG_TEXT_PATH_RULES if pat.search(path)):
        return True
    if host == "rfc-editor.org" or host.endswith(".rfc-editor.org"):
        match = re.match(r"^/rfc/rfc(\d+)\.txt$", path, re.I)
        if match and int(match.group(1)) >= _LONG_RFC_MIN:
            return True
    return False


def _needs_browser(url: str) -> bool:
    host = _host(url)
    return any(host == item or host.endswith("." + item) for item in _BROWSER_HOSTS)


def classify(url: str, rules: Ruleset) -> tuple[str, str, str]:
    """Return (route, rule_name, rule_status) for one URL."""

    diff = rules.rules["hooks.is_code_host_diff_url"]
    routing = rules.rules["hooks.inspect_prompt"]

    if diff.status == "available" and _on_code_host(url):
        try:
            if rules.hooks_module.is_code_host_diff_url(url):  # type: ignore[union-attr]
                diff.hit()
                return "gh_git", diff.name, "available"
        except Exception as exc:  # noqa: BLE001 - a broken predicate is reported, not fatal
            diff.status = "unavailable"
            diff.detail = f"raised {type(exc).__name__}: {exc}"

    # Dedicated GitHub route: hooks.inspect_prompt is the only URL interception
    # point in the product. A non-empty .urls list means hooks sends the link to
    # Envoy, so the evaluator reports what the product actually does, not a mirror.
    if routing.status == "available" and _on_code_host(url):
        try:
            decision = rules.hooks_module.inspect_prompt(url)  # type: ignore[union-attr]
            if tuple(getattr(decision, "urls", ()) or ()):
                routing.hit()
                return "gh_git", routing.name, "available"
        except Exception as exc:  # noqa: BLE001 - a broken predicate is reported, not fatal
            routing.status = "unavailable"
            routing.detail = f"raised {type(exc).__name__}: {exc}"

    if _on_code_host(url) and _REPO_SHELL_RE.search(_path(url)):
        rules.rules["route_eval.code_host_repo_shell"].hit()
        return "gh_git", "route_eval.code_host_repo_shell", "available"

    if _is_video(url):
        rules.rules["route_eval.video_or_audio"].hit()
        return "envoy_task", "route_eval.video_or_audio", "available"

    if _is_pdf(url):
        rules.rules["route_eval.pdf_document"].hit()
        return "envoy_task", "route_eval.pdf_document", "available"

    if _is_long_text(url):
        rules.rules["route_eval.long_text_document"].hit()
        return "envoy_task", "route_eval.long_text_document", "available"

    if _needs_browser(url):
        rules.rules["route_eval.browser_required"].hit()
        return "envoy", "route_eval.browser_required", "available"

    rules.rules["route_eval.plain_static"].hit()
    return "host_fetch", "route_eval.plain_static", "available"


# --- fixture validation --------------------------------------------------

_REQUIRED_ITEM_KEYS = {"id", "category", "url", "expected_route", "why", "expect_first_evidence_seconds"}
_OPTIONAL_ITEM_KEYS = {"capability"}


def validate(payload: object) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["fixture root must be a JSON object"], []
    links = payload.get("links")
    if not isinstance(links, list):
        return ["fixture.links must be a list"], []
    if payload.get("schema_version") != 1:
        errors.append(f"schema_version must be 1, got {payload.get('schema_version')!r}")
    declared = payload.get("counts") or {}
    if not isinstance(declared, dict):
        errors.append("counts must be an object")

    seen: set[str] = set()
    for index, item in enumerate(links):
        where = f"links[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        keys = set(item)
        missing = _REQUIRED_ITEM_KEYS - keys
        if missing:
            errors.append(f"{where}: missing {sorted(missing)}")
        unknown = keys - _REQUIRED_ITEM_KEYS - _OPTIONAL_ITEM_KEYS
        if unknown:
            errors.append(f"{where}: unknown fields {sorted(unknown)}")

        ident = item.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9_]+_\d{2}", ident or ""):
            errors.append(f"{where}: bad id {ident!r}")
        elif ident in seen:
            errors.append(f"{where}: duplicate id {ident!r}")
        else:
            seen.add(ident)

        category = item.get("category")
        if category not in CATEGORIES:
            errors.append(f"{where}: unknown category {category!r}")
        elif isinstance(ident, str) and not ident.startswith(f"{category}_"):
            errors.append(f"{where}: id {ident!r} does not start with {category}_")

        route = item.get("expected_route")
        if route not in ROUTES:
            errors.append(f"{where}: expected_route must be one of {list(ROUTES)}, got {route!r}")

        url = item.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://[^/\s]+", url or ""):
            errors.append(f"{where}: url must be absolute http(s), got {url!r}")

        why = item.get("why")
        if not isinstance(why, str) or len(why.strip()) < 10:
            errors.append(f"{where}: why must be a sentence of at least 10 chars")

        target = item.get("expect_first_evidence_seconds")
        if not isinstance(target, dict):
            errors.append(f"{where}: expect_first_evidence_seconds must be an object")
        else:
            if target.get("kind") != "target":
                errors.append(f"{where}: expect_first_evidence_seconds.kind must be 'target'")
            value = target.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                errors.append(f"{where}: expect_first_evidence_seconds.value must be a positive number")
            elif route in SECONDS_CEILING and value > SECONDS_CEILING[route]:
                errors.append(
                    f"{where}: target {value}s exceeds the {route} ceiling {SECONDS_CEILING[route]}s"
                )
            if set(target) - {"value", "kind"}:
                errors.append(f"{where}: unexpected keys in expect_first_evidence_seconds")

        capability = item.get("capability")
        if capability is not None and (not isinstance(capability, str) or not capability.strip()):
            errors.append(f"{where}: capability must be a non-empty string when present")

    counts: dict[str, int] = {}
    for item in links:
        if isinstance(item, dict) and item.get("category") in CATEGORIES:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
    for category in CATEGORIES:
        if counts.get(category, 0) != PER_CATEGORY:
            errors.append(f"category {category}: expected {PER_CATEGORY} links, found {counts.get(category, 0)}")
        if isinstance(declared, dict) and declared.get(category, PER_CATEGORY) != counts.get(category, 0):
            errors.append(
                f"counts.{category}={declared.get(category)!r} disagrees with the links list ({counts.get(category, 0)})"
            )
    if len(links) != TOTAL_LINKS:
        errors.append(f"expected {TOTAL_LINKS} links, found {len(links)}")
    return errors, links


# --- reporting -----------------------------------------------------------


def evaluate(links: list[dict], rules: Ruleset) -> tuple[list[dict], list[dict]]:
    records, mismatches = [], []
    for item in links:
        route, rule_name, status = classify(item["url"], rules)
        record = {
            "id": item["id"],
            "category": item["category"],
            "url": item["url"],
            "expected_route": item["expected_route"],
            "predicted_route": route,
            "rule_source": rule_name,
            "rule_status": status,
            "expect_first_evidence_seconds": item["expect_first_evidence_seconds"]["value"],
            "target_kind": item["expect_first_evidence_seconds"]["kind"],
        }
        if item.get("capability"):
            record["capability"] = item["capability"]
        records.append(record)
        if route != item["expected_route"]:
            mismatches.append(
                {
                    "id": item["id"],
                    "url": item["url"],
                    "expected_route": item["expected_route"],
                    "predicted_route": route,
                    "rule_source": rule_name,
                }
            )
    return records, mismatches


def summarise(payload: dict, links: list[dict], records: list[dict], mismatches: list[dict],
              rules: Ruleset, schema_errors: list[str]) -> dict:
    by_category: dict[str, dict] = {}
    for category in CATEGORIES:
        subset = [r for r in records if r["category"] == category]
        expected = sorted({r["expected_route"] for r in subset})
        predicted = sorted({r["predicted_route"] for r in subset})
        by_category[category] = {
            "count": len(subset),
            "expected_route": expected[0] if len(expected) == 1 else expected,
            "predicted_routes": predicted,
            "consistent": expected == predicted,
            "rules_used": sorted({r["rule_source"] for r in subset}),
            "unavailable_fallback": sum(1 for r in subset if r["rule_status"] == "unavailable_fallback"),
            "capabilities": sorted({r["capability"] for r in subset if r.get("capability")}),
        }

    expected_distribution, predicted_distribution = {}, {}
    for record in records:
        expected_distribution[record["expected_route"]] = expected_distribution.get(record["expected_route"], 0) + 1
        predicted_distribution[record["predicted_route"]] = predicted_distribution.get(record["predicted_route"], 0) + 1

    rule_rows = [
        {
            "rule_source": rule.name,
            "declared_in": rule.source,
            "status": rule.status,
            "hits": rule.hits,
            "detail": rule.detail,
        }
        for rule in rules.rules.values()
    ]
    unavailable = [row for row in rule_rows if row["status"] == "unavailable"]

    return {
        "mode": "dry-run",
        "network": "not used",
        "fixture": {
            "path": "tests/fixtures/routing/links.json",
            "schema_version": payload.get("schema_version"),
            "description": payload.get("description"),
        },
        "totals": {
            "links": len(links),
            "expected_links": TOTAL_LINKS,
            "categories": len(CATEGORIES),
            "per_category": PER_CATEGORY,
            "schema_errors": len(schema_errors),
            "route_mismatches": len(mismatches),
        },
        "by_category": by_category,
        "route_distribution": {
            "expected": expected_distribution,
            "predicted": predicted_distribution,
            "identical": expected_distribution == predicted_distribution,
        },
        "rules": rule_rows,
        "unavailable": unavailable,
        "skill_table": rules.skill_table,
        "mismatches": mismatches,
        "targets": {
            "fields": ["expect_first_evidence_seconds"],
            "kind": "target",
            "note": "Every expect_first_evidence_seconds value is a routing target, not a measurement. No run has produced a first-evidence time for these links.",
        },
        "live": {
            "enabled": False,
            "status": "disabled",
            "plan": LIVE_PLAN,
            "note": "Pass --live only after reading the TODO block in this script. Live mode is a stub and performs no network I/O.",
        },
        "schema_errors": schema_errors,
    }


def print_human(records: list[dict], summary: dict) -> None:
    width = max(len(r["id"]) for r in records) if records else 10
    print(f"{'id'.ljust(width)}  {'expected':11} {'predicted':11} {'rule_status':20} rule_source")
    for record in records:
        flag = "  " if record["expected_route"] == record["predicted_route"] else "!!"
        print(
            f"{flag}{record['id'].ljust(width)}  {record['expected_route']:11} "
            f"{record['predicted_route']:11} {record['rule_status']:20} {record['rule_source']}  [{record['expect_first_evidence_seconds']}s target]"
        )
    total = summary["totals"]
    print()
    print(f"links={total['links']} categories={total['categories']} schema_errors={total['schema_errors']} "
          f"route_mismatches={total['route_mismatches']}")
    for category, row in summary["by_category"].items():
        print(f"  {category:16} {row['count']:>2}  mode={row['expected_route']}  consistent={row['consistent']}  "
              f"rules={','.join(row['rules_used'])}")
    for row in summary["unavailable"]:
        print(f"  unavailable: {row['rule_source']} ({row['declared_in']}) - {row['detail']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route-correctness evaluation (offline by default).")
    parser.add_argument("--dry-run", action="store_true", help="validate the fixture and routing rules only (default)")
    parser.add_argument("--live", action="store_true", help="stub: refuses to run and performs no network I/O")
    parser.add_argument("--json-only", action="store_true", help="print only the JSON summary")
    parser.add_argument("--fixture", type=Path, default=FIXTURE, help="override the fixture path")
    args = parser.parse_args(argv)

    if args.live:
        plan = {"mode": "live", "status": "not_implemented", "network": "not used",
                "reason": "live requests are not implemented; see the TODO block in scripts/acceptance/route_eval.py",
                "plan": LIVE_PLAN}
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 2

    try:
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"mode": "dry-run", "status": "unreadable_fixture",
                          "fixture": str(args.fixture), "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False, indent=2))
        return 1

    schema_errors, links = validate(payload)
    rules = load_rules()
    records: list[dict] = []
    mismatches: list[dict] = []
    if not schema_errors:
        records, mismatches = evaluate(links, rules)

    summary = summarise(payload, links, records, mismatches, rules, schema_errors)
    summary["status"] = "ok" if not schema_errors and not mismatches and len(links) == TOTAL_LINKS else "failed"

    if not args.json_only:
        print_human(records, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
