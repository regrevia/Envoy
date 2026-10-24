#!/usr/bin/env python3
"""Copy the canonical Envoy skill into the plugin bundles that auto-load it.

Claude Code and Codex both read a `skills/` directory from an installed plugin,
so the workflow guidance travels with the plugin instead of depending on a
second `npx skills add` step the user can forget. The canonical file stays the
single source of truth in `integrations/skills/envoy`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "integrations" / "skills" / "envoy"
MEMBERS = ("SKILL.md", "references/protocol.md")
TARGETS = (
    ROOT / "integrations" / "claude-code" / "envoy" / "skills" / "envoy",
    ROOT / "integrations" / "codex" / "envoy" / "skills" / "envoy",
)


def render() -> dict[Path, str]:
    """Return the exact bytes each bundled skill file must contain."""

    payload: dict[Path, str] = {}
    for target in TARGETS:
        for member in MEMBERS:
            source = CANONICAL / member
            if not source.is_file():
                raise SystemExit(f"canonical skill member missing: {source}")
            payload[target / member] = source.read_text(encoding="utf-8")
    return payload


def check() -> int:
    stale = []
    for path, text in render().items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            stale.append(path)
    if stale:
        for path in stale:
            print(f"stale bundled skill: {path.relative_to(ROOT).as_posix()}", file=sys.stderr)
        print("run: python scripts/sync_host_skills.py", file=sys.stderr)
        return 1
    print(f"bundled host skills are current ({len(render())} files)")
    return 0


def write() -> int:
    for path, text in render().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when a bundled copy is stale")
    args = parser.parse_args(argv)
    return check() if args.check else write()


if __name__ == "__main__":
    raise SystemExit(main())
