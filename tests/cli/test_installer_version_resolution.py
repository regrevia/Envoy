"""Both installers resolve the release version at run time.

A literal version in either script is the bug this guards: it silently pins a
one-line install to whatever release was current the day it was written, and the
installer then downloads assets that a later tag never published.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "integrations" / "install.sh"
PS1 = ROOT / "integrations" / "install.ps1"
LITERAL = re.compile(r"v\d+\.\d+\.\d+")


@pytest.fixture(scope="module")
def sh() -> str:
    return SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1() -> str:
    return PS1.read_text(encoding="utf-8")


def test_shell_installer_has_no_literal_version(sh: str):
    assert not LITERAL.findall(sh), LITERAL.findall(sh)
    assert "resolve_version()" in sh
    assert "integrations/release-manifest.json" in sh


def test_powershell_installer_has_no_literal_version(ps1: str):
    assert not LITERAL.findall(ps1), LITERAL.findall(ps1)
    assert "$Script:ManifestUrl" in ps1
    assert "integrations/release-manifest.json" in ps1


def test_both_installers_build_asset_urls_from_the_resolved_version(sh: str, ps1: str):
    # The version segment of every asset URL is an interpolation, never a value.
    # POSIX goes through the overridable release root, whose default is the same
    # public path; Windows builds the GitHub URL directly.
    assert '$RELEASES_BASE/v$RUNTIME_VERSION/' in sh
    assert 'ENVOY_RELEASES_BASE:-https://github.com/$REPO/releases/download' in sh
    assert '$($Script:ReleasesBase)/v$($Script:RuntimeVersion)/' in ps1
    assert 'releases/download\"\n}' in ps1 or 'releases/download' in ps1


def test_the_manifest_url_and_the_asset_host_come_from_one_repo_variable(sh: str, ps1: str):
    assert 'REPO="' in sh and 'REPO' in sh[sh.index('REPO="'):][:200]
    assert "$Script:Repo" in ps1
    for text in (sh, ps1):
        assert "regrevia/Envoy" in text


def test_version_override_is_honoured_on_both_sides(sh: str, ps1: str):
    assert "ENVOY_VERSION" in sh
    assert "ENVOY_VERSION" in ps1


def test_release_root_is_overridable_on_both_sides(sh: str, ps1: str):
    # A mirror or a loopback checkout must be able to serve v<version>/ assets
    # and the manifest, and the untouched default must stay the public host.
    assert "ENVOY_MANIFEST_URL:-https://raw.githubusercontent.com/$REPO/main/" in sh
    assert sh.count("$RELEASES_BASE/v$") >= 4
    assert "$env:ENVOY_RELEASES_BASE" in ps1 and "$env:ENVOY_MANIFEST_URL" in ps1
    assert ps1.count('$($Script:ReleasesBase)/v$') >= 4
    assert "https://github.com/$($Script:Repo)/releases/download" in ps1
    # A fork changes the repository once and every URL follows.
    assert '${ENVOY_REPO:-regrevia/Envoy}' in sh
    assert "$env:ENVOY_REPO" in ps1
