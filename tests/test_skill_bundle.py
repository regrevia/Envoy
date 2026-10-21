import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package_envoy_skill.py"


def test_skill_bundle_contains_only_public_skill_material(tmp_path):
    output = tmp_path / "envoy-skill.zip"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["manifest"]["credentials_included"] is False
    assert payload["manifest"]["entrypoint"] == "SKILL.md"
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert names == [
            "envoy-skill.json",
            "hosts/mcp.json",
            "hosts/README.md",
            "INSTALL.md",
            "references/protocol.md",
            "SKILL.md",
        ]
        manifest = json.loads(archive.read("envoy-skill.json"))
        assert manifest["protocol"] == "envoy.read/1"
        assert manifest["installation"] == "INSTALL.md"
        assert b"API Key" not in archive.read("SKILL.md")
        assert b"GitHub Release" in archive.read("INSTALL.md")
        host_config = json.loads(archive.read("hosts/mcp.json"))
        assert host_config["mcpServers"]["envoy"]["args"] == ["mcp"]


def test_skill_bundle_is_deterministic_for_same_sources(tmp_path):
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    for output in (first, second):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert first.read_bytes() == second.read_bytes()


def test_skill_bundle_uses_isolated_browser_after_empty_or_challenged_read(tmp_path):
    output = tmp_path / "envoy-skill.zip"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(output) as archive:
        skill = archive.read("SKILL.md").decode("utf-8")

    assert "empty_content" in skill
    assert "challenge_required" in skill
    assert "the source in its isolated browser" in skill
    assert "ordinary page controls such as" in skill
    assert "dismiss a dismissible login dialog" in skill
    assert "dialogs inside embedded frames" in skill
    assert "repeats after scrolling and lazy" in skill
    assert "waits for the user to complete" in skill
    assert "browser automation tool is" in skill
    assert "required." in skill
    assert "Never automate solving or bypassing a" in skill
    assert "180 seconds by default" in skill
    assert "returns `challenge_required` instead of `empty_content`" in skill
    assert "scope.include_visual: true" in skill
    assert "cross-evidence interpretation" in skill
    normalized_skill = " ".join(skill.split())
    assert "inspect every listed resource" in normalized_skill
    assert "once per inspected resource" in normalized_skill
    assert "Do not claim complete visual coverage" in normalized_skill
    assert "`needs_host` is a handoff, not a terminal answer state" in normalized_skill
    assert "expires_at" in normalized_skill
    assert "Do not repeat" in normalized_skill


def test_skill_bundle_explains_per_frame_observation_handoff(tmp_path):
    output = tmp_path / "envoy-skill.zip"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    with zipfile.ZipFile(output) as archive:
        skill = archive.read("SKILL.md").decode("utf-8")
        protocol = archive.read("references/protocol.md").decode("utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_protocol = " ".join(protocol.split())
    assert "pairing" in normalized_skill
    assert "resource_id" in normalized_skill
    assert "source_block_id" in normalized_protocol
    assert "per inspected frame" in normalized_protocol
    assert "Wait for each observation call to return" in normalized_protocol
    assert "`needs_host` is a handoff, not a terminal answer state" in normalized_protocol
    assert "expires_at" in normalized_protocol
    assert "do not repeat `envoy_read`" in normalized_protocol
