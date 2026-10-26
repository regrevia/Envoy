#!/usr/bin/env python3
"""Build a credential-free Envoy Skill bundle from the repository."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


SKILL_VERSION = "0.1.12"
REQUIRED_FILES = (
    "SKILL.md",
    "INSTALL.md",
    "references/protocol.md",
)
FORBIDDEN_PARTS = {
    ".env",
    ".env.local",
    "cookies",
    "credentials",
    "secrets",
    "browser-profile",
}
FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".key", ".pem", ".enc")


def _safe_members(skill_root: Path) -> list[Path]:
    members = []
    paths = sorted(
        skill_root.rglob("*"),
        key=lambda item: item.relative_to(skill_root).as_posix().casefold(),
    )
    for path in paths:
        if not path.is_file():
            continue
        relative = path.relative_to(skill_root)
        parts = {part.lower() for part in relative.parts}
        lowered = str(relative).lower()
        if parts & FORBIDDEN_PARTS or lowered.endswith(FORBIDDEN_SUFFIXES):
            raise ValueError(f"refusing sensitive Skill member: {relative}")
        members.append(path)
    return members


def build_bundle(repo_root: Path, output: Path, version: str = SKILL_VERSION) -> dict:
    skill_root = repo_root / "integrations" / "skills" / "envoy"
    if not skill_root.is_dir():
        raise FileNotFoundError(f"Skill directory not found: {skill_root}")
    missing = [name for name in REQUIRED_FILES if not (skill_root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Skill files missing: {', '.join(missing)}")

    members = _safe_members(skill_root)
    manifest = {
        "schema_version": "envoy.skill.bundle/1",
        "name": "envoy",
        "license": "Apache-2.0",
        "version": version,
        "entrypoint": "SKILL.md",
        "installation": "INSTALL.md",
        "references": ["references/protocol.md"],
        "mcp": {"command": "envoy", "args": ["mcp"]},
        "protocol": "envoy.read/1",
        "credentials_included": False,
        "source": "public_links_only",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest_info = zipfile.ZipInfo("envoy-skill.json")
        manifest_info.date_time = (2020, 1, 1, 0, 0, 0)
        manifest_info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(manifest_info, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for path in members:
            relative = path.relative_to(skill_root).as_posix()
            info = zipfile.ZipInfo(relative)
            info.date_time = (2020, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return {
        "output": str(output),
        "manifest": manifest,
        "files": ["envoy-skill.json", *[path.relative_to(skill_root).as_posix() for path in members]],
        "size_bytes": output.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package the credential-free Envoy Skill")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", default=SKILL_VERSION)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_bundle(
            Path(args.repo_root).resolve(),
            Path(args.output).resolve(),
            args.version,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
