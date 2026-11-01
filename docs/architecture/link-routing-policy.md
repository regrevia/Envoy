# Link routing policy: right reader, right evidence, right cost

Envoy exists to read material the host cannot read itself. It is not the middle
layer for every URL. This document states which reader owns which URL, why, and
how a change to that ownership is evaluated.

The corpus that checks this policy lives in
[`tests/fixtures/routing/`](/Users/apple/Desktop/2026/Envoy/tests/fixtures/routing/README.md);
the offline checker is
[`scripts/acceptance/route_eval.py`](/Users/apple/Desktop/2026/Envoy/scripts/acceptance/route_eval.py).

## The claim

A route is correct when it satisfies three conditions at once:

1. **Right reader** — that reader can return the evidence this URL actually
   contains (a diff, a transcript, a PDF text layer, a rendered DOM).
2. **Right evidence** — the answer is the part that decides something: a file's
   line range, a commit SHA, a timestamped segment, the section that matters.
3. **Right cost** — no browser process, no retained material, no checkpoint when
   a single bounded fetch would do.

Two of these are cheap to check and one is expensive, which is why "how many
times was Envoy called" is a bad metric: it measures cost only, and it rewards
the failure mode where everything goes through the browser. A run that routes
all 60 links to Envoy can score perfectly on call count and return nothing for
`github.com/*/blob/*`, because a browser renders a page title, not a file.

## Decision tree

Applied in order; the first match wins. Rule ids match the `rule_source` values
printed by the evaluator.

| Rule | Condition | Route | Why this rule is first |
| --- | --- | --- | --- |
| R1 | code host (`github.com`, `gitlab.com`, `bitbucket.org`, `codeberg.org`) and the path is a navigation or diff page: `/commit`, `/commits`, `/blob`, `/tree`, `/raw`, `/pull`, `/pulls`, `/pull-requests`, `/merge_requests`, `/issues`, `/compare`, `/diff` | `gh_git` | the browser returns chrome around the payload; the API returns the payload |
| R2 | code host and a repo shell: `/owner/repo`, `/owner/repo/releases`, `/tags`, `/branches`, `/activity`, `/network`, `/actions`, `/wiki`, `/orgs/<org>/repositories` | `gh_git` | metadata questions are API questions; the rendered page adds nothing |
| R3 | video or audio host, or a `/watch`, `/playlist`, `/video`, `/talks`, `/shorts` path | `envoy_task` | needs a transcript, timestamps, frames — and must stay retained to be re-read |
| R4 | `.pdf` path, or `arxiv.org/pdf/` | `envoy_task` | page windows plus a text layer; a one-shot read cannot page back |
| R5 | long structured text: `w3.org/TR/`, `docs.python.org/3/reference/`, `rfc-editor.org/rfc/rfcNNNN.txt` with NNNN >= 5000 | `envoy_task` | exceeds one fetch budget; needs expand/search on the retained material |
| R6 | client-rendered host: `vercel.com`, `linear.app`, `figma.com`, `notion.so`, `web.telegram.org`, `reddit.com`, `x.com`, `discord.com`, `open.spotify.com`, `canva.com` | `envoy` | a browser is required; retention is not |
| R7 | everything else | `host_fetch` | server-rendered page: one fetch, no session, no checkpoint |

R6 is deliberately `envoy` and not `envoy_task`. The render is unavoidable; the
long task is not. Retention is for material a later turn must come back to, not
for a page whose answer is one paragraph.

R5's RFC threshold is a heuristic, and it is labelled as one: RFC 2606 is three
paragraphs and belongs to `host_fetch`; RFC 9110 is a document and belongs to
`envoy_task`. The number is not a quality claim about the RFC.

## The four routes

| Route | Reader | Evidence it returns | What it must not be used for |
| --- | --- | --- | --- |
| `host_fetch` | caller's plain HTTP fetch | server-rendered HTML and text | anything behind JS, login, or a media container |
| `gh_git` | `gh api`, `git show`, `git ls-remote`; inside Envoy, the GitHub REST reader | diffs, file contents, line ranges, trees, SHAs, PR bodies | rendered page chrome, login-walled views |
| `envoy` | one-shot Envoy read | rendered DOM text, structure, cookies-backed pages | material that must be expanded later |
| `envoy_task` | Envoy long task | pages, ASR, frames, retained material, checkpoints | a short page that one fetch answers |

### The GitHub boundary

GitHub is the case most often routed wrong, so it gets its own boundary.

**Owned by `gh_git`:**

* `/blob/<ref>/<path>` — file content, and a line range when the fragment names one
* `/tree/<ref>` and `/tree/<ref>/<path>` — directory listing
* `/commit/<sha>`, `/commits` — patch, author, parents, commit SHA
* `/pull/<n>`, `/issues/<n>`, `/compare/<a>...<b>` — bodies, review state, changed files

The Envoy-side GitHub reader (`packages/envoy-cli/src/envoy_cli/github_source.py`,
`read_github_source`) covers this same ground through the REST API, and the
reader dispatches to it before any browser starts. That keeps a stray
`blob` URL from costing a browser session; it does not make Envoy the preferred
route. With `gh` or `git` on the machine, the caller's own tooling is cheaper and
should be used first.

**Not routed as `gh_git`:**

* `/owner/repo/releases`, `/tags`, `/branches`, `/wiki`, `/settings/profile`,
  `/explore` — the GitHub reader declines these and the URL falls back to a plain
  fetch of the rendered page. Repo-root and release metadata are still `gh_git`
  in this policy where `gh`/`git` is available, because `gh repo view` and
  `gh release list` answer them exactly; the fallback exists for machines without
  those tools.
* `raw.githubusercontent.com/<owner>/<repo>/<ref>` with no file path — a host,
  not a file; nothing to read.
* `gist.github.com/<owner>/<id>/raw/<file>` — raw bytes; the gist view itself is
  `gh_git`, the raw URL is already the payload.

The boundary in one line: **an API answers "what is in this repository"; a
browser answers "what does this page look like"; a diff question is the first
kind.**

## Counterexamples

Each of these looked reasonable and was wrong.

1. **Sending `/blob/` to Envoy and calling it coverage.** The browser returns the
   page title, sometimes the file name, never the diff or the line range. The read
   succeeded; the answer is empty. Route is `gh_git`.
2. **Sending `docs.python.org/3/reference/*` to a long task.** One fetch returns
   the whole page. The long task adds a browser session, a retention lease, and a
   checkpoint to a two-second read. Route is `host_fetch` unless the page is
   genuinely one of the long ones — the corpus keeps only those.
3. **Treating Envoy call count as success.** A run with 60 Envoy calls can be the
   most expensive possible run. The metric here is per-link route agreement, and
   cost is a property of the route, not of the call total.
4. **Sending a video to the one-shot route.** The first read returns a player
   shell. Transcript and frames need the long task, and the material must stay
   retained for the timestamps to mean anything later.
5. **Sending a short static page to a long task "for safety".** A guarded route
   still costs a session; the escalation ladder is fetch, then one Envoy read,
   then a long task.
6. **Using the 57-link total-coverage rate as the routing score.** That study is
   deprecated. It counts readable links and is maximised by routing everything
   through Envoy, which is exactly the failure this policy forbids.
7. **Reading a repo root through the browser to "get the README".** `gh repo
   view`, or a plain fetch, returns the same body. The browser adds a session and
   still cannot see below the README.
8. **Treating a `tree` URL as a file URL.** `/tree/<ref>/<dir>` is a listing;
   asking for line ranges there returns nothing and reads as a truncated file.

## Evaluation contract

The corpus pins the decision, not the plumbing:

* 6 categories x 10 links, each link declaring one `expected_route`;
* every link carries `why` — the decision must be justifiable from the URL alone;
* `expect_first_evidence_seconds` is a **target**, `kind: "target"`, never a
  measurement;
* the checker prints, per link, the rule that fired (`rule_source`) and whether
  that rule was read from product code or from a labelled fallback
  (`rule_status`: `available` / `unavailable` / `unavailable_fallback`);
* a missing module is reported as `unavailable` and never crashes the run;
* the run uses no network in `--dry-run`; `--live` is a stub that exits `2`.

Exit codes: `0` clean, `1` schema error or route mismatch, `2` live not
implemented.

The dry-run observed on 2026-11-01 (HEAD `ca4669b`, parallel work landing):

* 60 links, 0 schema errors, 0 route mismatches, exit `0`;
* distribution `host_fetch` 10, `gh_git` 20, `envoy` 10, `envoy_task` 20,
  identical for expected and predicted;
* rule hits: `hooks.is_code_host_diff_url` 10, `route_eval.code_host_repo_shell`
  10, `route_eval.browser_required` 10, `route_eval.plain_static` 10,
  `route_eval.video_or_audio` 10, `route_eval.pdf_document` 6,
  `route_eval.long_text_document` 4;
* unavailable: a navigation-page predicate in `hooks.py`. Today the diff
  predicate already covers all ten `github_nav_file` links, so the labelled
  fallback fires zero times — it exists so a future `hooks.py` change degrades
  visibly instead of silently routing those links to a browser.

These numbers are rule-hit counts from a file read, not latency measurements.

## Dependency status

| Dependency | Status at this revision | Consequence |
| --- | --- | --- |
| `hooks.is_code_host_diff_url` | available | owns `github_nav_file` |
| `hooks.inspect_prompt` | available | the only URL interception point; a non-empty `.urls` list is the `gh_git` signal |
| `envoy_cli.github_source` | present, one-level listing measured | `python/cpython/tree/main/Lib`: 2 requests, 191 entries, `ready`, 7.41–13.66 s over five runs |
| `envoy_cli.github_source` | present, presence-checked only | behaviour is `not_run` here |
| SKILL.md `## Decide before you call` | available, 9 rows / 6 route rows | hints are reported, not enforced |

## Changing a route

1. Add or edit the link in `tests/fixtures/routing/links.json`, keeping the
   category at ten.
2. Run the dry-run. A mismatch means either the URL is in the wrong category or
   the decision tree needs a rule; decide which, do not adjust the expectation to
   match the classifier.
3. Update this document's decision tree in the same change, and the SKILL.md
   table if the caller-facing advice moved.
4. Never replace a target with a measurement, and never re-introduce a
   whole-corpus `usable_rate` as the routing score.
