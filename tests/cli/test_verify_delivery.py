"""Checks for scripts/verify_delivery.py, the pre-install delivery gate."""

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import zipfile
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT = ROOT_DIR / "scripts" / "verify_delivery.py"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TOOL = Path(__file__).resolve().parents[2] / "scripts" / "dry_run_experiments.py"
VERSION = "0.1.24"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_delivery", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wheel(path: Path, version: str = VERSION, deps: bool = True) -> None:
    metadata = f"Metadata-Version: 2.1\nName: envoy-cli\nVersion: {version}\n"
    if deps:
        metadata += "Requires-Dist: httpx<1,>=0.28\nRequires-Dist: pyyaml\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"envoy_cli-{version}.dist-info/METADATA", metadata)
        archive.writestr("envoy_cli/__init__.py", "")
        archive.writestr("envoy_core/__init__.py", "")


def write_tgz(path: Path, version: str = VERSION) -> None:
    payload = json.dumps({"name": "envoy-dsh", "version": version}).encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def write_skill(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("SKILL.md", "# Envoy\n")
        archive.writestr("references/install.md", "x\n")


def write_plan(directory: Path, entries: list[dict] | None = None, status: str = "not_run") -> None:
    plan = {
        "schema": "envoy.experiment-plan/1",
        "version": VERSION,
        "status": status,
        "axes": {"long-video": {"entries": entries or [{"id": "V01", "status": "not_run"}]}},
    }
    (directory / "EXPERIMENT-PLAN.json").write_text(json.dumps(plan, indent=2) + "\n")


def build_delivery(directory: Path) -> None:
    write_plan(directory)
    (directory / "dry_run_experiments.py").write_text(TOOL.read_text(encoding="utf-8"))
    write_wheel(directory / f"envoy_cli-{VERSION}-py3-none-any.whl")
    write_tgz(directory / f"envoy-dsh-{VERSION}.tgz")
    write_skill(directory / "envoy-skill.zip")
    (directory / "install.sh").write_text(
        "#!/bin/sh\nset -eu\nurl=release-manifest.json\n"
        'wheel="envoy_cli-$version-py3-none-any.whl"\n'
    )
    (directory / "install.ps1").write_text(
        "param([string]$Target)\n$ManifestUrl = 'release-manifest.json'\n"
        '$wheel = "envoy_cli-$Version-py3-none-any.whl"\n'
    )
    artifacts = []
    for name in (
        f"envoy_cli-{VERSION}-py3-none-any.whl",
        f"envoy-dsh-{VERSION}.tgz",
        "envoy-skill.zip",
        "install.sh",
        "install.ps1",
    ):
        artifacts.append({"name": name, "sha256": sha256((directory / name).read_bytes()).hexdigest()})
    manifest = {
        "version": VERSION,
        "min_runtime": VERSION,
        "source_commit": COMMIT,
        "artifacts": artifacts,
    }
    (directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lines = [f"{a['sha256']}  {a['name']}" for a in artifacts]
    lines.append(
        f"{sha256((directory / 'release-manifest.json').read_bytes()).hexdigest()}  release-manifest.json"
    )
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def refresh_checksums(directory: Path, manifest: dict) -> None:
    lines = [f"{a['sha256']}  {a['name']}" for a in manifest["artifacts"]]
    lines.append(
        f"{sha256((directory / 'release-manifest.json').read_bytes()).hexdigest()}  release-manifest.json"
    )
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def switch_to_preview_wheel(directory: Path) -> str:
    (directory / f"envoy_cli-{VERSION}-py3-none-any.whl").unlink()
    preview_version = f"{VERSION}+preview.gabc.20261028"
    name = f"envoy_cli-{preview_version}-py3-none-any.whl"
    write_wheel(directory / name, version=preview_version)
    manifest = json.loads((directory / "release-manifest.json").read_text())
    manifest["artifacts"][0]["name"] = name
    manifest["artifacts"][0]["sha256"] = sha256((directory / name).read_bytes()).hexdigest()
    (directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    refresh_checksums(directory, manifest)
    return name


def run(directory: Path, *extra: str) -> tuple[int, str]:
    module = load_module()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = module.main(["--dir", str(directory), *extra])
    return code, buffer.getvalue()


def test_a_wheel_without_its_packages_is_refused(tmp_path: Path) -> None:
    directory = tmp_path
    build_delivery(directory)
    wheel = directory / f"envoy_cli-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"envoy_cli-{VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: envoy-cli\nVersion: {VERSION}\nRequires-Dist: httpx\n",
        )
    manifest = json.loads((directory / "release-manifest.json").read_text())
    manifest["artifacts"][0]["sha256"] = sha256(wheel.read_bytes()).hexdigest()
    (directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    refresh_checksums(directory, manifest)
    code, output = run(directory)
    assert code == 1, output
    assert "cannot import offline" in output, output


def test_good_delivery_passes(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    code, output = run(tmp_path)
    assert code == 0, output
    assert "delivery ok" in output
    assert "wheel is a release build" in output
    assert "resolves the version from the release manifest" in output


def test_tampered_artifact_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    with (tmp_path / "envoy-skill.zip").open("ab") as handle:
        handle.write(b"x")
    code, output = run(tmp_path)
    assert code == 1
    assert "digest mismatch" in output


def test_preview_wheel_is_rejected_by_default(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    switch_to_preview_wheel(tmp_path)
    code, output = run(tmp_path)
    assert code == 1, output
    assert "not a release build" in output


def test_preview_wheel_passes_with_allow_preview(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    switch_to_preview_wheel(tmp_path)
    code, output = run(tmp_path, "--allow-preview")
    assert code == 0, output
    assert "local preview build" in output


def test_wheel_without_runtime_dependency_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    wheel = tmp_path / f"envoy_cli-{VERSION}-py3-none-any.whl"
    write_wheel(wheel, deps=False)
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    for artifact in manifest["artifacts"]:
        if artifact["name"] == wheel.name:
            artifact["sha256"] = sha256(wheel.read_bytes()).hexdigest()
    (tmp_path / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    lines = [f"{a['sha256']}  {a['name']}" for a in manifest["artifacts"]]
    lines.append(
        f"{sha256((tmp_path / 'release-manifest.json').read_bytes()).hexdigest()}  release-manifest.json"
    )
    (tmp_path / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    code, output = run(tmp_path)
    assert code == 1, output
    assert "does not declare httpx" in output


def test_missing_checksums_reports_failure(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    (tmp_path / "SHA256SUMS").unlink()
    code, output = run(tmp_path)
    assert code == 1
    assert "SHA256SUMS: missing" in output


def test_plan_claiming_a_result_without_evidence_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    write_plan(
        tmp_path,
        [{"id": "V01", "status": "passed"}],
        status="passed",
    )
    code, output = run(tmp_path)
    assert code == 1, output
    assert "V01 is passed without an evidence line" in output


def test_plan_with_duplicate_ids_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    write_plan(
        tmp_path,
        [{"id": "V01", "status": "not_run"}, {"id": "V01", "status": "not_run"}],
    )
    code, output = run(tmp_path)
    assert code == 1, output
    assert "duplicate entry id 'V01'" in output


def test_recorded_plan_with_evidence_passes(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    write_plan(
        tmp_path,
        [
            {
                "id": "V01",
                "status": "passed",
                "evidence": "envoy read <url> -> success, 412 words",
                "observed_at": "2026-10-28T09:10:00+08:00",
            }
        ],
        status="passed",
    )
    code, output = run(tmp_path)
    assert code == 0, output
    assert "experiment-plan is valid (1 entries, 1 axes)" in output


def test_plan_command_naming_a_flag_the_cli_never_had_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    write_plan(tmp_path, [{"id": "D07", "status": "not_run", "command": "envoy read a.pdf --pages 10-25"}])
    code, out = run(tmp_path)
    assert code == 1
    assert "experiment-plan-commands" in out
    assert "--pages" in out


def test_plan_command_on_the_real_cli_surface_passes(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    command = (
        "sh -c 'printf %s \"{\\\"request_id\\\":\\\"V01\\\"}\" | envoy read --request-json -'"
    )
    write_plan(tmp_path, [{"id": "V01", "status": "not_run", "command": command}])
    code, out = run(tmp_path)
    assert code == 0
    assert "commands match the CLI surface (1 checked)" in out


def test_plan_gated_on_an_entry_that_does_not_exist_fails(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    path = tmp_path / "EXPERIMENT-PLAN.json"
    plan = json.loads(path.read_text())
    plan["holdout"] = {
        "id": "H12",
        "status": "pending_sample",
        "size": 12,
        "dependent_entries": ["V01", "V02"],
    }
    path.write_text(json.dumps(plan, indent=2) + "\n")
    code, output = run(tmp_path)
    assert code == 1, output
    assert "names unknown entries: V02" in output


def test_plan_gated_on_real_entries_passes(tmp_path: Path) -> None:
    build_delivery(tmp_path)
    path = tmp_path / "EXPERIMENT-PLAN.json"
    plan = json.loads(path.read_text())
    plan["holdout"] = {
        "id": "H12",
        "status": "pending_sample",
        "size": 12,
        "dependent_entries": ["V01"],
    }
    path.write_text(json.dumps(plan, indent=2) + "\n")
    code, output = run(tmp_path)
    assert code == 0, output
