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
    for host in ("dsh", "claude-code", "codex", "mcp", "skill"):
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


def test_mcp_target_merges_instead_of_replacing(tmp_path):
    """Claude Desktop and Cursor users only need an MCP entry; the script must
    keep the other servers already in that file."""
    bin_dir, env = _stubs(tmp_path)
    home = tmp_path / "home"
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers": {"other": {"command": "other"}}}')
    env.pop("ENVOY_DRY_RUN", None)
    env["ENVOY_VERSION"] = "0.1.14"
    result = _run(["mcp"], env)
    assert result.returncode == 0, result.stderr
    import json

    data = json.loads(config.read_text())
    assert data["mcpServers"]["other"] == {"command": "other"}
    assert data["mcpServers"]["envoy"]["args"] == ["mcp"]
    backups = list((home / ".cursor").glob("mcp.json.envoy-*"))
    assert len(backups) == 1


def test_skill_target_unpacks_the_release_zip(tmp_path):
    bin_dir, env = _stubs(tmp_path)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    skill_zip = tmp_path / "envoy-skill.zip"
    import zipfile

    with zipfile.ZipFile(skill_zip, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: envoy\n---\n")
    # Serve the wheel URL check and the zip download without the network.
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in *envoy-skill.zip*) cp %s "$3" ;; esac\nexit 0\n' % skill_zip
    )
    curl_stub.chmod(0o755)
    env.pop("ENVOY_DRY_RUN", None)
    env["ENVOY_VERSION"] = "0.1.14"
    env["ENVOY_SKILL_DIR"] = str(tmp_path / "skill")
    result = _run(["skill"], env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "skill" / "SKILL.md").is_file()


def test_stale_python_venv_is_rebuilt_and_health_uses_the_runtime(tmp_path):
    """A 3.9 runtime venv (macOS default python3) must be replaced, not reused."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "uv.log"
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{log}"\n'
        'case "$1" in\n'
        '  venv)\n'
        '    for arg in "$@"; do dir="$arg"; done\n'
        '    mkdir -p "$dir/bin"\n'
        '    printf \'#!/bin/sh\\ncase "$2" in *version_info*) exit 0;; esac\\nexec /usr/bin/python3 "$@"\\n\' > "$dir/bin/python"\n'
        '    cat > "$dir/bin/envoy" <<\'EOS\'\n'
        '#!/bin/sh\n'
        'if [ "$1" = doctor ]; then echo \'{"cli_version":"0.1.14","protocol_version":"envoy.read/1"}\'; fi\n'
        'EOS\n'
        '    chmod +x "$dir/bin/python" "$dir/bin/envoy"\n'
        '    ;;\n'
        'esac\n'
        'exit 0\n'
    )
    uv.chmod(0o755)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    stale = runtime / "bin" / "python"
    stale.write_text("#!/bin/sh\nexit 1\n")
    stale.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()

    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "ENVOY_HOME": str(runtime),
        "ENVOY_VERSION": "0.1.14",
        "ENVOY_DRY_RUN": "0",
    }
    result = subprocess.run(
        ["sh", str(SCRIPT), "mcp"], env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    invocations = log.read_text()
    assert "venv --quiet --python 3.11" in invocations
    assert (runtime / "bin" / "envoy").is_file()
    assert "runtime 0.1.14, protocol envoy.read/1" in result.stdout


def test_dev_install_script_is_valid_posix_sh():
    script = SCRIPT.parents[1] / "scripts" / "dev_install.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK)
    result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_unknown_host_prints_usage(tmp_path):
    _, env = _stubs(tmp_path)
    result = _run(["nope"], env)
    assert result.returncode == 2
    assert "usage: install.sh" in result.stderr
