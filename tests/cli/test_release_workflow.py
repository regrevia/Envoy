"""The release workflow is the only place a published asset is produced.

These checks read ``.github/workflows/release.yml`` as text and fail on the
mistakes that have real consequences: an asset that never gets built, a
manifest emitted before the assets it hashes, a checksum file that misses the
installers a host downloads first, or a hardcoded version that drifts from the
tag. Nothing here runs the workflow.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def text() -> str:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def test_tag_names_the_version_guard_and_no_version_is_hardcoded(text: str):
    assert "RELEASE_TAG: ${{ inputs.release_tag || github.ref_name }}" in text
    assert 'expected = os.environ["RELEASE_TAG"].removeprefix("v")' in text
    # Both packages are compared against the tag, not against each other.
    assert "packages/envoy-cli/pyproject.toml" in text
    assert "packages/envoy-core/pyproject.toml" in text
    assert "does not match tag" in text


def test_all_four_consistency_gates_run_before_building(text: str):
    for gate in (
        "scripts/check_versions.py --check",
        "scripts/check_release_manifest.py --check",
        "scripts/build_envoy_dsh_plugin.py --check",
        "scripts/sync_host_skills.py --check",
    ):
        assert gate in text, gate
    assert text.index("check_versions.py --check") < text.index("build_envoy_cli_wheel.py")


def test_every_shipped_asset_is_produced(text: str):
    assert "build_envoy_cli_wheel.py --output-dir dist --channel release" in text
    assert "package_envoy_skill.py --output dist/envoy-skill.zip" in text
    assert "npm pack --pack-destination dist integrations/dsh/envoy-dsh" in text
    assert "cp integrations/install.sh dist/install.sh" in text
    assert "cp integrations/install.ps1 dist/install.ps1" in text


def test_manifest_is_emitted_after_assets_and_before_checksums(text: str):
    assets = text.index("npm pack --pack-destination")
    emit = text.index("check_release_manifest.py --emit dist/release-manifest.json")
    sums = text.index("dist/SHA256SUMS")
    assert assets < emit < sums
    # SHA256SUMS is derived from what is on disk, so the manifest and both
    # installers are covered without a second list to keep in sync.
    assert 'assets = sorted(path for path in Path("dist").iterdir() if path.is_file())' in text


def test_npm_publish_stays_optional_and_never_hides_a_failure(text: str):
    assert "if [ -z \"${NODE_AUTH_TOKEN}\" ]; then" in text
    assert "NPM_TOKEN is not configured" in text
    # A missing token is the only tolerated skip; there is no blanket `|| true`.
    assert "|| true" not in text


def test_release_uploads_dist_and_notes_from_changelog(text: str):
    assert 'gh release create "$RELEASE_TAG" dist/*' in text
    assert "--verify-tag" in text
    assert "--notes-file CHANGELOG.md" in text
