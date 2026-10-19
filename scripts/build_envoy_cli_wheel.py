#!/usr/bin/env python3
"""Build a self-contained Envoy CLI wheel that embeds the matching core."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def build_wheel(repo_root: Path, output_dir: Path, python_executable: str = sys.executable) -> Path:
    cli_root = repo_root / "packages" / "envoy-cli"
    core_root = repo_root / "packages" / "envoy-core" / "src" / "envoy_core"
    if not cli_root.is_dir() or not core_root.is_dir():
        raise FileNotFoundError("Envoy CLI or core package source was not found")
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="envoy-cli-wheel-") as temporary:
        staging = Path(temporary) / "envoy-cli"
        shutil.copytree(
            cli_root,
            staging,
            ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__"),
        )
        shutil.copytree(core_root, staging / "src" / "envoy_core")
        subprocess.run(
            [
                python_executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                str(staging),
                "--wheel-dir",
                str(output_dir),
            ],
            check=True,
        )

    wheels = sorted(output_dir.glob("envoy_cli-*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise RuntimeError("Wheel build completed without producing envoy_cli wheel")
    return wheels[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--python", default=sys.executable, help="Python 3.10+ executable used to build the wheel")
    args = parser.parse_args()
    try:
        wheel = build_wheel(
            Path(args.repo_root).resolve(),
            Path(args.output_dir).resolve(),
            args.python,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Envoy CLI wheel build failed: {exc}", file=sys.stderr)
        return 2
    print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
