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
            "LICENSE",
            "NOTICE",
            "references/protocol.md",
            "SKILL.md",
        ]
        manifest = json.loads(archive.read("envoy-skill.json"))
        assert manifest["protocol"] == "envoy.read/1"
        assert manifest["installation"] == "INSTALL.md"
        assert manifest["mcp"]["args"] == ["mcp"]
        assert b"API Key" not in archive.read("SKILL.md")
        install = archive.read("INSTALL.md")
        assert b"GitHub Release" in install
        assert manifest["license"] == "Apache-2.0"
        for filename in ("LICENSE", "NOTICE"):
            assert archive.read(filename) == (ROOT / filename).read_bytes()
        assert b"preview" not in install.lower()
        assert b"envoy-preview" not in install
        assert b"uv tool install --force" in install
        assert b"envoy asr install --model base" in install
        assert b"ENVOY_PYPI_INDEX_URL" in install
        host_readme = archive.read("hosts/README.md")
        assert b"Release wheel" not in host_readme
        skill = archive.read("SKILL.md")
        assert b"installed Release runtime" not in skill
        for tool_name in (
            b"envoy_read_local_video",
            b"envoy_discover",
            b"envoy_collection_checkpoint",
        b"envoy_task",
            b"envoy_replay",
        ):
            assert tool_name in skill
        assert b"Xiaohongshu Notes" in skill
        assert b"Common Video Links" in skill
        assert b"Optional OCR" in skill
        assert b"ocr_profile" in skill
        assert b"Album totals may be unknown" in skill
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
    assert "only a unique URL exposed by the page's primary video element" in skill
    assert "cross-evidence interpretation" in skill
    normalized_skill = " ".join(skill.split())
    assert "frame capability alone never enables frame extraction" in normalized_skill
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


def test_skill_bundle_explains_collection_discovery_selection_and_resume(tmp_path):
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
    normalized = " ".join(skill.split())
    assert "`envoy_discover`" in normalized
    assert "`envoy_collection_checkpoint`" in normalized
    assert "Titles are selection hints, not content evidence" in normalized
    assert "ask a follow-up question" in normalized
    assert "checkpoint each selected item" in normalized
    assert "Do not claim that the profile is fully enumerated" in normalized


def test_skill_bundle_explains_local_video_plus_link_workflow(tmp_path):
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
    normalized = " ".join(skill.split())
    assert "`envoy_read_local_video`" in normalized
    assert "read the local file and public link as separate sources" in normalized
    assert "combine their evidence in the host response" in normalized
    assert "operation key" in normalized
