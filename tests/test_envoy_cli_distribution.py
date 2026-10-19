import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_envoy_cli_wheel.py"


def test_cli_wheel_bundles_matching_core_for_github_install(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    wheel_path = next(tmp_path.glob("envoy_cli-*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(metadata_path).decode("utf-8")

    assert any(name.startswith("envoy_cli/") and name.endswith(".py") for name in names)
    assert any(name.startswith("envoy_core/") and name.endswith(".py") for name in names)
    assert "Requires-Dist: envoy-core" not in metadata

    install_dir = tmp_path / "installed"
    install_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install_result.returncode == 0, install_result.stdout + install_result.stderr
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            "from envoy_cli.cli import main; raise SystemExit(main(['doctor', '--json']))",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"PATH": str(Path(sys.executable).parent), "PYTHONPATH": str(install_dir)},
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert '"tool": "envoy"' in smoke.stdout
    assert "direct_video_handoff" in smoke.stdout
