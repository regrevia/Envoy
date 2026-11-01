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
        "github_rate_limited",
        "gh` or `git",
    ):
        assert phrase in row, phrase
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


def test_repo_root_link_is_still_routed_and_why_hooks_leave_it_alone():
    """hooks.py blocks /tree, /blob and the other diff-ish paths (hooks.py:125).

    A bare repo root stays routed on purpose: `gh`/`git` availability is a
    property of the shell running the hook, not of the URL, so gating on it
    would make routing machine-dependent. With the GitHub API path in place,
    routing a repo root now reaches a real reader instead of a rendered page,
    so leaving the rule alone is also the correct outcome.
    """

    from envoy_cli.hooks import inspect_prompt

    assert inspect_prompt("https://github.com/regrevia/Envoy/tree/main/src").urls == ()
    assert inspect_prompt("https://github.com/regrevia/Envoy/blob/main/README.md").urls == ()
    assert inspect_prompt("https://github.com/regrevia/Envoy").urls == (
        "https://github.com/regrevia/Envoy",
    )
