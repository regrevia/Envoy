"""The README install lines must match what the installers actually dispatch.

A one-line install is only one line until the docs drift from the script: a
renamed target, a moved path or a different default branch turns the copy-paste
command into a 404 or an "unknown host" error. These checks compare the README
against the scripts instead of trusting both were edited together.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SH = ROOT / "integrations" / "install.sh"
PS1 = ROOT / "integrations" / "install.ps1"

RAW = "https://raw.githubusercontent.com/{repo}/main/integrations/{name}"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sh() -> str:
    return SH.read_text(encoding="utf-8")


def _sh_repo(sh: str) -> str:
    match = re.search(r'^REPO="\$\{ENVOY_REPO:-([^}]+)\}"', sh, re.MULTILINE)
    assert match, "install.sh should default REPO to the public repository"
    return match.group(1)


def _dispatched_targets(sh: str) -> set[str]:
    # The host word is the last argument, after any --profile flag, so the list
    # lives in the case block that dispatches on it.
    body = sh[sh.index('case "$host" in') :]
    return set(re.findall(r"^\s{2}([a-z][a-z-]*)(?:\|[a-z-]+)?\)", body, re.MULTILINE))


def test_posix_one_liner_points_at_the_default_repository(readme: str, sh: str):
    url = RAW.format(repo=_sh_repo(sh), name="install.sh")
    assert readme.count(url) >= 2, f"both language sections should use {url}"


def test_powershell_one_liner_points_at_the_default_repository(readme: str, sh: str):
    url = RAW.format(repo=_sh_repo(sh), name="install.ps1")
    assert readme.count(url) >= 2


def test_every_readme_target_is_dispatched(readme: str, sh: str):
    dispatched = _dispatched_targets(sh)
    assert {"claude-code", "codex", "dsh", "auto"} <= dispatched
    targets = set(re.findall(r"install\.sh \| sh -s -- ([a-z-]+)", readme))
    assert targets, "the README should show copy-paste commands"
    assert targets - dispatched == set()


def test_every_dispatch_target_is_documented(readme: str, sh: str):
    # `path` is an internal helper for the shim, not a user-facing install step.
    downloadable = _dispatched_targets(sh) - {"path"}
    missing = {
        name
        for name in downloadable
        if not re.search(rf"(?<![a-z-]){re.escape(name)}(?![a-z-])", readme)
    }
    assert missing == set(), f"undocumented install targets: {sorted(missing)}"
