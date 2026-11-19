"""Repository-level guards for shipped versions and generated artifacts.

The release version is hand-written into a dozen manifests, and the host skill
copies are generated from one canonical source. These tests fail when any of
them drifts, so a partial bump or a forgotten regeneration cannot ship.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(module: str):
    spec = importlib.util.spec_from_file_location(module, SCRIPTS / f"{module}.py")
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


versions = _load("check_versions")


def test_every_version_declaration_matches_the_cli_package():
    expected = versions.authoritative_version()
    rows = versions.collect()
    assert versions.find_drift(rows, expected) == []
    assert len(rows) == len(versions.DECLARATIONS) + len(versions.TEST_CONSTANTS)


def test_release_docs_mention_the_current_version():
    assert versions.find_doc_gaps(versions.authoritative_version()) == []


def test_drift_report_separates_stale_values_from_unreadable_files():
    rows = [
        {"path": "manifest.json", "version": "0.1.6"},
        {"path": "plugin.json", "version": "0.1.5"},
        {"path": "gone.json", "version": None, "error": "missing"},
    ]

    drift = versions.find_drift(rows, "0.1.6")

    assert [row["path"] for row in drift] == ["plugin.json", "gone.json"]
    assert [row["problem"] for row in drift] == ["drift", "unreadable"]


def test_version_check_cli_succeeds_on_the_checked_out_tree():
    result = _run("check_versions.py", "--check", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["drift"] == []
    assert payload["docs_missing_version"] == []


def test_host_skill_copies_are_byte_identical_to_the_canonical_skill():
    canonical = ROOT / "integrations" / "skills" / "envoy"
    copies = [
        ROOT / "integrations" / "claude-code" / "envoy" / "skills" / "envoy",
        ROOT / "integrations" / "codex" / "envoy" / "skills" / "envoy",
    ]

    for relative in ("SKILL.md", "references/protocol.md"):
        expected = (canonical / relative).read_bytes()
        for copy in copies:
            actual = (copy / relative).read_bytes()
            assert actual == expected, f"{copy.relative_to(ROOT)}/{relative} is stale"


def test_host_plugins_declare_their_bundled_skills_directory():
    claude_plugin = json.loads(
        (ROOT / "integrations" / "claude-code" / "envoy" / ".claude-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )
    codex_plugin = json.loads(
        (ROOT / "integrations" / "codex" / "envoy" / ".codex-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )

    # Claude Code discovers skills/ without a manifest field; Codex needs one.
    claude_skills = claude_plugin.get("skills", "skills")
    assert (ROOT / "integrations" / "claude-code" / "envoy" / claude_skills).is_dir()
    assert (ROOT / "integrations" / "claude-code" / "envoy" / "skills" / "envoy" / "SKILL.md").is_file()
    assert codex_plugin["skills"] == "./skills/"
    assert (ROOT / "integrations" / "codex" / "envoy" / "skills" / "envoy" / "SKILL.md").is_file()


@pytest.mark.parametrize(
    "script",
    ["sync_host_skills.py", "build_envoy_dsh_plugin.py"],
)
def test_generated_host_artifacts_are_current(script: str):
    result = _run(script, "--check")

    assert result.returncode == 0, result.stdout + result.stderr


def _markdown_link_targets(text: str) -> list[str]:
    """Link targets outside fenced code blocks.

    A shell snippet can contain ``]((Get-Date) ...)`` by accident; only prose
    links are documentation.
    """

    prose: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            prose.append(line)
    return re.findall(r"\]\(([^)\s]+)\)", "\n".join(prose))


def test_markdown_links_are_relative_and_land_inside_the_repository():
    """A link that points at one machine's disk is a dead link for everyone else.

    Several docs were written on a checkout at a fixed absolute path, so their
    links only resolved for the author. Nothing else validates Markdown links,
    which is why they survived; this keeps them relative and real.
    """

    markdown = [
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts
        and "node_modules" not in path.parts
        and "build" not in path.parts
    ]
    assert markdown

    absolute: list[str] = []
    missing: list[str] = []
    for path in markdown:
        for target in _markdown_link_targets(path.read_text(encoding="utf-8", errors="replace")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if target.startswith(("/", "$", "~", "file:")):
                absolute.append(f"{path.relative_to(ROOT)}: {target}")
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)}: {target}")

    assert absolute == [], "Markdown links must be repository-relative:\n" + "\n".join(absolute)
    assert missing == [], "Markdown links point at files that do not exist:\n" + "\n".join(missing)
