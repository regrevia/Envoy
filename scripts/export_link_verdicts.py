#!/usr/bin/env python3
"""Export the Python link-verdict rules as JSON for the DSH plugin.

`envoy_cli.platforms.read_support_verdict()` is the single source of truth for
the supported / unsupported / unknown verdict. The DeepSeek Harness bundle is
plain JavaScript and cannot import Python, so this script copies the declarative
parts of the rules (host sets, path regexes, file suffixes, literal host groups)
into `integrations/dsh/envoy-dsh/link-verdicts.json`. The JavaScript side only
does the same host/path matching, never a second copy of the tables.

Run with the same environment as the tests:

    uv run --python 3.11 --with-editable packages/envoy-core \
        --with-editable packages/envoy-cli python scripts/export_link_verdicts.py

`--check` regenerates in memory and exits 1 when the committed JSON is stale,
so CI fails when a Python rule changes without a regenerated bundle.
"""

from __future__ import annotations

import argparse
import difflib
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "integrations" / "dsh" / "envoy-dsh" / "link-verdicts.json"

_HOST_LITERAL = re.compile(r'host\s+(?:in\s+\{([^}]*)\}|==\s*"([^"]+)")')
_REGEX_REF = re.compile(r"\b(_[A-Z0-9_]+_RE)\.match\(path\)")


def _load_platforms():
    for package in ("envoy-core", "envoy-cli"):
        source = ROOT / "packages" / package / "src"
        if source.is_dir() and str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from envoy_cli import platforms

    return platforms


def _literal_path_rules(platforms) -> list[dict[str, object]]:
    """Host literals compared in the verdict function, paired with their regex.

    `read_support_verdict()` writes three anonymous login walls inline, e.g.
    ``if host in {"t.me", "telegram.me"} and _TME_PRIVATE_RE.match(path)``.
    Reading them from the function source keeps this export honest instead of
    duplicating the host lists here.
    """

    rules: list[dict[str, object]] = []
    for line in inspect.getsource(platforms.read_support_verdict).splitlines():
        literal = _HOST_LITERAL.search(line)
        reference = _REGEX_REF.search(line)
        if literal is None or reference is None:
            continue
        if literal.group(1) is not None:
            hosts = [part.strip().strip("\"'") for part in literal.group(1).split(",")]
        else:
            hosts = [literal.group(2)]
        pattern = getattr(platforms, reference.group(1)).pattern
        rules.append({"hosts": sorted({host for host in hosts if host}), "path": pattern})
    return rules


def build_payload(platforms) -> dict[str, object]:
    return {
        "generated_by": "scripts/export_link_verdicts.py",
        "version": 1,
        "patterns": {
            "douyin_video": platforms._DOUYIN_VIDEO_RE.pattern,
            "douyin_note": platforms._DOUYIN_NOTE_RE.pattern,
            "douyin_live": platforms._DOUYIN_LIVE_RE.pattern,
        },
        "supported": {
            "hosts": sorted(platforms._SUPPORTED_HOSTS),
            "host_suffixes": sorted(platforms._SUPPORTED_HOST_SUFFIXES),
            "file_suffixes": sorted(platforms._SUPPORTED_FILE_SUFFIXES),
            "douyin_hosts": sorted(platforms._DOUYIN_READ_HOSTS),
        },
        "unsupported": {
            "hosts": sorted(platforms._UNSUPPORTED_HOSTS),
            "douyin_live_hosts": sorted(platforms._DOUYIN_LIVE_HOSTS),
            "path_rules": _literal_path_rules(platforms),
        },
    }


def render(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when link-verdicts.json is stale instead of writing it",
    )
    args = parser.parse_args(argv)

    expected = render(build_payload(_load_platforms()))
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if args.check:
        if current == expected:
            print(f"{OUTPUT.relative_to(ROOT)} is up to date")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(OUTPUT.relative_to(ROOT)),
            tofile=f"{OUTPUT.relative_to(ROOT)} (regenerated)",
        )
        sys.stderr.writelines(diff)
        print(
            f"ERROR: {OUTPUT.relative_to(ROOT)} is stale; run "
            "scripts/export_link_verdicts.py with the command in its docstring",
            file=sys.stderr,
        )
        return 1

    if current == expected:
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
