# GitHub reader acceptance — 2026-11-01

Acceptance plan for the GitHub source reader
([`packages/envoy-cli/src/envoy_cli/github_source.py`]($HOME/Desktop/2026/Envoy/packages/envoy-cli/src/envoy_cli/github_source.py)),
the piece that lets a repository URL return a file, a tree, a diff, or a commit
SHA without starting a browser.

**Status of every scenario in this document: `not_run`.** The module landed at
this revision while the routing evaluation was in progress; nothing below has
been executed against a live repository. The commands are written to be run
as-is by whoever takes the first run, and each scenario names the fields that
must be checked so the result is pass/fail, not a judgement call.

The scenario list and the "not run" list at the end are the complete set — there
is no ninth scenario hiding elsewhere in this file.

## Evidence levels

Used throughout, matching the existing acceptance convention:

| Level | Meaning |
| --- | --- |
| `verified` | the command was run, and the named fields matched |
| `unit_checked` | covered by an offline test with a fixture or a mocked HTTP layer, not by a live call |
| `not_run` | no evidence yet |
| `unknown` | ran, but the output does not settle the question |

A run may not fill in a target value or a latency it did not observe. Targets,
where quoted, come from the routing fixture and stay labelled `target`.

## Prerequisites

```bash
cd $HOME/Desktop/2026/Envoy

# the venv that has envoy-cli installed, and gh for the cross-check
packages/envoy-cli/.venv/bin/python -c "import sys; print(sys.version)"
gh auth status
```

Every Python snippet below starts from the same two lines; they are repeated so
each block runs on its own:

```python
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
```

Record in the result: commit SHA of `HEAD`, gh version, whether `GH_TOKEN` was
set, and the wall-clock time. Rate-limit behaviour depends on the token, and a
result without that context cannot be reproduced.

## S1 — repository tree is complete or says it was cut

Question: does a repo-root request return the whole tree, and if the API
truncates it, does the response say so instead of looking complete?

```bash
gh api repos/pallets/flask/git/trees/main?recursive=1 --jq '.truncated'
```

```python
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli.github_source import parse_github_url, read_github_source
from envoy_core.schemas.read_v1 import ReadRequest

url = "https://github.com/pallets/flask"
request = ReadRequest(source={"url": url}, goal="list the repository tree")
response = read_github_source(request, source_url=url)
assert response is not None
details = response.coverage.modalities["text"].details
print(response.result, details.get("tree_entries"), response.coverage.unresolved)
```

| Check | Expected |
| --- | --- |
| `response.result` | `ReadResult.COMPLETE`, or `PARTIAL` when `gh` reports `truncated: true` |
| tree entry count | consistent with the `gh` listing, or capped at `MAX_TREE_ENTRIES` with an entry in `coverage.unresolved` |
| truncation | `coverage.unresolved` names the truncation; a truncated tree is never reported as complete |

Status: `not_run`.

## S2 — subdirectory navigation

Question: does `/tree/<ref>/<dir>` list that directory, and only that directory?

```bash
gh api repos/pallets/flask/contents/src/flask --jq '[.[].name]'
```

```python
url = "https://github.com/pallets/flask/tree/main/src/flask"
request = ReadRequest(source={"url": url}, goal="list this directory")
response = read_github_source(request, source_url=url)
print(response.coverage.modalities["text"].details)
```

| Check | Expected |
| --- | --- |
| entries | the same names `gh api .../contents/` returns, as files and directories |
| scope | no recursion into subdirectories; this is a listing, not a tree |
| path echo | `coverage.modalities["text"].details` names the path that was listed |

Status: `not_run`.

## S3 — file content and line range

Question: does `/blob/<ref>/<path>#L10-L20` return exactly lines 10–20, not the
whole file and not the rendered page?

```bash
gh api repos/pallets/flask/contents/src/flask/app.py \
  --jq '.content' | base64 -d | sed -n '10,20p'
```

```python
url = "https://github.com/pallets/flask/blob/main/src/flask/app.py#L10-L20"
link = parse_github_url(url)
assert link is not None and (link.line_start, link.line_end) == (10, 20)

request = ReadRequest(source={"url": url}, goal="read this line range")
response = read_github_source(request, source_url=url)
block = response.blocks[0].text
print(block[:200], len(block.splitlines()))
```

| Check | Expected |
| --- | --- |
| `link.line_start`, `link.line_end` | `10`, `20` parsed from the fragment |
| block text | starts at line 10 and ends at line 20; line count 11 at most |
| no range in URL | whole file, capped at `MAX_FILE_BYTES`, with a cap noted in `coverage.unresolved` |
| large file | binary or oversized content is refused with a stated reason, not silently truncated to nothing |

Status: `not_run`.

## S4 — commit SHA binding

Question: does a commit URL return the SHA it names, so a later quote can be
pinned to a revision?

```bash
gh api repos/pallets/flask/commits/main --jq '.sha'
```

```python
url = "https://github.com/pallets/flask/commit/<full-sha>"
request = ReadRequest(source={"url": url}, goal="summarise this commit")
response = read_github_source(request, source_url=url)
print(response.source_revision, response.result)
print(response.coverage.modalities["text"].details.get("sha"))
```

| Check | Expected |
| --- | --- |
| `source_revision` | the full 40-character SHA the URL names |
| short SHA in URL | resolved to a full SHA; the resolution is visible, not hidden |
| patch | present, capped at `MAX_PATCH_CHARS` with the cap stated when it bites |
| wrong SHA | a clear not-found outcome (`GithubNotFound` mapped to an error), never an empty success |

Status: `not_run`.

## S5 — no browser is started

Question: does the GitHub path stay off the browser, so a `blob` URL cannot cost
a Chrome session?

```python
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli.github_source import read_github_source
from envoy_core.schemas.read_v1 import ReadRequest

url = "https://github.com/pallets/flask/blob/main/src/flask/app.py"
request = ReadRequest(source={"url": url}, goal="read the file")
response = read_github_source(request, source_url=url)
assert response is not None, "github_source declined a URL it owns"
assert "envoy_cli.browser" not in sys.modules, "a browser module was imported"
```

| Check | Expected |
| --- | --- |
| response | non-`None`; `None` means the reader declined a URL in its own scope |
| browser import | `envoy_cli.browser` absent from `sys.modules` |
| process | no Chrome child process during the call (`pgrep -f "Google Chrome"` before/after) |

Status: `not_run`.

## S6 — request and time budget

Question: does one link stay inside the routing budget rather than walking an
entire repository?

Budget under test (`LIVE_PLAN` in
[`scripts/acceptance/route_eval.py`]($HOME/Desktop/2026/Envoy/scripts/acceptance/route_eval.py)):
at most 4 requests per link, 20s per link.

```python
import time, urllib.request
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]

calls = []
real = urllib.request.urlopen
def counting(*a, **k):
    calls.append(a[0] if a else k.get("url"))
    return real(*a, **k)
urllib.request.urlopen = counting

from envoy_cli.github_source import read_github_source
from envoy_core.schemas.read_v1 import ReadRequest

url = "https://github.com/pallets/flask/blob/main/src/flask/app.py"
started = time.monotonic()
response = read_github_source(ReadRequest(source={"url": url}, goal="read"), source_url=url)
print("requests", len(calls), "seconds", round(time.monotonic() - started, 3))
```

| Check | Expected |
| --- | --- |
| request count | <= 4 for a file URL; a tree walk may exceed it only if the response marks itself partial |
| elapsed | inside the `gh_git` target of 15s, quoted as a target, not as measured evidence |
| retries | a 403 or 5xx does not become an unbounded retry loop |

Status: `not_run` for the file URL above.

### S6.1 单层目录列举实测（2026-11-02）

S6 保留为目标；这一小节只记录单独跑的一条目录列举，避免把时延目标当成测量值。

对象：`https://github.com/python/cpython/tree/main/Lib`（匿名，无 `GITHUB_TOKEN`）。同一请求连测 5 次：

| 指标 | 实测 |
| --- | --- |
| 端到端耗时 | 7.41 / 7.65 / 9.52 / 9.53 / 13.66 s（抖动区间，不给单点值） |
| `api_requests` / `request_count` | 2（`commits/main` 解析 SHA + `contents/Lib?ref=<sha>`） |
| 条目 | `entries_total = entries_shown = 191`，`files_shown` + `dirs_shown` = 191 |
| 结果 | `ready`，`unresolved = []`，`listing_source = contents_api` |
| 剩余成本 | 两次请求的传输：`commits` 2.23 s、`contents/Lib` 5.89 s / 171 KB；渲染约 4.5 s |

反证（本轮据此改掉默认行为）：同仓库 `git/trees/main?recursive=1` 单次 78.34 s、载荷 1 535 265 字节，
端到端 82.88 s 且返回 `partial`、2912 条被截到上限 2000 条。归因规则：先量载荷再怀疑读取代码。

证据级别：观测数字为 `verified`（真实登录前公开仓库、匿名配额内，5 次共 10 次 `api.github.com` 请求）；
但“目录树列举普遍够快够全”**不成立**——只对一层目录成立，递归全树仍是 20 s 预算、1000 条上限并降级报告。

## S7 — rate limit and authentication

Question: when GitHub returns 403 for rate limiting, or a private repo returns
404 without a token, does the caller get a typed error it can act on?

```python
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli.github_source import (
    GithubNotFound, GithubRateLimited, GithubUnauthorized, read_github_source,
)
from envoy_core.schemas.read_v1 import ReadRequest

url = "https://github.com/pallets/flask"
response = read_github_source(ReadRequest(source={"url": url}, goal="list"), source_url=url)
print(response.errors, response.execution_state)
```

| Check | Expected |
| --- | --- |
| unauthenticated 403 | `GithubRateLimited` (or an `errors` entry naming the rate limit), with the reset time when the API sends one |
| missing private repo | `GithubNotFound`, mapped to an error — not an empty success, not a browser fallback |
| bad token | `GithubUnauthorized`, distinct from not-found |
| exhausted quota | the response says retry-after; it does not retry internally until success |

Status: `not_run`.

## S8 — declining URLs outside the reader's scope

Question: does the reader return `None` for pages it does not own, so the caller
keeps its existing route instead of getting a thin answer labelled complete?

URLs the reader declines at this revision (from its own tests): a non-code host,
`github.com/settings/profile`, `github.com/explore`, `/{owner}/{repo}/wiki`,
`/{owner}/{repo}/releases`, a bare `raw.githubusercontent.com/{owner}/{repo}/{ref}`
with no file path, and a gist raw URL.

```python
import sys
sys.path[:0] = ["packages/envoy-cli/src", "packages/envoy-core/src"]
from envoy_cli.github_source import read_github_source
from envoy_core.schemas.read_v1 import ReadRequest

for url in (
    "https://example.com/acme/widget",
    "https://github.com/settings/profile",
    "https://github.com/pallets/flask/releases",
    "https://raw.githubusercontent.com/pallets/flask/main",
    "https://gist.github.com/ada/abc123/raw/a.py",
):
    got = read_github_source(ReadRequest(source={"url": url}, goal="read"), source_url=url)
    print(url, "->", got)
```

| Check | Expected |
| --- | --- |
| every URL above | `None` |
| repo root, file, tree, commit | a response — the positive side of the same boundary |
| reason | declining is silent by contract; the caller's own route (plain fetch, `gh`) takes over |

Status: `not_run`.

## What would make this document `verified`

For each scenario: the command run once against a fixed `HEAD`, its output
pasted with the date, the token state, and the fields compared line by line
against the "Expected" column. Any scenario whose output does not settle the
question is marked `unknown`, not passed by inference.

A later run should add the routing side — `route_eval.py --live` — once that mode
exists; it is currently a stub that exits `2` and touches no network.

## Not run

Every scenario in this document. Complete list, with the reason:

| Id | Scenario | Status | Reason |
| --- | --- | --- | --- |
| S1 | repository tree complete or marked truncated | `not_run` | module landed during this evaluation; no live call made |
| S2 | subdirectory navigation | `not_run` | same |
| S3 | file content and line range | `not_run` | same |
| S4 | commit SHA binding | `not_run` | same |
| S5 | no browser started | `not_run` | same |
| S6 | request and time budget | `not_run` | same; no latency observed, all budgets quoted are targets |
| S7 | rate limit and authentication | `not_run` | same; needs a token-less and a token-bearing run to be meaningful |
| S8 | declining out-of-scope URLs | `not_run` | same; behaviour read from the module's own tests, not executed here |

Count: 8 scenarios, 8 `not_run`, 0 `verified`, 0 `unit_checked`, 0 `unknown`.

Related, and also not evidence about this reader: the offline suite
`tests/cli/test_github_source.py` exists in the tree at this revision. It was not
executed as part of this acceptance, so no scenario above is marked
`unit_checked` on its strength. Running it is the cheapest first step for whoever
picks this up:

```bash
packages/envoy-cli/.venv/bin/python -m pytest tests/cli/test_github_source.py -q
```
