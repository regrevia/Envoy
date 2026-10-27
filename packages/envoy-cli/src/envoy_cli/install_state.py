"""Report how Envoy was installed, so a broken install is visible in `envoy doctor`.

The installer records the absolute path of the runtime it chose in
``~/.envoy/runtime-path`` (``ENVOY_HOME`` overrides the directory).  Commands should
then always run that binary, even when an older ``envoy`` shadows it earlier in
``PATH``.  This module tells the difference between the three ways that can fail:
nothing pinned yet, the pin points at a file that is gone, and the pin exists but
``PATH`` resolves to a different binary.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PIN_FILENAME = "runtime-path"
HOME_ENV = "ENVOY_HOME"

STATUS_PINNED = "pinned"
STATUS_UNPINNED = "unpinned"
STATUS_STALE_PIN = "stale_pin"
STATUS_SHADOWED = "shadowed"

INSTALL_HINT = "run sh install.sh"


def envoy_home() -> Path:
    """Directory holding Envoy's install state; ``ENVOY_HOME`` wins."""
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".envoy"


def pin_file() -> Path:
    return envoy_home() / PIN_FILENAME


def read_pinned_runtime() -> str | None:
    """Absolute runtime path recorded by the installer, or ``None``."""
    try:
        text = pin_file().read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except OSError:
        return None
    if not text:
        return None
    return os.path.normpath(text)


def invoked_executable() -> str | None:
    """Resolved path of the executable that is running this code, if knowable."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0)
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except OSError:
            pass
    found = shutil.which("envoy")
    if not found:
        return None
    try:
        return str(Path(found).resolve())
    except OSError:
        return str(found)


def path_envoy() -> str | None:
    """First ``envoy`` on ``PATH`` as the shell would run it."""
    found = shutil.which("envoy")
    if not found:
        return None
    try:
        return str(Path(found).resolve())
    except OSError:
        return str(found)


def _same_file(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return left == right


def _next_action(status: str, pinned: str | None, on_path: str | None) -> str | None:
    if status == STATUS_UNPINNED:
        return f"{INSTALL_HINT} once to pin the runtime"
    if status == STATUS_STALE_PIN:
        return f"pinned runtime is gone: {INSTALL_HINT}"
    if status == STATUS_SHADOWED:
        target = on_path or "another envoy"
        return f"PATH runs {target}; use {pinned} or {INSTALL_HINT}"
    return None


def install_report() -> dict:
    """Machine-readable install health used by ``envoy doctor``."""
    pinned = read_pinned_runtime()
    invoked = invoked_executable()
    on_path = path_envoy()

    if not pinned:
        status = STATUS_UNPINNED
    elif not Path(pinned).is_file():  # already normalized by read_pinned_runtime
        status = STATUS_STALE_PIN
    elif not _same_file(pinned, invoked):
        status = STATUS_SHADOWED if on_path and not _same_file(on_path, pinned) else STATUS_PINNED
    else:
        status = STATUS_PINNED

    return {
        "home": str(envoy_home()),
        "pinned_runtime": pinned,
        "invoked_executable": invoked,
        "path_envoy": on_path,
        "invoked_is_pinned": _same_file(pinned, invoked),
        "status": status,
        "next_action": _next_action(status, pinned, on_path),
    }
