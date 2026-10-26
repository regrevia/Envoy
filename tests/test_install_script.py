"""The one-command installer must stay a single entry point per host.

Marketplace visitors get one line instead of a runtime step followed by two
plugin commands, so these tests pin the dispatch, the dry-run path (no side
effects), and the fact that the script is valid POSIX sh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "install.sh"


def _stubs(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("dsh", "claude", "codex"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    envoy = bin_dir / "envoy"
    envoy.write_text('#!/bin/sh\necho "envoy 0.1.14"\n')
    envoy.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["ENVOY_DRY_RUN"] = "1"
    env["ENVOY_VERSION"] = "0.1.14"
    env["HOME"] = str(tmp_path / "home")
    return bin_dir, env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_script_is_valid_posix_sh():
    result = subprocess.run(["sh", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_each_host_has_a_one_line(tmp_path):
    _, env = _stubs(tmp_path)
    for host in ("dsh", "claude-code", "codex"):
        result = _run([host], env)
        assert result.returncode == 0, (host, result.stderr)
        assert "envoy install:" in result.stdout


def test_dry_run_does_not_touch_the_profile(tmp_path):
    bin_dir, env = _stubs(tmp_path)
    env["HOME"] = str(tmp_path / "home")
    # A bare HOME plus dry-run means nothing the script prints may become real:
    # no profile dir is written and no runtime venv appears.
    result = _run(["dsh"], env)
    assert result.returncode == 0, result.stderr
    assert "plugin" in result.stdout and "--profile" in result.stdout
    assert not (tmp_path / "home" / ".local" / "share" / "envoy-dsh" / "runtime").exists()
    assert bin_dir.is_dir()


def test_unknown_host_prints_usage(tmp_path):
    _, env = _stubs(tmp_path)
    result = _run(["nope"], env)
    assert result.returncode == 2
    assert "usage: install.sh" in result.stderr
