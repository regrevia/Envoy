"""Static checks for the PowerShell installer.

There is no PowerShell on the CI host, so `install.ps1` cannot be executed here.
These checks catch the mistakes a text edit actually makes: a `$Script:` variable
that is used but never assigned, a reference that reads a variable before the
line that sets it, and unbalanced braces/parens. They are not a substitute for
running the script on Windows; they only keep the static surface honest.
"""

import re
from pathlib import Path

import pytest

PS1 = Path(__file__).resolve().parents[2] / "integrations" / "install.ps1"


@pytest.fixture(scope="module")
def text() -> str:
    return PS1.read_text(encoding="utf-8")


def _assignments(text: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for match in re.finditer(r"^\$Script:([A-Za-z]+)\s*=", text, re.MULTILINE):
        found.setdefault(match.group(1), match.start())
    return found


def test_every_script_variable_is_assigned(text: str):
    assigned = _assignments(text)
    assert assigned, "install.ps1 should define its state on $Script: variables"
    used = set(re.findall(r"\$Script:([A-Za-z]+)", text))
    assert used - set(assigned) == set()


def test_no_script_variable_is_read_before_it_is_set(text: str):
    assigned = _assignments(text)
    for name in re.findall(r"\$Script:([A-Za-z]+)", text):
        first_use = text.index(f"$Script:{name}")
        assert first_use >= assigned[name], f"$Script:{name} is read before it is set"


def test_braces_and_parentheses_are_balanced(text: str):
    assert text.count("{") == text.count("}")
    assert text.count("(") == text.count(")")


def test_the_mirror_hooks_exist_once_each(text: str):
    # Once as the condition, once as the value; no second place to drift.
    assert text.count("$env:ENVOY_RELEASES_BASE") == 2
    assert text.count("$env:ENVOY_MANIFEST_URL") == 2
    assert text.count("$env:ENVOY_REPO") == 2
    # Both defaults point at the public host, so an unset variable changes nothing.
    assert "https://github.com/$($Script:Repo)/releases/download" in text
    assert "https://raw.githubusercontent.com/$($Script:Repo)/main/" in text
    assert '"regrevia/Envoy"' in text


def test_both_installers_dispatch_the_same_host_targets(text: str):
    """A host added on one platform must not be missing on the other."""
    shell = (PS1.parent / "install.sh").read_text(encoding="utf-8")
    documented = {"dsh", "claude-code", "codex", "mcp", "skill", "all", "path"}
    cases = set(re.findall(r'^\s*"([a-z-]*)"\s*\{', text, re.MULTILINE))
    assert documented <= cases, (documented - cases)
    assert "" in cases and "auto" in cases  # the no-argument default
    assert "dsh|claude-code|codex|mcp|skill|all|path" in shell
