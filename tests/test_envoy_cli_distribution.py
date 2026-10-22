import json
import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_envoy_cli_wheel.py"
PACKAGE_VERSION = "0.1.5"


def test_release_wheel_build_refuses_a_dirty_source_tree(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("envoy_wheel_builder", BUILD_SCRIPT)
    builder = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder, "_source_identity", lambda _root: ("a" * 12, True))

    with pytest.raises(RuntimeError, match="release wheels require a clean source tree"):
        builder.build_wheel(
            ROOT,
            tmp_path,
            python_executable=sys.executable,
            channel="release",
        )


def test_cli_wheel_bundles_matching_core_for_github_install(tmp_path):
    python = sys.executable
    if sys.version_info < (3, 10):
        python = shutil.which("python3.11") or shutil.which("python3.10")
    if not python:
        pytest.skip("A Python 3.10+ interpreter is required to test the standalone CLI wheel")
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH"
    }

    result = subprocess.run(
        [
            python,
            str(BUILD_SCRIPT),
            "--output-dir",
            str(tmp_path),
            "--python",
            python,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=clean_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    wheel_path = next(tmp_path.glob("envoy_cli-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(metadata_path).decode("utf-8")
        build_info_path = next(name for name in names if name.endswith("envoy_cli/build_info.py"))
        build_info = wheel.read(build_info_path).decode("utf-8")

    assert any(name.startswith("envoy_cli/") and name.endswith(".py") for name in names)
    assert any(name.startswith("envoy_core/") and name.endswith(".py") for name in names)
    assert "Requires-Dist: envoy-core" not in metadata
    assert f"Version: {PACKAGE_VERSION}+preview." in metadata
    assert "Requires-Dist: pydantic<3,>=2.10" in metadata
    assert "Requires-Dist: playwright<2,>=1.52" in metadata
    assert "Requires-Dist: openai-whisper==20250625; extra == \"media\"" in metadata
    assert 'BUILD_CHANNEL = "local-preview"' in build_info

    environment_dir = tmp_path / "clean-env"
    create_environment = subprocess.run(
        [python, "-m", "venv", str(environment_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=clean_env,
    )
    assert create_environment.returncode == 0, create_environment.stdout + create_environment.stderr
    environment_python = environment_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    upgrade_pip = subprocess.run(
        [str(environment_python), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True,
        text=True,
        check=False,
        env={**clean_env, "PIP_INDEX_URL": "https://pypi.org/simple"},
    )
    assert upgrade_pip.returncode == 0, upgrade_pip.stdout + upgrade_pip.stderr
    install_result = subprocess.run(
        [
            str(environment_python),
            "-m",
            "pip",
            "install",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**clean_env, "PIP_INDEX_URL": "https://pypi.org/simple"},
    )
    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    envoy_executable = environment_dir / ("Scripts/envoy.exe" if os.name == "nt" else "bin/envoy")
    assert envoy_executable.is_file(), install_result.stdout + install_result.stderr
    smoke_env = {
        key: value
        for key, value in clean_env.items()
        if key in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    clean_home = tmp_path / "clean-home"
    clean_home.mkdir()
    smoke_env.update({
        "HOME": str(clean_home),
        "PATH": os.pathsep.join((str(envoy_executable.parent), clean_env.get("PATH", ""))),
        "PYTHONPATH": "",
    })
    if os.name == "nt":
        smoke_env["USERPROFILE"] = str(clean_home)
    smoke = subprocess.run(
        [
            str(environment_python),
            "-c",
            "from envoy_cli.cli import main; raise SystemExit(main(['doctor', '--json']))",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env=smoke_env,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    response = json.loads(smoke.stdout)
    assert response["tool"] == "envoy"
    assert response["cli_version"].split("+", 1)[0] == PACKAGE_VERSION
    assert response["core_version"] == PACKAGE_VERSION
    assert response["build"]["channel"] == "local-preview"
    assert response["build"]["source_revision"]
    assert response["build"]["source_dirty"] is True
    assert "source_checkout" not in response["build"]
    assert "direct_video_handoff" in json.dumps(response)
