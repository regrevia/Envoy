"""The one-command installer must stay a single entry point per host.

Marketplace visitors get one line instead of a runtime step followed by two
plugin commands, so these tests pin the dispatch, the dry-run path (no side
effects), and the fact that the script is valid POSIX sh.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "install.sh"


def _stubs(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
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
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(
        "%s  envoy-skill.zip\n" % hashlib.sha256(skill_zip.read_bytes()).hexdigest()
    )
    # Serve the zip download and the checksum list without the network.
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in *SHA256SUMS*) cat %s ;; *envoy-skill.zip*) cp %s "$3" ;; esac\n'
        "exit 0\n" % (sums, skill_zip)
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


def test_uv_is_bootstrapped_when_only_an_old_python3_exists(tmp_path):
    """A machine with just macOS python3 3.9 has no usable interpreter: the
    installer must fetch uv itself instead of sending the user to a doc."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    uv_copy = bin_dir / "uv-inner"
    uv_copy.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  venv)\n'
        '    for arg in "$@"; do dir="$arg"; done\n'
        '    mkdir -p "$dir/bin"\n'
        '    printf \'#!/bin/sh\\ncase "$2" in *version_info*) exit 0;; esac\\nexec /usr/bin/python3 "$@"\\n\' > "$dir/bin/python"\n'
        '    printf \'#!/bin/sh\\necho "{\\\"cli_version\\\":\\\"0.1.14\\\"}"\\n\' > "$dir/bin/envoy"\n'
        '    chmod +x "$dir/bin/python" "$dir/bin/envoy"\n'
        '    ;;\n'
        'esac\n'
        'exit 0\n'
    )
    uv_copy.chmod(0o755)
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(f'#!/bin/sh\nmkdir -p "{home}/.local/bin"\ncp "{uv_copy}" "{home}/.local/bin/uv"\nchmod +x "{home}/.local/bin/uv"\n')
    curl_stub.chmod(0o755)
    runtime = tmp_path / "runtime"

    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "ENVOY_HOME": str(runtime),
        "ENVOY_VERSION": "0.1.14",
    }
    result = subprocess.run(
        ["sh", str(SCRIPT), "mcp"], env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert "uv not found; installing it" in result.stdout
    assert (runtime / "bin" / "envoy").is_file()


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


def test_checksum_list_without_the_artifact_fails(tmp_path):
    """A reachable SHA256SUMS that lacks the file must fail, not skip silently."""
    bin_dir, env = _stubs(tmp_path)
    sums = tmp_path / "SHA256SUMS-other"
    sums.write_text("%s  some-other-file.zip\n" % ("0" * 64))
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(
        "#!/bin/sh\ncase \"$*\" in *SHA256SUMS*) cat %s ;; esac\nexit 0\n" % sums
    )
    curl_stub.chmod(0o755)
    env.pop("ENVOY_DRY_RUN", None)
    result = _run(["skill"], env)
    assert result.returncode != 0
    assert "has no entry for envoy-skill.zip" in result.stderr


def test_unreachable_checksum_list_is_announced(tmp_path):
    """No SHA256SUMS at all must say so and still finish on the dry-run path."""
    bin_dir, env = _stubs(tmp_path)
    result = _run(["skill"], env)
    assert result.returncode == 0, result.stderr
    assert "envoy doctor --json" in result.stdout


def test_profiles_choose_how_much_is_provisioned(tmp_path):
    """One knob, three tiers. A first link must never be the moment something
    downloads, so the default profile is the complete one: runtime, Chromium
    and the Whisper base model."""
    expected = {
        "minimal": (False, False),
        "standard": (True, False),
        "full": (True, True),
    }
    for profile, (wants_browser, wants_media) in expected.items():
        _, env = _stubs(tmp_path / profile)
        result = _run(["--profile", profile, "mcp"], env)
        assert result.returncode == 0, (profile, result.stderr)
        assert ("browser runtime (one-time" in result.stdout) is wants_browser, profile
        assert ("speech recognition (Whisper" in result.stdout) is wants_media, profile


def test_full_is_the_default_profile(tmp_path):
    _, env = _stubs(tmp_path)
    result = _run(["mcp"], env)
    assert result.returncode == 0, result.stderr
    assert "browser runtime (one-time" in result.stdout
    assert "speech recognition (Whisper base" in result.stdout


def test_profile_accepts_the_equals_form_and_the_env(tmp_path):
    _, env = _stubs(tmp_path / "equals")
    result = _run(["--profile=minimal", "mcp"], env)
    assert result.returncode == 0, result.stderr
    assert "browser runtime (one-time" not in result.stdout

    _, env = _stubs(tmp_path / "env")
    env["ENVOY_PROFILE"] = "standard"
    env.pop("ENVOY_SETUP_MEDIA", None)
    result = _run(["mcp"], env)
    assert result.returncode == 0, result.stderr
    assert "browser runtime (one-time" in result.stdout
    assert "speech recognition (Whisper" not in result.stdout


def test_environment_override_wins_over_the_profile(tmp_path):
    """A CI box or a metered connection may want the full tier's plugin wiring
    without its downloads; the explicit override still wins."""
    _, env = _stubs(tmp_path)
    env["ENVOY_SETUP_BROWSER"] = "0"
    env["ENVOY_SETUP_MEDIA"] = "0"
    result = _run(["--profile", "full", "mcp"], env)
    assert result.returncode == 0, result.stderr
    assert "browser runtime (one-time" not in result.stdout
    assert "speech recognition (Whisper" not in result.stdout


def test_unknown_profile_is_rejected(tmp_path):
    _, env = _stubs(tmp_path)
    result = _run(["--profile", "ultra", "mcp"], env)
    assert result.returncode != 0
    assert "unknown profile 'ultra'" in result.stderr


def test_a_full_install_that_leaves_the_profile_incomplete_fails(tmp_path):
    """`envoy asr install` can exit 0 and still leave no model. Exit status has
    to match what the profile promised, or the user's first video is the 140 MB
    download the installer was supposed to absorb."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    envoy = bin_dir / "envoy"
    envoy.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  --version) echo "envoy 0.1.23" ;;\n'
        "  doctor) printf '%s\\n' "
        "'{\"cli_version\":\"0.1.23\",\"protocol_version\":\"1\","
        "\"runtime\":{\"browser\":{\"ready\":false},"
        "\"asr_model\":\"base\",\"asr_model_available\":false}}' ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    envoy.chmod(0o755)
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["ENVOY_VERSION"] = "0.1.23"
    env["ENVOY_DRY_RUN"] = "0"
    env["ENVOY_SETUP_BROWSER"] = "1"
    env["ENVOY_SETUP_MEDIA"] = "1"
    env["ENVOY_RUNTIME_BIN"] = str(envoy)
    result = _run(["mcp"], env)
    assert result.returncode != 0, result.stdout
    assert "install incomplete" in result.stderr
    assert "envoy asr install --model base" in result.stderr

