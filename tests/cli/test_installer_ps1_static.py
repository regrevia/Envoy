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


def test_no_lossy_ascii_write_for_pin_or_shim(text: str):
    """`-Encoding ASCII` mangles a Chinese user name into `??`."""
    # Only code counts: the explanatory comment mentions the old encoding.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "-Encoding ASCII" not in code
    # The pin is read back as UTF-8 by install_state, so write UTF-8 without BOM.
    assert "UTF8Encoding($false)" in code
    assert "Write-Utf8NoBom" in code


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


def test_a_machine_without_python_or_uv_fetches_uv_first(text: str):
    """The wheel needs a Python; uv is how a bare Windows machine gets one."""
    assert "function Ensure-Uv" in text
    body = text[text.index("function Ensure-Uv"):]
    body = body[: body.index("function ", 10)]
    assert "uv-$arch-pc-windows-msvc.zip" in body
    assert '.sha256' in body, "the uv archive must be verified like the wheel"
    assert "Get-FileHash" in body
    runtime = text[text.index("function Install-Runtime"):]
    runtime = runtime[: runtime.index("function ", 10)]
    # Bootstrap before the python fallback: on a bare host neither exists, and the
    # python branch is the one that dies with "install Python (or uv)".
    assert runtime.index("Ensure-Uv") < runtime.index("Get-Command python")


def test_uv_branch_verifies_the_checksum_like_the_python_branch(text: str):
    """The uv path used to hand the URL straight to uv, skipping SHA256SUMS."""
    if "} else {" not in text:
        return
    runtime = text[text.index("function Install-Runtime"):]
    runtime = runtime[: runtime.index("function ", 10)]
    # Each install branch that downloads must call Confirm-Checksum.
    assert runtime.count("Confirm-Checksum") >= 2, "both uv and python branches must verify"


def test_download_keeps_the_real_wheel_filename(text: str):
    """pip/uv reject a wheel whose filename was decorated to make it unique."""
    runtime = text[text.index("function Install-Runtime"):]
    runtime = runtime[: runtime.index("function ", 10)]
    # Uniqueness must come from a temp directory, not from renaming the file.
    assert "envoy-dl-" in runtime
    assert '"envoy-" + [guid]' not in runtime


def test_pin_and_shim_follow_envoy_home(text: str):
    """The pin must land where `envoy doctor` looks.

    install_state.pin_file() is <ENVOY_HOME>/runtime-path when ENVOY_HOME is
    set; a fixed ~/.envoy/runtime-path made doctor report `unpinned` for anyone
    who relocated the runtime.
    """
    assert "$Script:EnvoyHome" in text
    assert 'Join-Path $Script:EnvoyHome "runtime-path"' in text
    assert 'Join-Path $Script:EnvoyHome "bin"' in text
    assert 'Join-Path $HOME ".envoy\\runtime-path"' not in text


def test_shim_uses_the_oem_code_page_not_utf8(text: str):
    """cmd.exe parses .cmd in the OEM code page; Encoding.Default is UTF-8 in PS7."""
    assert "OEMCodePage" in text
    assert "[System.Text.Encoding]::Default" not in text


def test_profiles_exist_and_default_to_full(text: str):
    """Same three tiers as install.sh, same default.

    install.sh cannot be run from this file to compare, so pin the strings both
    installers must agree on.
    """
    assert "[string]$Profile" in text
    assert '$Script:Profile' in text
    for profile in ("minimal", "standard", "full"):
        assert f'"{profile}"' in text
    assert '$Script:Profile -eq "minimal"' in text
    assert '$Script:Profile -eq "full"' in text
    assert '$env:ENVOY_PROFILE' in text
    assert "expected minimal, standard or full" in text


def test_profile_provisioning_runs_outside_the_runtime_install(text: str):
    """A host CLI already on PATH skips Install-Runtime, so provisioning hung
    off that branch would never apply the profile the user picked."""
    assert "function Ensure-Profile" in text
    assert "function Provision-Media" in text
    ensure = text[text.index("function Ensure-Profile"):]
    ensure = ensure[: ensure.index("function ", 10)]
    assert "Provision-Browser" in ensure and "Provision-Media" in ensure
    for branch in ('"mcp"', '"dsh"', '"claude-code"', '"codex"', '"skill"'):
        line = next(item for item in text.splitlines() if item.strip().startswith(branch))
        assert "Ensure-Profile" in line, branch
    auto = text[text.index("function Install-Auto"):]
    auto = auto[: auto.index("Confirm-Health")]
    assert "Ensure-Profile" in auto
    runtime = text[text.index("function Install-Runtime"):]
    runtime = runtime[: runtime.index("function ", 10)]
    assert "Provision-Browser $Script:RuntimeBin" not in runtime


def test_a_missing_profile_capability_fails_the_install(text: str):
    """A full install that leaves Chromium or Whisper out is not a success: the
    user would meet that download on their first link, inside the host's tool
    deadline. The doctor result decides, not the provisioning exit code."""
    assert "$Script:CapabilityMissing = $false" in text
    assert "$Script:CapabilityMissing = $true" in text
    assert 'Stop-Install "install incomplete' in text
    assert 'if ($Script:SetupBrowser -eq "1" -and -not $data.runtime.browser.ready)' in text
    assert 'if ($Script:SetupMedia -eq "1" -and -not $data.runtime.asr_model_available)' in text


def test_every_provisioning_branch_also_verifies_the_profile(text: str):
    branches = [
        line for line in text.splitlines()
        if line.strip().startswith('"') and "Ensure-Profile" in line
    ]
    assert len(branches) >= 6, branches
    for line in branches:
        assert "Confirm-Health" in line, line

