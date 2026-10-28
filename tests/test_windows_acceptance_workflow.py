"""Guards for the self-hosted Windows acceptance workflow.

`regrevia/Envoy` is public: a self-hosted runner reachable from a fork pull
request would execute arbitrary code on a development machine. These tests pin
the parts of the workflow that make that impossible, so a later edit cannot
quietly widen the trigger surface.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "windows-acceptance.yml"
SCRIPTS = ROOT / "scripts" / "windows"
DOC = ROOT / "docs" / "windows-acceptance.md"


def workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.is_file(), "windows-acceptance.yml should exist"
    assert DOC.is_file(), "docs/windows-acceptance.md should exist"


def test_no_pull_request_trigger():
    text = workflow()
    # A comment may name the event; a YAML trigger key may not.
    assert not re.search(r"^\s{2}pull_request(_target)?\s*:", text, re.MULTILINE), (
        "a self-hosted runner must never be triggered by pull requests"
    )
    assert not re.search(r"^\s{2}(push|schedule|release)\s*:", text, re.MULTILINE)


def test_only_dispatch_and_release_run():
    text = workflow()
    assert re.search(r"^\s{2}workflow_dispatch\s*:", text, re.MULTILINE)
    assert re.search(r"^\s{2}workflow_run\s*:", text, re.MULTILINE)
    assert "Envoy 正式发行 / Envoy Release" in text, "follow the tag release, not an arbitrary workflow"


def test_self_hosted_jobs_are_labelled():
    text = workflow()
    assert text.count("runs-on: [self-hosted, windows, x64, envoy-win]") == 2
    assert "runs-on: windows-2022" in text, "keep a hosted clean-machine leg"


def test_default_permissions_are_read():
    text = workflow()
    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE), (
        "default to contents: read; widen only on an evidence step"
    )
    assert "contents: write" not in text or text.count("contents: write") == 1


def test_both_powershell_shells_are_exercised():
    text = workflow()
    assert "shell: powershell" in text
    assert "shell: pwsh" in text
    assert "ExpectShellMajor 5" in text
    assert "ExpectShellMajor 7" in text


def test_user_path_is_snapshotted_and_restored():
    text = workflow()
    assert text.count("path.before.json") >= 4
    assert text.count("还原用户 PATH / Restore PATH") >= 2


def test_identity_is_masked_in_logs():
    text = workflow()
    assert "::add-mask::" in text
    for name in ("USERNAME", "COMPUTERNAME", "USERPROFILE", "LOCALAPPDATA"):
        assert name in text


def test_acceptance_scripts_are_ascii():
    # Windows PowerShell 5.1 reads a .ps1 in the ANSI codepage unless it has a
    # BOM: a non-ASCII comment can corrupt the parse of the whole file.
    for script in SCRIPTS.glob("*.ps1"):
        script.read_bytes().decode("ascii")


def test_acceptance_script_restores_state():
    text = (SCRIPTS / "acceptance.ps1").read_text(encoding="utf-8")
    assert "finally" in text
    assert "Set-RawUserPath" in text
    assert "REG_EXPAND_SZ" in text
    assert "Remove-Item -LiteralPath $EnvoyHome -Recurse" in text


def test_runner_registration_is_not_owned_by_this_repository():
    """Node registration is regrevia infrastructure, not an Envoy artifact.

    A personal GitHub account has no organization-level runners, so the same
    machine serves several repositories as several independent instances. That
    setup lives in regrevia/win-runner; keeping a copy here would drift.
    """
    assert not (SCRIPTS / "runner-setup.ps1").exists()

    doc = (ROOT / "docs" / "windows-acceptance.md").read_text(encoding="utf-8")
    assert "regrevia/win-runner" in doc
    assert "runner-setup.ps1" not in doc or "win-runner" in doc

    workflow = (ROOT / ".github" / "workflows" / "windows-acceptance.yml").read_text(encoding="utf-8")
    assert "[self-hosted, windows, x64, envoy-win]" in workflow
