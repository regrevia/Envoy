"""Routing discipline: Envoy is not the middle layer for every URL.

These tests pin the wording that keeps the model from calling Envoy on material
it can read itself, and the static-page behaviour that decides whether that call
is even worth making. They are offline and read-only: no network, no browser.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_FILES = (
    REPO / "integrations" / "skills" / "envoy" / "SKILL.md",
    REPO / "integrations" / "claude-code" / "envoy" / "skills" / "envoy" / "SKILL.md",
    REPO / "integrations" / "codex" / "envoy" / "skills" / "envoy" / "SKILL.md",
)


def _frontmatter_description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    frontmatter = text.split("\n---\n", 1)[0]
    lines = frontmatter.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("description:"))
    body = lines[start + 1 :]
    return " ".join(" ".join(body).split())


def test_skill_descriptions_are_identical_and_name_the_do_not_use_cases():
    descriptions = [_frontmatter_description(path) for path in SKILL_FILES]
    assert len(set(descriptions)) == 1, "the three shipped SKILL.md copies drifted"

    description = descriptions[0].lower()
    # Each of these is a case where the host can read the URL itself.
    for phrase in (
        "short static page",
        "source navigation",
        "gh or git",
        "already in this turn's context",
        "edited",
        "plain host fetch",
    ):
        assert phrase in description, phrase


def test_generated_dsh_plugin_copy_matches_the_canonical_skill():
    """The fourth shipped copy is generated, not hand-edited.

    ``integrations/dsh/envoy-dsh/skill/{envoy.md,manifest.json}`` come from
    ``scripts/build_envoy_dsh_plugin.py`` reading the canonical SKILL.md, so a
    hand ``cp`` of the three copies leaves the description the DeepSeek Harness
    actually serves untouched. The ``--check`` run is the guard: it fails when
    the generated files are stale against the canonical skill.
    """

    import json

    canonical = _frontmatter_description(SKILL_FILES[0]).lower()
    manifest = REPO / "integrations" / "dsh" / "envoy-dsh" / "skill" / "manifest.json"
    description = json.loads(manifest.read_text(encoding="utf-8"))["description"].lower()
    for phrase in ("short static page", "gh or git", "plain host fetch"):
        assert phrase in canonical, phrase
        assert phrase in description, f"generated description is stale: {phrase}"

    completed = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_envoy_dsh_plugin.py"), "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO / "packages" / "envoy-cli" / "src")},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_plain_fetch_completion_is_judged_by_the_task_not_a_character_count():
    """Length is not the stop condition; the task's requirement is.

    A body that stops mid-sentence, or that never carries the needed table,
    figure, or passage, is not an answer even at a thousand characters.
    Escalation stays evidence-shaped, and honest uncertainty is the fallback.
    """

    for path in SKILL_FILES:
        body = path.read_text(encoding="utf-8")
        assert "800 characters" not in body, path
        for phrase in (
            "passage or values the task",
            "ends where the requested section ends",
            "mid-sentence",
            "the body is truncated",
            "the table, figure, attachment, or code block the answer needs is absent",
            "off-topic",
            "what may be missing",
            "almost always a shell",
            "application/pdf",
            "enable JavaScript",
            "envoy_replay",
        ):
            assert phrase in body, f"{path}: {phrase}"
        assert "escalate only on the evidence" in body, path


def test_skill_table_routes_github_to_the_api_path_and_names_the_limits():
    text = (REPO / "integrations" / "skills" / "envoy" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    row = next(
        line
        for line in text.splitlines()
        if line.startswith("| GitHub repo, tree, blob, commit, pull, or issue:")
    )
    for phrase in (
        "GitHub API path",
        "no browser",
        "line range",
        "commit SHA",
        "diff and stats",
        "coverage.unresolved",
        "a permission 403 asks for access",
        "a quota 403 gives the reset time",
    ):
        assert phrase in row, phrase
    # Tool selection is one rule, stated once for every host, not per row.
    assert "gh` or `git" not in row, row
    assert text.count("gh` or `git") == 1
    normalized = " ".join(text.split())
    assert "`gh` or `git` comes first when either is at hand" in normalized
    # The page-only route keeps the honest limit for hosts without an API path.
    fallback = next(
        line
        for line in text.splitlines()
        if line.startswith("| GitLab, Bitbucket, or a GitHub page the API path declines")
    )
    assert "never the diff or the tree" in fallback


def test_envoy_read_description_leads_with_routing_and_keeps_needs_host():
    from envoy_cli.mcp import TOOLS

    spec = next(tool for tool in TOOLS if tool["name"] == "envoy_read")
    description = spec["description"]
    assert description.startswith(
        "Route first: read a short static page, a file the host already has"
    )
    for phrase in (
        "host's own fetch",
        "Skip it for material already in this turn's context",
        "A plain fetch is complete when its body carries the passage",
        "Escalate only on evidence",
        "gh or git when they are at hand",
        "goes through the GitHub API with no browser",
        "coverage.unresolved",
    ):
        assert phrase in description, phrase
    # The handoff semantics this paragraph sits in front of must stay intact.
    for kept in (
        "An envoy_read needs_host response is a handoff, not a terminal state.",
        "Request frames with scope.include_visual=true",
        "retention {raw_media:'persistent'}",
    ):
        assert kept in description, kept


def test_static_html_keeps_the_title_but_flattens_headings_and_links():
    """Characterises the static page path so the gap is visible, not guessed.

    `_PageParser` treats h1..h6 as anonymous block breaks (reader.py:332, :352)
    and never reads `<a href>`, so a plain HTML page arrives as one title block
    plus one flat paragraph block. Until that parser emits heading levels and a
    link list, a non-GitHub page cannot serve as its own outline.
    """

    from envoy_cli import reader

    html = (
        "<html><head><title>Release notes</title></head><body>"
        "<h1>Release notes</h1>"
        "<h2>Fixes</h2>"
        "<p>The parser keeps this sentence.</p>"
        '<a href="https://docs.example.com/next">Next steps</a>'
        "</body></html>"
    )
    parser = reader._PageParser()
    parser.feed(html)
    title = reader._clean_text(parser.title_parts)
    text = reader._clean_text(parser.text_parts)

    assert title == "Release notes"
    assert "The parser keeps this sentence." in text
    assert "Next steps" in text
    # Documented gaps: the heading level and the link target are both dropped.
    assert "#" not in text
    assert "https://docs.example.com/next" not in text


def test_a_static_page_does_not_import_or_start_a_browser():
    """The static path must stay on the plain HTTP fetch.

    The two predicates that would send a plain URL to the isolated browser are
    checked directly, and importing the read stack must not pull in the browser
    module or Playwright; both are imported only inside the browser call.
    """

    src = [
        str(REPO / "packages" / "envoy-core" / "src"),
        str(REPO / "packages" / "envoy-cli" / "src"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(src + [env.get("PYTHONPATH", "")]).strip(
        os.pathsep
    )
    probe = (
        "import json, sys\n"
        "from envoy_cli import reader\n"
        "from envoy_cli.platforms import is_platform_page\n"
        "url = 'https://example.com/notes/2026'\n"
        "print(json.dumps({\n"
        " 'platform': is_platform_page(url),\n"
        " 'video': reader._url_is_video_page(url),\n"
        " 'browser': 'envoy_cli.browser' in sys.modules,\n"
        " 'playwright': 'playwright' in sys.modules,\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == (
        '{"platform": false, "video": false, "browser": false, "playwright": false}'
    )


def test_github_pages_stay_routed_because_the_api_path_answers_them():
    """The hook must not filter what the GitHub API path can read.

    `gh`/`git` availability is a property of the shell running the hook, not of
    the URL, so gating on it would make routing machine-dependent. The API path
    returns the tree, a file's bytes, the commit diff, and the PR or issue body
    with comments, so every GitHub page stays routed. Other code hosts keep the
    old rule: their diff still comes back as a rendered title.
    """

    from envoy_cli.hooks import inspect_prompt

    for url in (
        "https://github.com/regrevia/Envoy",
        "https://github.com/regrevia/Envoy/tree/main/src",
        "https://github.com/regrevia/Envoy/blob/main/README.md",
        "https://github.com/regrevia/Envoy/commit/8e6d17ae56e8e6f73385783bde39d8190ff68f85",
        "https://github.com/regrevia/Envoy/pull/42",
        "https://github.com/regrevia/Envoy/issues/12",
    ):
        assert inspect_prompt(url).urls == (url,), url

    assert inspect_prompt("https://gitlab.com/a/b/-/blob/main/x.py").urls == ()
    assert inspect_prompt("https://bitbucket.org/a/b/pull-requests/3").urls == ()


def test_boundary_section_names_both_sides_before_the_table():
    """The skill must state the ✅/❌ boundary, not only the middle table.

    The table answers "which tool"; the boundary answers "whether to call at
    all", which is the decision that costs a wasted fetch when it is wrong.
    """

    text = SKILL_FILES[0].read_text(encoding="utf-8")
    call_at = text.index("\n✅ Call Envoy when")
    skip_at = text.index("\n❌ Do not call Envoy when")
    table_at = text.index("\n| Situation | Do |")
    assert call_at < table_at and skip_at < table_at
    section = text[call_at:table_at]
    for phrase in (
        "transcript, timeline, or\n  frame",
        "PDF, DOCX, PPTX, XLSX, EPUB",
        "empty\n  JavaScript shell",
        "GitHub API path reads it with no browser",
        "must outlive this turn",
        "in this turn's context",
        "shortened, cited, or saved",
        "length is not a completion signal",
        "localhost",
        "test-prompts.json",
    ):
        assert phrase in section, phrase


def test_test_prompts_cover_should_call_decoy_and_sibling_cases():
    """The trigger file is the machine-checkable half of the boundary.

    Each case must name a class, and the file must carry a bait case whose
    answer is a sibling tool rather than Envoy itself — the case a
    "a skill about links" description cannot separate.
    """

    import json

    payload = json.loads(
        (REPO / "integrations" / "skills" / "envoy" / "test-prompts.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema"] == "envoy.test-prompts/1"
    assert payload["skill"] == "envoy"
    cases = payload["cases"]
    assert len(cases) >= 12
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)), "duplicate case id"

    kinds = {case["kind"] for case in cases}
    assert kinds == {"should_call", "decoy", "ambiguous"}, kinds
    for kind in kinds:
        assert sum(case["kind"] == kind for case in cases) >= 3, kind

    # A bait case must route to a sibling, never to envoy_read.
    siblings = {
        case.get("sibling")
        for case in cases
        if case["kind"] == "decoy" and case.get("sibling")
    }
    assert {"envoy_expand", "envoy_search", "host-native-git", "plain-fetch"} <= siblings
    for case in cases:
        if case.get("sibling"):
            assert case["expect"] != "envoy_read", case["id"]
        assert case["prompt"].strip(), case["id"]
        assert case["reason"].strip(), case["id"]

    for case in cases:
        if case["kind"] != "should_call":
            continue
        assert case["expect"] == "envoy_read", case["id"]
        assert case["routing"] == "route", case["id"]


def test_test_prompts_routing_field_matches_the_hook():
    """Every declared `routing` value must be what `inspect_prompt` returns.

    Without this the file drifts into prose that no longer describes the code,
    which is exactly how the GitHub links stayed silently filtered while the
    skill table promised the API path.
    """

    import json

    from envoy_cli.hooks import extract_public_urls, inspect_prompt

    payload = json.loads(
        (REPO / "integrations" / "skills" / "envoy" / "test-prompts.json").read_text(
            encoding="utf-8"
        )
    )
    checked = 0
    for case in payload["cases"]:
        expected = case.get("routing")
        if expected is None:
            continue
        urls = extract_public_urls(case["prompt"])
        decision = inspect_prompt(case["prompt"])
        actual = "route" if decision.should_route else "skip"
        # A prompt with no public URL cannot route, whatever the task needs.
        if not urls:
            assert actual == "skip", case["id"]
            continue
        assert actual == expected, (case["id"], urls, decision.opted_out)
        checked += 1
    assert checked >= 10
