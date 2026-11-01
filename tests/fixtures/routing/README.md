# Routing fixture: which reader gets which URL

This directory holds the route-correctness corpus for the link reader. It answers
one question per link:

> Which reader should this URL go to, and which rule decided that?

It does **not** answer "how many times was Envoy called". Call count is a cost
signal, not a correctness signal: a run that sends every link through Envoy has
perfect coverage and is still wrong, because most of those calls return less than
the host could fetch in one request.

```
tests/fixtures/routing/
  links.json   the corpus: 6 categories x 10 links
  README.md    this file: schema, routes, and what the numbers mean
```

The evaluator is [`scripts/acceptance/route_eval.py`](/Users/apple/Desktop/2026/Envoy/scripts/acceptance/route_eval.py).
The reasoning behind each route is [`docs/architecture/link-routing-policy.md`](/Users/apple/Desktop/2026/Envoy/docs/architecture/link-routing-policy.md).

## Routes

| `expected_route` | Reader | Typical cost | What it cannot give |
| --- | --- | --- | --- |
| `host_fetch` | plain HTTP fetch at the caller | one request, no session | JS-rendered text, video, PDF layout |
| `gh_git` | `gh api` / `git show` / the GitHub REST path | one API request, no browser | rendered page chrome, login-walled views |
| `envoy` | one-shot Envoy read (browser) | a browser session per read | retention, later expand/search |
| `envoy_task` | Envoy long task | browser + retention + checkpoints | a fast answer for a short page |

A route is wrong in both directions: a `blob` page sent to the one-shot browser
route returns a title and never the source, and a one-paragraph page sent to a
long task pays a session and a checkpoint for nothing.

## Fields

Top level:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`; the evaluator rejects anything else |
| `fixture` | stable fixture name, `"routing-links"` |
| `description` | one paragraph, also echoed into the run summary |
| `routes` | human-readable meaning of the four route names |
| `expect_first_evidence_seconds_kind` | `"target"` — restates the per-link `kind` |
| `counts` | per-category totals; the evaluator cross-checks them against `links` |
| `links` | the corpus |

Per link:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | `<category>_<NN>`; must start with its own category and be unique |
| `category` | yes | one of the six below |
| `url` | yes | absolute `http(s)` URL |
| `expected_route` | yes | one of `host_fetch`, `gh_git`, `envoy`, `envoy_task` |
| `why` | yes | the decision in one sentence, at least 10 characters |
| `expect_first_evidence_seconds` | yes | `{"value": N, "kind": "target"}` — a design target |
| `capability` | no | the capability that makes the route work: `git_api`, `browser_render`, `pdf_text_layer`, `asr`, `media_frames`, `collection` |

Unknown fields are a schema error, not a silent pass. Adding a field means
updating `_REQUIRED_ITEM_KEYS` / `_OPTIONAL_ITEM_KEYS` in the evaluator in the
same change.

## Categories

| Category | Links | Route | What it pins |
| --- | --- | --- | --- |
| `simple_static` | 10 | `host_fetch` | server-rendered HTML and text; escalating is a regression |
| `github_repo` | 10 | `gh_git` | repo root, releases, tags, org repo lists |
| `github_nav_file` | 10 | `gh_git` | blob, tree, commit, pull, issues, compare |
| `complex_js` | 10 | `envoy` | SPA shells; no retention needed |
| `long_doc_pdf` | 10 | `envoy_task` | long RFCs, W3C TR, reference docs, PDFs |
| `video` | 10 | `envoy_task` | video and audio; transcript and frames |

## Targets are not measurements

Every `expect_first_evidence_seconds` value is a **target**: the budget the route
should stay inside, set from the route's design, not from a run. No link in this
corpus has a measured first-evidence time. The evaluator keeps the distinction
visible in three places:

* the fixture stores `"kind": "target"`;
* each record in the run summary carries `target_kind`;
* the summary has a `targets` block stating that nothing here was measured.

A value above the per-route ceiling in the evaluator (`SECONDS_CEILING`:
`host_fetch` 10s, `gh_git` 15s, `envoy` 30s, `envoy_task` 180s) is a schema
error: it means the target itself is wrong for the route, not that the link is
slow.

Measured latencies, when they exist, belong in a separate results artifact with
the command, the date, and the revision that produced them. They never get
written back into this fixture.

## Decoupled from the 57-link coverage metric

The 57-link total-coverage study in
[`docs/experiments/long-task-metrics-2026-11-01.md`](/Users/apple/Desktop/2026/Envoy/docs/experiments/long-task-metrics-2026-11-01.md)
is deprecated: it counts links that came back readable, so routing every link
through Envoy scores well and costs the most. This corpus shares no links, no
counts, and no verdict vocabulary with it. A single `usable_rate` number must not
be built from these 60 links either — the metric here is per-link route
agreement, reported per category.

## Running

```bash
# offline: validate the fixture and the routing rules, no network
packages/envoy-cli/.venv/bin/python scripts/acceptance/route_eval.py --dry-run

# machine-readable only
packages/envoy-cli/.venv/bin/python scripts/acceptance/route_eval.py --dry-run --json-only
```

Exit codes: `0` clean, `1` schema error or route mismatch, `2` live mode not
implemented.

`--live` is a stub. It prints the budget plan (`LIVE_PLAN`: at most 12 links,
20s per link, 180s total, at most 4 requests per link) and exits `2` without
touching the network.

## Modules that may be missing

The evaluator reads rules from product code and degrades to a labelled local
fallback instead of failing. Every record carries `rule_source` and
`rule_status`; a rule that could not be read is listed in the summary's
`unavailable` array with a reason.

Observed on 2026-11-01 at HEAD `ca4669b` with parallel work landing:

| Dependency | Status | Effect |
| --- | --- | --- |
| `hooks.is_code_host_diff_url` | available, 10 hits | decides all ten `github_nav_file` links |
| `hooks.inspect_prompt` | available, 10 hits | real product predicate: a non-empty `.urls` list routes the link to `gh_git`; the evaluator calls it instead of mirroring it |
| `envoy_cli.github_source` | available (landed during this evaluation) | presence is checked only; its behaviour is `not_run` here |
| SKILL.md `## Decide before you call` table | available, 9 rows, 6 route rows | reported as `skill_table`; a changed table changes the hints in the summary, not the fixture |

Re-run the dry-run after a parallel change lands: `unavailable` and hit counts
are what move, not the expected routes.

## Adding a link

1. Pick the category from the *evidence* the URL needs, then the route follows.
2. Give `why` a sentence a reader can check against the URL path.
3. Keep the category at exactly 10 links and update `counts`.
4. If the URL needs a credential, a fixed commit SHA, or a live number that
   changes, say so in `why` and confirm the reference before any `--live` run.
   Several SHAs and PR numbers in the corpus are shape samples for that reason.
