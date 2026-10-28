"""Console entrypoint that answers version questions without loading Envoy.

The Windows measurement showed a full CLI start at ~650-720 ms per invocation,
almost all of it imports: ``cli`` pulls in the reader stack (httpx, pypdf) and the
MCP bridge before it prints anything. ``envoy --version`` is asked on every host
start and by the installer, and its answer is static, so it is served here and the
real parser is only built when the command needs it.
"""

from __future__ import annotations

import sys
from typing import Optional

_VERSION_FLAGS = {"--version", "-V"}


def _version_text() -> str:
    from envoy_core import __version__ as core_version

    from . import __version__

    return f"envoy-cli {__version__} (core {core_version})"


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 1 and arguments[0] in _VERSION_FLAGS:
        print(_version_text())
        return 0
    from .cli import main as cli_main

    return cli_main(arguments)


def entrypoint() -> None:
    raise SystemExit(main())
