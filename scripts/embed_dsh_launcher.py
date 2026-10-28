#!/usr/bin/env python3
"""Embed ``integrations/dsh/envoy-dsh/launch.cjs`` into the bundle patch.

The DSH bundle runs its launcher through ``node -e <stub>`` because an MCP client
spawns the command with the host process cwd, so a package-relative path is not
reliable.  A cmd.exe command line is capped at 8191 characters and ``node`` on
PATH is often a ``.cmd`` shim that goes through cmd.exe, so the source travels
gzipped + base64: the stub is roughly a quarter of the source size.

The stub is generated, never hand-written.  After editing ``launch.cjs`` run::

    python scripts/embed_dsh_launcher.py --write

``--check`` exits 1 when the embedded payload no longer matches ``launch.cjs``.
Tests also assert byte equality after inflating; this script is the cheap gate.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "integrations/dsh/envoy-dsh/launch.cjs"
PATCH = ROOT / "integrations/dsh/envoy-dsh/cordis.patch.yml"
PAYLOAD = re.compile(r"(Buffer\.from\(')([A-Za-z0-9+/=]+)(')")
CMD_LIMIT = 8191


def encode(source: bytes) -> str:
    # mtime=0 keeps the payload byte-stable across rebuilds; the compressed bytes
    # themselves still differ between zlib builds, so only ever compare decoded
    # content (see ``matches``).
    return base64.b64encode(gzip.compress(source, 9, mtime=0)).decode("ascii")


def decode(payload: str) -> bytes:
    return gzip.decompress(base64.b64decode(payload))


def matches(payload: str, source: bytes) -> bool:
    """True when the embedded payload unpacks to exactly ``source``.

    Byte-comparing the base64 would make the check depend on the local zlib build:
    the same ``launch.cjs`` compresses to different bytes on macOS and on Linux.
    """
    try:
        return decode(payload) == source
    except (OSError, ValueError):
        return False


def current(patch_text: str) -> str:
    match = PAYLOAD.search(patch_text)
    if not match:
        raise SystemExit(f"{PATCH} has no embedded launcher payload")
    return match.group(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="rewrite the embedded payload")
    group.add_argument("--check", action="store_true", help="fail when the payload is stale")
    args = parser.parse_args(argv)

    source = LAUNCHER.read_bytes()
    wanted = encode(source)
    patch_text = PATCH.read_text(encoding="utf-8")
    have = current(patch_text)

    if args.check:
        if not matches(have, source):
            print(
                f"stale: embedded launcher payload in {PATCH.relative_to(ROOT)} does not match "
                f"{LAUNCHER.relative_to(ROOT)}; run python scripts/embed_dsh_launcher.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"ok embedded launcher payload matches {LAUNCHER.relative_to(ROOT)} ({len(wanted)} chars)")
        return 0

    updated = PAYLOAD.sub(lambda m: f"{m.group(1)}{wanted}{m.group(3)}", patch_text, count=1)
    PATCH.write_text(updated, encoding="utf-8")
    print(f"wrote embedded launcher payload into {PATCH.relative_to(ROOT)} ({len(wanted)} chars)")
    line = max(len(row) for row in updated.splitlines())
    if line > CMD_LIMIT:
        print(f"warning: a patch line is {line} characters; cmd.exe caps a command line at {CMD_LIMIT}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
