#!/usr/bin/env python3
"""Check a staged delivery directory before a human installs anything.

The release workflow builds artifacts, writes ``release-manifest.json`` and
``SHA256SUMS``. This script re-derives every cross-reference a person would
otherwise check by hand, without network access and without installing the
wheel:

* every artifact named in ``SHA256SUMS`` exists and has the recorded digest;
* every artifact named in the manifest exists, has the recorded digest, and
  matches the file names and version in the delivery directory;
* the wheel is a ``release`` build (no ``+preview`` marker) and declares the
  runtime dependencies an offline install needs;
* the DSH bundle's ``package.json`` version equals the manifest version;
* the Skill zip contains ``SKILL.md``;
* both installers mention the manifest version in their download URLs;
* ``EXPERIMENT-PLAN.json`` is schema-valid and no entry claims a result it did
  not record (a terminal status without evidence fails the delivery).

Usage:
    python scripts/verify_delivery.py --dir <delivery-dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from pathlib import Path

DOC_FILES = {
    "COMPATIBILITY.md",
    "verify_delivery.py",
    "DEVELOPMENT-REPORT.md",
    "EXPERIMENT-PLAN.json",
    "RUN-WHEN-PRESENT.md",
}
SUMS = "SHA256SUMS"
MANIFEST = "release-manifest.json"
WHEEL_PATTERN = re.compile(r"^envoy_cli-(?P<version>[0-9][^-]*)-py3-none-any\.whl$")
TGZ_PATTERN = re.compile(r"^envoy-dsh-(?P<version>[0-9].*)\.tgz$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Result:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.failures: list[str] = []
        self.checks = 0

    def ok(self, name: str) -> None:
        self.checks += 1
        print(f"ok   {name}")

    def fail(self, name: str, detail: str) -> None:
        self.checks += 1
        self.failures.append(f"{name}: {detail}")
        print(f"FAIL {name}: {detail}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or not SHA256.match(parts[0]):
            raise ValueError(f"malformed SHA256SUMS line: {line!r}")
        entries[parts[1].lstrip("*")] = parts[0]
    return entries


MODULES = ("envoy_cli/", "envoy_core/")


def wheel_modules(path: Path) -> list[str]:
    """Top-level importable packages carried by the wheel."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return [module for module in MODULES if any(n.startswith(module) for n in names)]


def wheel_metadata(path: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith(".dist-info/METADATA"):
                text = archive.read(name).decode("utf-8", "replace")
                version = ""
                requires: list[str] = []
                for line in text.splitlines():
                    if line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                    elif line.startswith("Requires-Dist:"):
                        requires.append(line.split(":", 1)[1].strip())
                return version, requires
    raise ValueError("wheel has no dist-info/METADATA")


def tgz_package(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name == "package/package.json":
                raw = archive.extractfile(member)
                assert raw is not None
                return json.loads(raw.read().decode("utf-8"))
    raise ValueError("tarball has no package/package.json")


def single(pattern: re.Pattern[str], names: list[str], label: str) -> tuple[str, str]:
    matches = [pattern.match(name) for name in names]
    hits = [(name, match) for name, match in zip(names, matches) if match]
    if len(hits) != 1:
        raise ValueError(f"expected exactly one {label} in delivery directory, found {len(hits)}")
    name, match = hits[0]
    assert match is not None
    return name, match.group("version")


PLAN_SCHEMA = "envoy.experiment-plan/1"
PLAN_STATUSES = {"not_run", "running", "passed", "failed", "blocked", "skipped"}


def plan_problem(plan: dict) -> str:
    """Return why an experiment plan is not ship-safe, or "" when it is."""
    if plan.get("schema") != PLAN_SCHEMA:
        return f"schema is {plan.get('schema')!r}, expected {PLAN_SCHEMA!r}"
    axes = plan.get("axes")
    if not isinstance(axes, dict) or not axes:
        return "axes is empty"
    seen: set[str] = set()
    every: list[dict] = []
    for axis_name, axis in axes.items():
        entries = axis.get("entries") if isinstance(axis, dict) else None
        if not isinstance(entries, list) or not entries:
            return f"axis {axis_name!r} has no entries"
        for entry in entries:
            entry_id = str(entry.get("id", ""))
            if not entry_id:
                return f"axis {axis_name!r} has an entry without an id"
            if entry_id in seen:
                return f"duplicate entry id {entry_id!r}"
            seen.add(entry_id)
            every.append(entry)
            status = entry.get("status")
            if status not in PLAN_STATUSES:
                return f"{entry_id} has status {status!r}"
            if status in {"passed", "failed"}:
                if not str(entry.get("evidence", "")).strip():
                    return f"{entry_id} is {status} without an evidence line"
                if not str(entry.get("observed_at", "")).strip():
                    return f"{entry_id} is {status} without observed_at"
            if status == "not_run" and entry.get("evidence"):
                return f"{entry_id} is not_run but carries evidence"
    holdout = plan.get("holdout")
    if isinstance(holdout, dict):
        dependent = holdout.get("dependent_entries") or []
        if not isinstance(dependent, list):
            return "holdout.dependent_entries is not a list"
        unknown = [str(entry_id) for entry_id in dependent if str(entry_id) not in seen]
        if holdout.get("status") != "frozen":
            recorded = sorted(
                str(entry.get("id"))
                for entry in every
                if str(entry.get("id")) in {str(d) for d in dependent}
                and entry.get("status") in {"passed", "failed"}
            )
            if recorded:
                return (
                    f"holdout {holdout.get('id', '?')} is {holdout.get('status')!r} but "
                    f"{', '.join(recorded)} already carry a result"
                )
        if unknown:
            # A typo here silently ungates the measurement that was held back,
            # which is the one thing a holdout exists to prevent.
            return f"holdout.dependent_entries names unknown entries: {', '.join(unknown)}"
    if plan.get("status") not in PLAN_STATUSES:
        return f"plan status is {plan.get('status')!r}"
    return ""


def load_plan_tool(directory: Path):
    """Import the shipped dry-run tool so the plan is checked against one CLI surface."""
    import importlib.util

    path = directory / "dry_run_experiments.py"
    spec = importlib.util.spec_from_file_location("shipped_dry_run_experiments", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"cannot load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(directory: Path, *, allow_preview: bool = False) -> Result:
    result = Result(directory)

    sums_path = directory / SUMS
    manifest_path = directory / MANIFEST
    if not sums_path.is_file():
        result.fail(SUMS, "missing")
        return result
    if not manifest_path.is_file():
        result.fail(MANIFEST, "missing")
        return result

    try:
        sums = parse_sums(sums_path)
    except ValueError as exc:
        result.fail(SUMS, str(exc))
        return result
    result.ok(f"{SUMS} parses ({len(sums)} entries)")

    bad = [name for name, digest in sums.items() if not (directory / name).is_file()]
    if bad:
        result.fail(SUMS, f"listed files missing: {', '.join(sorted(bad))}")
    else:
        mismatched = [
            name for name, digest in sums.items() if sha256(directory / name) != digest
        ]
        if mismatched:
            result.fail(SUMS, f"digest mismatch: {', '.join(sorted(mismatched))}")
        else:
            result.ok(f"{SUMS} digests match ({len(sums)} files)")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        result.fail(MANIFEST, f"not JSON: {exc}")
        return result

    version = str(manifest.get("version", ""))
    commit = str(manifest.get("source_commit", ""))
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        result.fail(MANIFEST, f"version is not x.y.z: {version!r}")
    else:
        result.ok(f"{MANIFEST} version {version}")
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        result.ok(f"{MANIFEST} source_commit {commit[:12]}")
    else:
        result.fail(MANIFEST, f"source_commit is not a 40-hex git id: {commit!r}")

    artifacts = manifest.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        result.fail(MANIFEST, "artifacts list is empty")
    else:
        missing = [a.get("name") for a in artifacts if not (directory / str(a.get("name"))).is_file()]
        if missing:
            result.fail(MANIFEST, f"artifacts missing: {', '.join(map(str, missing))}")
        else:
            drifted = [
                a["name"] for a in artifacts if sha256(directory / str(a["name"])) != a.get("sha256")
            ]
            if drifted:
                result.fail(MANIFEST, f"artifact digests differ from manifest: {', '.join(drifted)}")
            else:
                result.ok(f"{MANIFEST} artifact digests match ({len(artifacts)})")
        declared = {str(a.get("name")) for a in artifacts}
        undeclared = sorted(
            name
            for name in sums
            if name not in declared
            and name not in DOC_FILES
            and name not in {SUMS, MANIFEST}
        )
        if undeclared:
            result.fail(MANIFEST, f"files in SHA256SUMS but not in manifest: {', '.join(undeclared)}")
        else:
            result.ok("manifest covers every hashed artifact")

    try:
        wheel_name, wheel_version = single(WHEEL_PATTERN, sorted(sums), "release wheel")
    except ValueError as exc:
        result.fail("wheel", str(exc))
        return result
    preview = "+" in wheel_version
    if preview and not allow_preview:
        result.fail("wheel", f"not a release build: {wheel_version}")
    elif version and wheel_version.split("+", 1)[0] != version:
        result.fail("wheel", f"file version {wheel_version} != manifest {version}")
    else:
        try:
            inner_version, requires = wheel_metadata(directory / wheel_name)
        except (ValueError, zipfile.BadZipFile) as exc:
            result.fail("wheel", str(exc))
        else:
            if inner_version != wheel_version:
                result.fail("wheel", f"METADATA version {inner_version} != file name {wheel_version}")
            elif not any(r.lower().startswith("httpx") for r in requires):
                result.fail("wheel", "wheel does not declare httpx, an offline install would fail")
            elif missing := [m for m in MODULES if m not in wheel_modules(directory / wheel_name)]:
                result.fail("wheel", f"wheel cannot import offline, missing {', '.join(missing)}")
            elif preview:
                result.ok(f"wheel is a local preview build ({inner_version}, {len(requires)} deps)")
            else:
                result.ok(f"wheel is a release build ({inner_version}, {len(requires)} deps)")

    try:
        tgz_name, tgz_version = single(TGZ_PATTERN, sorted(sums), "DSH bundle")
    except ValueError as exc:
        result.fail("dsh-bundle", str(exc))
    else:
        try:
            package = tgz_package(directory / tgz_name)
        except (ValueError, tarfile.TarError, json.JSONDecodeError) as exc:
            result.fail("dsh-bundle", str(exc))
        else:
            if str(package.get("version")) != (version or tgz_version):
                result.fail(
                    "dsh-bundle",
                    f"package.json version {package.get('version')!r} != manifest {version!r}",
                )
            elif not str(package.get("name", "")).startswith("envoy-dsh"):
                result.fail("dsh-bundle", f"unexpected package name {package.get('name')!r}")
            else:
                result.ok(f"dsh-bundle is {package.get('name')}@{package.get('version')}")

    skill = directory / "envoy-skill.zip"
    try:
        with zipfile.ZipFile(skill) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        result.fail("skill-zip", str(exc))
    else:
        if "SKILL.md" not in names:
            result.fail("skill-zip", "SKILL.md missing at the archive root")
        else:
            result.ok(f"skill-zip has SKILL.md ({len(names)} entries)")

    # Installers deliberately take the version from the published release
    # manifest instead of embedding it, so check that contract rather than a
    # literal version string.
    for installer, markers in (
        ("install.sh", ("set -eu", "release-manifest.json", "envoy_cli-$version-py3-none-any.whl")),
        ("install.ps1", ("param(", "release-manifest.json", "envoy_cli-$Version-py3-none-any.whl")),
    ):
        path = directory / installer
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            result.fail(installer, str(exc))
            continue
        missing = [marker for marker in markers if marker not in text]
        if missing:
            result.fail(installer, f"missing contract pieces: {', '.join(missing)}")
        else:
            result.ok(f"{installer} resolves the version from the release manifest")

    plan_path = directory / "EXPERIMENT-PLAN.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        result.fail("experiment-plan", str(exc))
    except ValueError as exc:
        result.fail("experiment-plan", f"not JSON: {exc}")
    else:
        problem = plan_problem(plan)
        if problem:
            result.fail("experiment-plan", problem)
        else:
            entries = [e for axis in plan["axes"].values() for e in axis["entries"]]
            result.ok(f"experiment-plan is valid ({len(entries)} entries, {len(plan['axes'])} axes)")
            # The plan tells a human what to run when they next have the machine to
            # themselves, so a command naming a flag this CLI never had is a defect
            # is only found when it is pasted, long after the release shipped.
            try:
                tool = load_plan_tool(directory)
            except Exception as exc:  # noqa: BLE001 - any import failure is a ship defect
                result.fail("experiment-plan-commands", f"cannot load dry_run_experiments.py: {exc}")
            else:
                commands = {
                    str(entry["id"]): str(entry["command"])
                    for entry in entries
                    if str(entry.get("command", "")).strip()
                }
                problems = [
                    problem
                    for entry_id, command in commands.items()
                    for problem in tool.cli_problems(entry_id, command)
                ]
                problems.extend(tool.holdout_problems(plan))
                if problems:
                    result.fail("experiment-plan-commands", "; ".join(problems[:3]))
                else:
                    result.ok(
                        f"experiment-plan commands match the CLI surface "
                        f"({len(commands)} checked, holdout commands too)"
                    )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a staged Envoy delivery directory.")
    parser.add_argument("--dir", default=".", help="delivery directory to check")
    parser.add_argument(
        "--allow-preview",
        action="store_true",
        help="accept a local-preview wheel (0.1.24+preview.g<rev>.<UTC>) instead of a release build",
    )
    args = parser.parse_args(argv)
    directory = Path(args.dir).expanduser().resolve()
    if not directory.is_dir():
        print(f"not a directory: {directory}", file=sys.stderr)
        return 2
    result = verify(directory, allow_preview=args.allow_preview)
    if result.failures:
        print(f"\n{len(result.failures)} of {result.checks} checks failed in {directory}")
        return 1
    print(f"\ndelivery ok: {directory} ({result.checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
