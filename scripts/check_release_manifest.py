#!/usr/bin/env python3
"""Keep the install/upgrade contract in one place and emit the release copy.

``integrations/release-manifest.json`` is the single declaration of what a host
installs: the Skill/plugin version, the minimum compatible CLI runtime, and the
protocol versions. Three consumers read it in different forms:

* the DSH bundle launcher cannot parse JSON in a shell one-liner, so its pinned
  version and required runtime are literals in ``cordis.patch.yml`` and have to
  match the manifest (``--check`` fails on drift, ``--write`` rewrites them);
* ``release.yml`` calls ``--emit`` to write the published copy, which adds the
  source commit and the sha256 of every shipped artifact, so an installed
  runtime can be traced back to the commit and bytes that produced it;
* CI fails on any mismatch between the manifest, the CLI package version, the
  bundled DSH plugin, and the protocol reference.

Usage:
    python scripts/check_release_manifest.py --check
    python scripts/check_release_manifest.py --write
    python scripts/check_release_manifest.py --emit dist/release-manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "integrations" / "release-manifest.json"
CLI_PYPROJECT = ROOT / "packages" / "envoy-cli" / "pyproject.toml"
DSH_PACKAGE = ROOT / "integrations" / "dsh" / "envoy-dsh" / "package.json"
DSH_PATCH = ROOT / "integrations" / "dsh" / "envoy-dsh" / "cordis.patch.yml"
PROTOCOL_DOC = ROOT / "integrations" / "skills" / "envoy" / "references" / "protocol.md"

PATCH_VERSION_RE = re.compile(r'V="\$\{ENVOY_DSH_VERSION:-([^}]+)\}"')
PATCH_REQUIRED_RE = re.compile(r'REQ="\$\{ENVOY_DSH_REQUIRED_RUNTIME:-(\$V|[^}]+)\}"')
_VERSION_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _cli_version() -> str:
    # Regex instead of tomllib: the check also runs on Python 3.9/3.10 hosts,
    # and CI already pins the authoritative version format.
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', CLI_PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE
    )
    if match is None:
        raise SystemExit(f"no [project] version in {CLI_PYPROJECT}")
    return match.group(1)


def _dsh_version() -> str:
    return json.loads(DSH_PACKAGE.read_text(encoding="utf-8"))["version"]


def _patch_pins() -> tuple[str | None, str | None]:
    text = DSH_PATCH.read_text(encoding="utf-8")
    version = PATCH_VERSION_RE.search(text)
    required = PATCH_REQUIRED_RE.search(text)
    return (
        version.group(1) if version else None,
        required.group(1) if required else None,
    )


def _normalised_required(pin: str | None) -> str | None:
    """``$V`` means "same as the provisioned version"."""

    return None if pin == "$V" else pin


def problems() -> list[str]:
    manifest = _manifest()
    version = str(manifest.get("version", ""))
    min_runtime = str(manifest.get("min_runtime", ""))
    protocols = manifest.get("protocols") or {}
    cli_version = _cli_version()
    out: list[str] = []

    if manifest.get("schema") != "envoy.release-manifest/1":
        out.append(f"schema must be envoy.release-manifest/1, got {manifest.get('schema')!r}")
    for name, value in (("version", version), ("min_runtime", min_runtime)):
        if not _VERSION_RE.fullmatch(value or ""):
            out.append(f"{name} must be a stable x.y.z version, got {value!r}")
    if version and version != cli_version:
        out.append(f"manifest version {version} != envoy-cli pyproject {cli_version}")
    if version and min_runtime != version:
        out.append(
            f"min_runtime {min_runtime} != version {version}; a new CLI may raise the "
            "floor only together with a manifest bump"
        )
    if version and _dsh_version() != version:
        out.append(f"DSH plugin version {_dsh_version()} != manifest version {version}")

    protocol_text = PROTOCOL_DOC.read_text(encoding="utf-8")
    if set(protocols) != {"read", "observe"}:
        out.append(f"protocols must declare read and observe, got {sorted(protocols)}")
    for name, declared in protocols.items():
        if declared not in protocol_text:
            out.append(
                f"protocol {name}={declared} is not documented in {PROTOCOL_DOC.relative_to(ROOT)}"
            )

    pinned_version, pinned_required = _patch_pins()
    if pinned_version is None:
        out.append("cordis.patch.yml lost its ENVOY_DSH_VERSION default")
    elif pinned_version != version:
        out.append(f"cordis.patch.yml provisions {pinned_version}, manifest says {version}")
    if pinned_required is None:
        out.append("cordis.patch.yml lost its ENVOY_DSH_REQUIRED_RUNTIME default")
    elif _normalised_required(pinned_required) not in (None, min_runtime):
        out.append(
            f"cordis.patch.yml requires {pinned_required}, manifest min_runtime is {min_runtime}"
        )
    return out


def check() -> int:
    found = problems()
    if found:
        for line in found:
            print(f"release manifest: {line}", file=sys.stderr)
        print("run: python scripts/check_release_manifest.py --write", file=sys.stderr)
        return 1
    manifest = _manifest()
    print(
        "release manifest is current "
        f"(Envoy {manifest['version']}, runtime >= {manifest['min_runtime']}, "
        f"protocols {', '.join(sorted(manifest['protocols'].values()))})"
    )
    return 0


def write() -> int:
    """Align the manifest and the shell pins with the authoritative CLI version."""

    version = _cli_version()
    manifest = _manifest()
    manifest["version"] = version
    manifest["min_runtime"] = version
    manifest.setdefault("schema", "envoy.release-manifest/1")
    manifest.setdefault("protocols", {"read": "envoy.read/1", "observe": "envoy.observe/1"})
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT).as_posix()} (version {version})")

    text = DSH_PATCH.read_text(encoding="utf-8")
    text = PATCH_VERSION_RE.sub(f'V="${{ENVOY_DSH_VERSION:-{version}}}"', text, count=1)
    text = PATCH_REQUIRED_RE.sub('REQ="${ENVOY_DSH_REQUIRED_RUNTIME:-$V}"', text, count=1)
    DSH_PATCH.write_text(text, encoding="utf-8")
    print(f"wrote {DSH_PATCH.relative_to(ROOT).as_posix()} (provisions {version})")
    return 0


def emit(output: Path) -> int:
    """Write the published copy: requirements plus commit and artifact hashes."""

    code = check()
    if code:
        return code
    if not output.parent.is_dir():
        output.parent.mkdir(parents=True, exist_ok=True)
    payload = _manifest()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    payload["source_commit"] = commit.stdout.strip() if commit.returncode == 0 else None
    artifacts = []
    target = output.resolve()
    for path in sorted((ROOT / "dist").glob("*")):
        if path.is_file() and path.resolve() != target:
            artifacts.append(
                {
                    "name": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    payload["artifacts"] = artifacts
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} ({len(artifacts)} artifacts, commit {payload['source_commit']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="fail on any drift")
    group.add_argument(
        "--write", action="store_true", help="rewrite declarations from the CLI version"
    )
    group.add_argument("--emit", type=Path, metavar="PATH", help="write the released manifest")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.write:
        return write()
    return emit(args.emit)


if __name__ == "__main__":
    raise SystemExit(main())
