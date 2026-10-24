#!/usr/bin/env python3
"""Keep every shipped version declaration in lockstep with the CLI package.

The release version is written by hand into a dozen manifests (two
``pyproject.toml`` files, three plugin/marketplace manifests, the DSH bundle, the
skill packager, and two test constants). Nothing used to compare them, so a
partial bump shipped mismatched artifacts. ``--check`` exits non-zero on any
drift and CI runs it; ``--write`` bumps every declaration from the
authoritative CLI package instead of doing it by hand.

Usage:
    python scripts/check_versions.py           # human-readable inventory
    python scripts/check_versions.py --check   # exit 1 on drift
    python scripts/check_versions.py --write   # rewrite declarations from the CLI package
    python scripts/check_versions.py --json    # machine-readable report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# packages/envoy-cli is the source of truth: both release workflows read it.
CLI_PYPROJECT = "packages/envoy-cli/pyproject.toml"

_TOML_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_PY_VERSION = re.compile(r'^(?:PACKAGE_VERSION|SKILL_VERSION)\s*=\s*"([^"]+)"', re.MULTILINE)
_CORE_VERSION = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_CORE_VERSION_ASSERT = re.compile(r'envoy_core\.__version__\s*==\s*"([^"]+)"')


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _toml(relative: str) -> str:
    match = _TOML_VERSION.search(_read(relative))
    if match is None:
        raise ValueError(f"no [project] version in {relative}")
    return match.group(1)


def _json(relative: str, *keys: str) -> str:
    node = json.loads(_read(relative))
    for key in keys:
        node = node[key]
    return node


def _regex(relative: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(_read(relative))
    if match is None:
        raise ValueError(f"no version constant in {relative}")
    return match.group(1)


# Manifests that ship to users, marketplaces, or the packagers.
DECLARATIONS: tuple[tuple[str, callable], ...] = (
    ("packages/envoy-core/pyproject.toml", _toml),
    ("packages/envoy-core/src/envoy_core/__init__.py", lambda p: _regex(p, _CORE_VERSION)),
    (
        "integrations/claude-code/envoy/.claude-plugin/plugin.json",
        lambda p: _json(p, "version"),
    ),
    (
        "integrations/codex/envoy/.codex-plugin/plugin.json",
        lambda p: _json(p, "version"),
    ),
    (
        ".claude-plugin/marketplace.json",
        lambda p: _json(p, "plugins", 0, "version"),
    ),
    ("integrations/dsh/envoy-dsh/package.json", lambda p: _json(p, "version")),
    ("integrations/dsh/envoy-dsh/skill/manifest.json", lambda p: _json(p, "version")),
    ("scripts/package_envoy_skill.py", lambda p: _regex(p, _PY_VERSION)),
)

# Tests pin the version on purpose; a bump must update them too.
TEST_CONSTANTS: tuple[tuple[str, callable], ...] = (
    ("tests/test_envoy_cli_distribution.py", lambda p: _regex(p, _PY_VERSION)),
    ("tests/core_protocol/test_read_protocol.py", lambda p: _regex(p, _CORE_VERSION_ASSERT)),
)

# User-facing docs must mention the current release. Historical notes under
# docs/ and CHANGELOG.md are snapshots, so they are intentionally excluded.
DOC_MENTIONS: tuple[str, ...] = (
    "integrations/skills/envoy/INSTALL.md",
    "integrations/dsh/README.md",
)

# How ``--write`` patches a declaration: the pattern spans the whole assignment
# and ``count=1`` keeps look-alike keys (a nested plugin version, a dependency
# pin) untouched. The replacement restores the captured separator and splices in
# the authoritative version.
_PATCHES: dict[str, tuple[re.Pattern[str], str]] = {
    CLI_PYPROJECT: (re.compile(r'(?m)^version(\s*=\s*)"[^"]+"'), 'version\\1"{v}"'),
    "packages/envoy-core/pyproject.toml": (
        re.compile(r'(?m)^version(\s*=\s*)"[^"]+"'),
        'version\\1"{v}"',
    ),
    "packages/envoy-core/src/envoy_core/__init__.py": (
        re.compile(r'(?m)^__version__(\s*=\s*)"[^"]+"'),
        '__version__\\1"{v}"',
    ),
    "integrations/claude-code/envoy/.claude-plugin/plugin.json": (
        re.compile(r'"version"(\s*:\s*)"[^"]+"'),
        '"version"\\1"{v}"',
    ),
    "integrations/codex/envoy/.codex-plugin/plugin.json": (
        re.compile(r'"version"(\s*:\s*)"[^"]+"'),
        '"version"\\1"{v}"',
    ),
    ".claude-plugin/marketplace.json": (
        re.compile(r'"version"(\s*:\s*)"[^"]+"'),
        '"version"\\1"{v}"',
    ),
    "integrations/dsh/envoy-dsh/package.json": (
        re.compile(r'"version"(\s*:\s*)"[^"]+"'),
        '"version"\\1"{v}"',
    ),
    "integrations/dsh/envoy-dsh/skill/manifest.json": (
        re.compile(r'"version"(\s*:\s*)"[^"]+"'),
        '"version"\\1"{v}"',
    ),
    "scripts/package_envoy_skill.py": (
        re.compile(r'(?m)^SKILL_VERSION(\s*=\s*)"[^"]+"'),
        'SKILL_VERSION\\1"{v}"',
    ),
    "tests/test_envoy_cli_distribution.py": (
        re.compile(r'(?m)^PACKAGE_VERSION(\s*=\s*)"[^"]+"'),
        'PACKAGE_VERSION\\1"{v}"',
    ),
    "tests/core_protocol/test_read_protocol.py": (
        re.compile(r'(envoy_core\.__version__\s*==\s*)"[^"]+"'),
        '\\1"{v}"',
    ),
}


def authoritative_version() -> str:
    return _toml(CLI_PYPROJECT)


def collect() -> list[dict]:
    """Return one row per declaration, with any read failure kept as an error."""

    rows: list[dict] = []
    for group, table in (("manifest", DECLARATIONS), ("test", TEST_CONSTANTS)):
        for relative, reader in table:
            try:
                rows.append({"kind": group, "path": relative, "version": reader(relative)})
            except Exception as exc:  # missing file, bad JSON, renamed key
                rows.append({"kind": group, "path": relative, "version": None, "error": str(exc)})
    return rows


def find_drift(rows: list[dict], expected: str) -> list[dict]:
    """Pure comparison so the reporting rule is testable without the repo."""

    problems = []
    for row in rows:
        if row.get("error"):
            problems.append({**row, "problem": "unreadable"})
        elif row["version"] != expected:
            problems.append({**row, "problem": "drift"})
    return problems


def find_doc_gaps(expected: str) -> list[str]:
    missing = []
    for relative in DOC_MENTIONS:
        if expected not in _read(relative):
            missing.append(relative)
    return missing


def write_declarations(expected: str) -> list[str]:
    """Rewrite every declaration to ``expected`` and return the paths touched.

    Docs are not rewritten: their prose is authored by hand, so a missing
    mention stays a reported gap rather than a silent edit.
    """

    changed: list[str] = []
    for relative, (pattern, template) in _PATCHES.items():
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        patched, replacements = pattern.subn(template.format(v=expected), text, count=1)
        if replacements and patched != text:
            path.write_text(patched, encoding="utf-8")
            changed.append(relative)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when anything drifts")
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite every declaration from the CLI package version",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    expected = authoritative_version()
    written = write_declarations(expected) if args.write else []
    rows = collect()
    drift = find_drift(rows, expected)
    doc_gaps = find_doc_gaps(expected)
    ok = not drift and not doc_gaps

    if args.json:
        print(json.dumps({
            "expected": expected,
            "source": CLI_PYPROJECT,
            "ok": ok,
            "written": written,
            "declarations": rows,
            "drift": drift,
            "docs_missing_version": doc_gaps,
        }, ensure_ascii=False))
    else:
        print(f"authoritative version: {expected} ({CLI_PYPROJECT})")
        for relative in written:
            print(f"  WROTE  {relative}")
        for row in rows:
            if row.get("error"):
                print(f"  ERROR  {row['path']}: {row['error']}")
            elif row["version"] != expected:
                print(f"  DRIFT  {row['path']}: {row['version']} != {expected}")
        for relative in doc_gaps:
            print(f"  DRIFT  {relative}: does not mention {expected}")
        if ok:
            print(f"all {len(rows)} version declarations match ({len(DOC_MENTIONS)} docs checked)")
        else:
            print("run: update every declaration above and re-run --check", file=sys.stderr)

    if args.check and not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
