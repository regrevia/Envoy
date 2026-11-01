"""Offline checks for the GitHub source reader.

Nothing here touches the network: every API call is a fake fed to the two
module-level HTTP functions the reader funnels through, so the tests pin the
request *count* (the whole point of the module), the parsed fields, and the
honest-failure paths.

The reader must stay cheap: a repository root is 3 requests, a tree 2, a file
with an explicit commit SHA 1, a commit 1, a PR 3, an issue 2. A regression in
any of those numbers is a regression in latency, so they are asserted.

Listing a directory is one level by default and costs at most 2 requests
(``commits`` for the SHA + one listing call). The whole-repository recursive
tree is expensive for GitHub to build (cpython: 78 s / 1.5 MB, measured), so it
is only requested when the goal says the caller wants the whole tree or the
explicit environment flag is set; the pinned tests keep that opt-in honest.
"""

from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
for _path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest

from envoy_cli import github_source
from envoy_core.schemas.read_v1 import ErrorCode, ReadRequest, ReadResult, Scope, SourceRef

BLOB_SHA = "a" * 40
COMMIT_SHA = "b" * 40
HEAD_SHA = "c" * 40


def _request(url: str, *, locator: str | None = None, visual: bool = False) -> ReadRequest:
    return ReadRequest(
        request_id="req-1",
        source=SourceRef(url=url),
        scope=Scope(locator=locator, include_visual=visual),
    )


def _read(url: str, *, locator: str | None = None) -> "object":
    response = github_source.read_github_source(_request(url, locator=locator))
    assert response is not None, f"{url} should be owned by the GitHub reader"
    return response


def _text(response: object) -> str:
    return "\n".join(block.content.text for block in response.blocks)


class _Api:
    """Fake GitHub API: routes on URL substring, records calls."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str | None]] = []

    def _match(self, path: str) -> object:
        # Longest needle wins: "/repos/o/r" must not shadow "/repos/o/r/commits/main".
        for needle, value in sorted(self.routes.items(), key=lambda item: len(item[0]), reverse=True):
            if needle in path:
                if isinstance(value, Exception):
                    raise value
                return value
        raise github_source.GithubNotFound(f"no fake route for {path}", status=404)

    def json(self, path: str, *, token: str | None = None, timeout: float = 0) -> dict:
        self.calls.append((path, token))
        return self._match(path)

    def raw(self, url: str, *, token: str | None = None, timeout: float = 0, max_bytes: int = 0):
        self.calls.append((url, token))
        return self._match(url)


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    """A developer's real token must not leak into the offline fakes."""

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.fixture()
def api(monkeypatch):
    def install(routes: dict[str, object]) -> _Api:
        fake = _Api(routes)
        monkeypatch.setattr(github_source, "_github_json", fake.json)
        monkeypatch.setattr(github_source, "_github_raw", fake.raw)
        return fake

    return install


# ---------------------------------------------------------------------------
# Repository root and trees
# ---------------------------------------------------------------------------


def test_repo_root_reads_metadata_and_tree_in_three_requests(api):
    fake = api({
        "/repos/acme/widget": {
            "default_branch": "main",
            "description": "A widget",
            "language": "Python",
            "license": {"spdx_id": "MIT"},
            "size": 42,
            "stargazers_count": 7,
            "forks_count": 1,
            "open_issues_count": 2,
            "pushed_at": "2024-01-01T00:00:00Z",
        },
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}": {
            "truncated": False,
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "README.md", "type": "blob", "size": 120},
                {"path": "pyproject.toml", "type": "blob", "size": 300},
            ],
        },
    })

    response = _read("https://github.com/acme/widget")

    assert response.result is ReadResult.READY
    assert response.execution_state.name == "COMPLETED"
    details = response.coverage.modalities["code"].details
    assert details["reader"] == "github"
    assert details["github_kind"] == "repo"
    assert details["default_branch"] == "main"
    assert details["commit_sha"] == COMMIT_SHA
    assert details["entries_shown"] == 3
    assert details["dirs_shown"] == 1
    assert details["files_shown"] == 2
    assert details["request_count"] == 3
    assert len(fake.calls) == 3
    body = _text(response)
    assert "acme/widget" in body
    assert "[dir] src/" in body
    assert "README.md (120 B)" in body
    # Every block carries a locator a caller can quote back.
    for block in response.blocks:
        assert block.locator.source_url == "https://github.com/acme/widget"
        assert block.locator.quote


def test_subdirectory_listing_is_one_level_and_two_requests(api):
    fake = api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/contents/packages/envoy-cli?ref={COMMIT_SHA}": [
            {"name": "reader.py", "type": "file", "size": 10, "sha": "a" * 40},
            {"name": "tests", "type": "dir", "sha": "b" * 40},
        ],
    })

    response = _read("https://github.com/acme/widget/tree/main/packages/envoy-cli")

    details = response.coverage.modalities["code"].details
    assert details["github_kind"] == "tree"
    assert details["path"] == "packages/envoy-cli"
    assert details["plan"] == "one_level"
    assert details["mode"] == "one_level"
    assert details["listing_source"] == "contents_api"
    assert details["entries_total"] == 2  # direct children only, not the repo
    assert details["entries_shown"] == 2
    assert details["request_count"] == 2
    assert len(fake.calls) == 2
    # The slow whole-repository tree is never requested for a subdirectory.
    assert not any("recursive=1" in path for path, _ in fake.calls)
    body = _text(response)
    assert "packages/envoy-cli/reader.py" in body
    assert "packages/envoy-cli/tests/" in body
    assert "README.md" not in body


def test_one_level_listing_names_readable_subdirectory_urls(api):
    api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        "/repos/acme/widget": {"default_branch": "main", "size": 4},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}": {
            "truncated": False,
            "tree": [
                {"path": "packages", "type": "tree"},
                {"path": "README.md", "type": "blob", "size": 5},
            ],
        },
    })

    response = _read("https://github.com/acme/widget")

    details = response.coverage.modalities["code"].details
    assert details["plan"] == "one_level"
    assert details["listing_source"] == "git_trees"
    assert details["subdirectory_urls"] == {
        "packages": "https://github.com/acme/widget/tree/main/packages",
    }
    body = _text(response)
    # A directory line is directly re-readable, so the caller can walk the tree
    # one cheap request at a time instead of asking for the whole repository.
    assert "https://github.com/acme/widget/tree/main/packages" in body


def test_huge_tree_is_capped_and_says_so(api):
    entries = [{"path": f"src/file_{index}.py", "type": "blob", "size": index} for index in range(2100)]
    api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}": {"truncated": False, "tree": entries},
    })

    response = _read("https://github.com/acme/widget/tree/main")

    details = response.coverage.modalities["code"].details
    assert details["entries_shown"] == github_source.MAX_ONE_LEVEL_ENTRIES
    assert details["entries_total"] == 2100
    assert details["entries_capped_by_reader"] is True
    assert response.result is ReadResult.PARTIAL
    assert any("capped at" in note for note in response.coverage.unresolved)


def test_goal_asking_for_the_whole_tree_switches_to_recursive(api):
    fake = api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}?recursive=1": {
            "truncated": False,
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "src/a.py", "type": "blob", "size": 3},
                {"path": "src/deep/b.py", "type": "blob", "size": 4},
            ],
        },
    })

    request = ReadRequest(
        request_id="req-1",
        source=SourceRef(url="https://github.com/acme/widget/tree/main/src"),
        goal="list every file in this repository",
    )
    response = github_source.read_github_source(request)
    assert response is not None

    details = response.coverage.modalities["code"].details
    assert details["plan"] == "recursive"
    assert details["mode"] == "recursive"
    assert details["plan_reason"].startswith("goal_keyword")
    assert details["listing_source"] == "git_trees?recursive=1"
    assert details["entries_total"] == 3
    # Recursion is opt-in only: exactly one whole-tree request, no fallback.
    assert sum(1 for path, _ in fake.calls if "recursive=1" in path) == 1
    body = _text(response)
    assert "src/deep/b.py" in body


def test_env_flag_is_the_explicit_whole_tree_escape_hatch(api, monkeypatch):
    api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}?recursive=1": {
            "truncated": False,
            "tree": [
                {"path": "src/a.py", "type": "blob", "size": 3},
                {"path": "src/deep/b.py", "type": "blob", "size": 4},
            ],
        },
    })
    monkeypatch.setenv("ENVOY_GITHUB_RECURSIVE_TREE", "1")

    response = _read("https://github.com/acme/widget/tree/main/src")

    details = response.coverage.modalities["code"].details
    assert details["plan"] == "recursive"
    assert details["plan_reason"].startswith("explicit_env_flag")
    assert "src/deep/b.py" in _text(response)


def test_recursive_tree_timeout_degrades_to_one_level(api):
    fake = api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}?recursive=1": github_source.GithubUnavailable(
            "GitHub request failed (TimeoutError)", url="/git/trees"
        ),
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}": {
            "truncated": False,
            "tree": [
                {"path": "src", "type": "tree"},
                {"path": "README.md", "type": "blob", "size": 5},
            ],
        },
    })

    request = ReadRequest(
        request_id="req-1",
        source=SourceRef(url="https://github.com/acme/widget/tree/main"),
        goal="read the entire repository tree",
    )
    response = github_source.read_github_source(request)
    assert response is not None

    details = response.coverage.modalities["code"].details
    # The caller is never left waiting for the whole-tree answer.
    assert details["plan"] == "one_level"
    assert details["plan_downgraded_from"] == "recursive"
    assert details["plan_downgrade_reason"] == "recursive_tree_timeout"
    assert details["entries_shown"] == 2
    assert any(
        "recursive_tree_timeout" in note for note in response.coverage.unresolved
    )
    # The recursive attempt happened once and was then abandoned, not retried.
    assert sum(1 for path, _ in fake.calls if "recursive=1" in path) == 1
    body = _text(response)
    assert "src/" in body
    assert response.coverage.modalities["code"].extraction == "partial"


def test_recursive_entries_are_capped_by_the_reader(api):
    entries = [
        {"path": f"pkg/file_{index}.py", "type": "blob", "size": index}
        for index in range(1500)
    ]
    api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        "/repos/acme/widget": {"default_branch": "main", "size": 4},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}?recursive=1": {
            "truncated": False,
            "tree": entries,
        },
    })

    request = ReadRequest(
        request_id="req-1",
        source=SourceRef(url="https://github.com/acme/widget"),
        scope=Scope(locator=None),
        goal="recursively list all files",
    )
    response = github_source.read_github_source(request)
    assert response is not None

    details = response.coverage.modalities["code"].details
    assert details["plan"] == "recursive"
    assert details["entries_total"] == 1500
    assert details["entries_shown"] == github_source.MAX_RECURSIVE_ENTRIES
    assert details["entries_capped_by_reader"] is True
    assert response.result is ReadResult.PARTIAL


def test_truncated_recursive_tree_is_reported_honestly(api):
    api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        "/repos/acme/widget": {"default_branch": "main", "size": 4},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}?recursive=1": {
            "truncated": True,
            "tree": [{"path": "a.txt", "type": "blob", "size": 1}],
        },
    })

    request = ReadRequest(
        request_id="req-1",
        source=SourceRef(url="https://github.com/acme/widget"),
        goal="list all files in the repository",
    )
    response = github_source.read_github_source(request)
    assert response is not None

    details = response.coverage.modalities["code"].details
    assert details["plan"] == "recursive"
    assert details["tree_truncated"] is True
    assert details["entries_total"] is None
    assert details["entries_total_lower_bound"] == 1
    assert any("truncated the recursive tree" in note for note in response.coverage.unresolved)


def test_subdirectory_listing_never_touches_the_tree_endpoint(api):
    fake = api({
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/contents/scripts?ref={COMMIT_SHA}": [
            {"name": "build.sh", "type": "file", "size": 9, "sha": "d" * 40},
            {"name": "lib", "type": "dir", "sha": "e" * 40},
        ],
    })

    response = _read("https://github.com/acme/widget/tree/main/scripts")

    details = response.coverage.modalities["code"].details
    assert details["listing_source"] == "contents_api"
    assert details["entries_shown"] == 2
    assert details["request_count"] == 2
    assert len(fake.calls) == 2
    # Depth does not add requests: the same git tree of a deep repository would
    # cost tens of seconds and megabytes, and is never fetched for a listing.
    assert not any("git/trees" in path for path, _ in fake.calls)
    body = _text(response)
    assert "scripts/build.sh" in body
    assert "scripts/lib/" in body
    assert "https://github.com/acme/widget/tree/main/scripts/lib" in body


# ---------------------------------------------------------------------------
# File with a line range
# ---------------------------------------------------------------------------


def _file_payload(text: str) -> dict:
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(raw),
        "sha": "f" * 40,
        "content": base64.b64encode(raw).decode("ascii"),
    }


def test_blob_line_range_from_url_fragment(api):
    file_text = "line1\nline2\nline3\nline4\nline5\n"
    api({
        "/repos/acme/widget/commits/main": {"sha": BLOB_SHA},
        f"/repos/acme/widget/contents/src/app.py?ref={BLOB_SHA}": _file_payload(file_text),
    })

    response = _read("https://github.com/acme/widget/blob/main/src/app.py#L3-L4")

    details = response.coverage.modalities["code"].details
    assert details["github_kind"] == "blob"
    assert details["commit_sha"] == BLOB_SHA
    assert details["line_start"] == 3
    assert details["line_end"] == 4
    assert details["total_lines"] == 5
    assert details["content_hash"] == hashlib.sha256(file_text.encode("utf-8")).hexdigest()
    assert details["request_count"] == 2
    code_block = [block for block in response.blocks if block.kind.value == "code"][0]
    assert code_block.content.text == "line3\nline4"
    assert code_block.content.language == "python"


def test_blob_with_explicit_commit_sha_is_one_request(api):
    fake = api({
        f"/repos/acme/widget/contents/README.md?ref={BLOB_SHA}": _file_payload("hello\n"),
    })

    response = _read(f"https://github.com/acme/widget/blob/{BLOB_SHA}/README.md")

    details = response.coverage.modalities["code"].details
    assert details["request_count"] == 1
    assert len(fake.calls) == 1
    assert details["line_start"] == 1
    assert details["line_end"] == 1
    assert details["content_hash"] == hashlib.sha256(b"hello\n").hexdigest()


def test_blob_line_range_from_scope_locator(api):
    api({
        "/repos/acme/widget/commits/main": {"sha": BLOB_SHA},
        f"/repos/acme/widget/contents/f.txt?ref={BLOB_SHA}": _file_payload("a\nb\nc\n"),
    })

    response = _read("https://github.com/acme/widget/blob/main/f.txt", locator="L2")

    details = response.coverage.modalities["code"].details
    assert (details["line_start"], details["line_end"]) == (2, 2)
    assert [b for b in response.blocks if b.kind.value == "code"][0].content.text == "b"


def test_blob_range_past_end_of_file_is_reported(api):
    api({
        "/repos/acme/widget/commits/main": {"sha": BLOB_SHA},
        f"/repos/acme/widget/contents/f.txt?ref={BLOB_SHA}": _file_payload("a\nb\n"),
    })

    response = _read("https://github.com/acme/widget/blob/main/f.txt#L99-L120")

    details = response.coverage.modalities["code"].details
    assert (details["line_start"], details["line_end"]) == (2, 2)
    assert any("outside the file" in note for note in response.coverage.unresolved)
    assert response.result is ReadResult.PARTIAL


def test_large_file_falls_back_to_raw_and_drops_content_hash(api):
    fake = api({
        "/repos/acme/widget/commits/main": {"sha": BLOB_SHA},
        f"/repos/acme/widget/contents/big.csv?ref={BLOB_SHA}": {"type": "file", "size": 5_000_000},
        f"/{BLOB_SHA}/big.csv": (b"a,b\nc,d\n", True),
    })

    response = _read("https://github.com/acme/widget/blob/main/big.csv")

    details = response.coverage.modalities["code"].details
    assert details["file_source"] == "raw.githubusercontent.com"
    assert details["file_truncated"] is True
    assert details["content_hash"] is None
    assert details["total_lines_is_lower_bound"] is True
    assert any("reader limit" in note for note in response.coverage.unresolved)
    assert fake.calls, "raw fallback should have been attempted"


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_commit_reads_message_stats_and_files_in_one_request(api):
    fake = api({
        f"/repos/acme/widget/commits/{COMMIT_SHA}": {
            "sha": COMMIT_SHA,
            "html_url": f"https://github.com/acme/widget/commit/{COMMIT_SHA}",
            "commit": {
                "message": "Fix the parser\n\nLonger explanation.",
                "author": {"name": "Ada", "email": "ada@example.com", "date": "2024-01-02T03:04:05Z"},
            },
            "stats": {"additions": 10, "deletions": 2, "total": 12},
            "files": [
                {"filename": "src/app.py", "status": "modified", "additions": 8, "deletions": 2,
                 "changes": 10, "patch": "@@ -1 +1 @@\n-old\n+new"},
            ],
        },
    })

    response = _read(f"https://github.com/acme/widget/commit/{COMMIT_SHA}")

    details = response.coverage.modalities["code"].details
    assert details["github_kind"] == "commit"
    assert details["subject"] == "Fix the parser"
    assert details["author"] == "Ada"
    assert details["stats"] == {"additions": 10, "deletions": 2, "total": 12}
    assert details["files"][0]["filename"] == "src/app.py"
    assert details["request_count"] == 1
    assert len(fake.calls) == 1
    body = _text(response)
    assert "src/app.py" in body
    assert "Longer explanation." in body


def test_commit_without_patch_marks_coverage_partial(api):
    api({
        f"/repos/acme/widget/commits/{COMMIT_SHA}": {
            "sha": COMMIT_SHA,
            "commit": {"message": "Binary bump", "author": {"name": "Ada", "date": "2024-01-02T03:04:05Z"}},
            "stats": {"additions": 0, "deletions": 0, "total": 0},
            "files": [{"filename": "logo.png", "status": "modified", "additions": 0, "deletions": 0, "changes": 0}],
        },
    })

    response = _read(f"https://github.com/acme/widget/commit/{COMMIT_SHA}")

    details = response.coverage.modalities["code"].details
    assert details["patch_truncated"] is True
    assert response.result is ReadResult.PARTIAL
    assert any("patch" in note.lower() for note in response.coverage.unresolved)


# ---------------------------------------------------------------------------
# Pull request and issue
# ---------------------------------------------------------------------------


def test_pull_request_reads_body_files_and_comments(api):
    fake = api({
        "/repos/acme/widget/pulls/12": {
            "title": "Add the reader",
            "body": "This adds it.",
            "state": "open",
            "draft": False,
            "merged": False,
            "user": {"login": "ada"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "head": {"sha": HEAD_SHA, "ref": "feature"},
            "base": {"sha": COMMIT_SHA, "ref": "main"},
            "additions": 20,
            "deletions": 1,
            "changed_files": 1,
            "comments": 1,
        },
        "/repos/acme/widget/pulls/12/files": [
            {"filename": "src/app.py", "status": "added", "additions": 20, "deletions": 0,
             "patch": "@@ -0,0 +1 @@\n+new"},
        ],
        "/repos/acme/widget/issues/12/comments": [
            {"user": {"login": "bob"}, "created_at": "2024-01-03T00:00:00Z", "body": "Looks good"},
        ],
    })

    response = _read("https://github.com/acme/widget/pull/12")

    details = response.coverage.modalities["code"].details
    assert details["number"] == 12
    assert details["title"] == "Add the reader"
    assert details["head_sha"] == HEAD_SHA
    assert details["files"][0]["filename"] == "src/app.py"
    assert details["comment_summaries"][0]["author"] == "bob"
    assert details["request_count"] == 3
    assert len(fake.calls) == 3
    assert any("review comments" in note for note in response.coverage.unresolved)
    body = _text(response)
    assert "Add the reader" in body
    assert "Looks good" in body


def test_issue_reads_body_labels_and_first_comments(api):
    fake = api({
        "/repos/acme/widget/issues/7": {
            "title": "Crash on empty input",
            "body": "It crashes.",
            "state": "open",
            "user": {"login": "ada"},
            "labels": [{"name": "bug"}],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "comments": 2,
        },
        "/repos/acme/widget/issues/7/comments": [
            {"user": {"login": "bob"}, "created_at": "2024-01-03T00:00:00Z", "body": "Reproduced"},
            {"user": {"login": "ada"}, "created_at": "2024-01-04T00:00:00Z", "body": "Fixed"},
        ],
    })

    response = _read("https://github.com/acme/widget/issues/7")

    details = response.coverage.modalities["text"].details
    assert details["title"] == "Crash on empty input"
    assert details["labels"] == ["bug"]
    assert [item["author"] for item in details["comment_summaries"]] == ["bob", "ada"]
    assert details["request_count"] == 2
    assert len(fake.calls) == 2
    assert _text(response).count("Reproduced") == 1


# ---------------------------------------------------------------------------
# Errors: report, never escalate to a browser
# ---------------------------------------------------------------------------


def test_rate_limit_is_a_retryable_block_with_reset_time(api):
    api({"/repos/acme/widget": github_source.GithubRateLimited(
        "API rate limit exceeded", status=403, reset_at="2024-01-01T00:10:00Z", reset_epoch=1704075000,
    )})

    response = _read("https://github.com/acme/widget")

    assert response.result is ReadResult.BLOCKED
    error = response.errors[0]
    assert error.code is ErrorCode.NETWORK_ERROR
    assert error.retryable is True
    assert error.details["github_error_code"] == "github_rate_limited"
    assert error.details["reset_at"] == "2024-01-01T00:10:00Z"
    assert "GITHUB_TOKEN" in error.details["hint"]


def test_missing_repository_is_source_unavailable(api):
    api({"/repos/acme/widget": github_source.GithubNotFound("Not Found", status=404)})

    response = _read("https://github.com/acme/widget")

    assert response.result is ReadResult.BLOCKED
    assert response.errors[0].code is ErrorCode.SOURCE_UNAVAILABLE
    assert response.errors[0].details["github_error_code"] == "github_not_found"


def test_rejected_token_requires_action(api):
    api({"/repos/acme/widget": github_source.GithubUnauthorized("Bad credentials", status=401)})

    response = _read("https://github.com/acme/widget")

    assert response.result is ReadResult.BLOCKED
    assert response.errors[0].code is ErrorCode.AUTH_REQUIRED
    assert response.errors[0].action_required is True


def test_unreachable_api_is_retryable_and_does_not_fall_back(api):
    api({"/repos/acme/widget": github_source.GithubUnavailable("timed out")})

    response = _read("https://github.com/acme/widget")

    assert response.result is ReadResult.BLOCKED
    assert response.errors[0].code is ErrorCode.NETWORK_ERROR
    assert response.errors[0].retryable is True


# ---------------------------------------------------------------------------
# Auth, fallbacks and URL shapes
# ---------------------------------------------------------------------------


def test_token_is_sent_as_bearer(api, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    fake = api({
        "/repos/acme/widget": {"default_branch": "main"},
        "/repos/acme/widget/commits/main": {"sha": COMMIT_SHA},
        f"/repos/acme/widget/git/trees/{COMMIT_SHA}": {"truncated": False, "tree": []},
    })

    response = _read("https://github.com/acme/widget")

    assert fake.calls[0][1] == "secret-token"
    assert response.coverage.modalities["code"].details["authenticated"] is True


def test_raw_url_is_one_request_and_does_not_invent_a_commit(api):
    fake = api({"raw.githubusercontent.com": (b"hello\nworld\n", False)})

    response = _read("https://raw.githubusercontent.com/acme/widget/main/a.txt")

    details = response.coverage.modalities["code"].details
    assert details["github_kind"] == "raw"
    assert details["commit_sha"] is None
    assert details["total_lines"] == 2
    assert details["content_hash"] == hashlib.sha256(b"hello\nworld\n").hexdigest()
    assert details["request_count"] == 1
    assert len(fake.calls) == 1
    assert any("branch or tag" in note for note in response.coverage.unresolved)


def test_gist_returns_basic_metadata_only(api):
    api({"/gists/abc123": {
        "description": "Notes",
        "public": True,
        "owner": {"login": "ada"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "files": {"a.py": {"size": 10, "language": "Python", "truncated": False}},
    }})

    response = _read("https://gist.github.com/ada/abc123")

    details = response.coverage.modalities["text"].details
    assert details["gist_id"] == "abc123"
    assert details["files"]["a.py"]["size"] == 10
    assert response.result is ReadResult.PARTIAL
    assert any("Gist file contents" in note for note in response.coverage.unresolved)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/acme/widget",
        "https://github.com/settings/profile",
        "https://github.com/explore",
        "https://github.com/acme/widget/wiki",
        "https://github.com/acme/widget/releases",
        "https://raw.githubusercontent.com/acme/widget/main",  # no file path
        "https://gist.github.com/ada/abc123/raw/a.py",
    ],
)
def test_urls_outside_the_reader_fall_back(url):
    assert github_source.read_github_source(_request(url)) is None


def test_reader_dispatches_github_urls_to_the_github_reader(monkeypatch):
    """The reader hook must return the GitHub response instead of booting a browser."""

    from envoy_cli import reader as reader_module

    sentinel = object()
    monkeypatch.setattr(reader_module, "_is_public_http_url", lambda *args, **kwargs: True)
    monkeypatch.setattr(reader_module, "is_collection_url", lambda url: False)
    monkeypatch.setattr(github_source, "read_github_source", lambda request, source_url=None: sentinel)

    assert reader_module._read_public_page_once(_request("https://github.com/acme/widget")) is sentinel
