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


# --- Windows-adaptation regressions (seen on a real host) ------------------

def test_dry_run_never_dies_on_the_runtime_it_did_not_install(text: str):
    """Get-EnvoyBinary must return the planned target under ENVOY_DRY_RUN.

    A dry run writes nothing, so RuntimeBin is absent; without an early return
    Get-EnvoyBinary fell through to Stop-Install and `install.ps1 skill` under
    ENVOY_DRY_RUN=1 exited 1 — the very path the Windows handoff recommends.
    """
    body = text[text.index("function Get-EnvoyBinary"):]
    body = body[: body.index("function ", 10)]
    assert "$Script:DryRun" in body, "Get-EnvoyBinary must special-case a dry run"
    # The dry-run return has to come before the Stop-Install fallback.
    assert body.index("$Script:DryRun") < body.index("Stop-Install")


def test_user_path_write_preserves_expand_type(text: str):
    """A REG_EXPAND_SZ user PATH must not be downgraded to REG_SZ.

    [Environment]::SetEnvironmentVariable writes REG_SZ and would freeze
    %USERPROFILE% etc. The installer must read the raw value and write back via
    reg.exe with the original type.
    """
    assert "function Get-RawUserPath" in text
    assert "DoNotExpandEnvironmentNames" in text
    assert "REG_EXPAND_SZ" in text
    # reg add is the non-lossy write; the .NET setter stays only as a fallback.
    assert "reg.exe" in text
    # Pure merge helper exists so the no-duplicate rule is testable offline.
    assert "function Join-PathValue" in text
