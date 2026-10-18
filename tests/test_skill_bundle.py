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
        assert names == ["envoy-skill.json", "SKILL.md", "references/protocol.md"]
        manifest = json.loads(archive.read("envoy-skill.json"))
        assert manifest["protocol"] == "envoy.read/1"
        assert b"API Key" not in archive.read("SKILL.md")


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
