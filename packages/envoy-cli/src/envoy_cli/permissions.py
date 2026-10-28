"""Owner-only file permissions, expressed the way each platform understands.

``os.chmod(path, 0o600)`` is the whole story on POSIX and a no-op on NTFS: the
mode comes back as 0o666 and Windows keeps whatever ACL the parent handed down.
Envoy's config and cookie store hold a switch and imported cookie values, so on
Windows the same intent is expressed with ``icacls``: break inheritance and grant
the current account full control, nobody else anything.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def _running_on_windows() -> bool:
    return os.name == "nt"


def _owner_sid() -> str:
    """The SID of the account running Envoy, or "" when it cannot be read."""

    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in result.stdout.splitlines():
        parts = [item.strip().strip('"') for item in line.split(",")]
        for part in parts:
            if part.upper().startswith("S-1-"):
                return part
    return ""


def restrict_to_owner(path: Path, *, directory: bool = False) -> None:
    """Make ``path`` readable only by the account that runs Envoy.

    Failures are not raised: the caller has just written the file and a permission
    it cannot tighten must not turn a successful write into an error.
    """

    if _running_on_windows():
        sid = _owner_sid()
        if not sid:
            return
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return
        return
    mode = 0o700 if directory else 0o600
    try:
        os.chmod(path, mode)
    except OSError:
        return


def is_owner_only(path: Path) -> bool:
    """Whether ``path`` is restricted to the current account."""

    if _running_on_windows():
        sid = _owner_sid()
        if not sid:
            return False
        try:
            result = subprocess.run(
                ["icacls", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        grants = [
            line.strip()
            for line in result.stdout.splitlines()
            if ":" in line and "Successfully processed" not in line
        ]
        if not grants:
            return False
        allowed = 0
        for line in grants:
            # "file SID:(F)" on one line when icacls prints the path; the
            # permission part always follows the last colon.
            permission = line.rsplit(":", 1)[-1].strip()
            if not permission:
                continue
            if "(" in line and sid in line.upper():
                allowed += 1
                continue
            return False
        return allowed >= 1
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    if path.is_dir():
        return mode == 0o700
    return mode == 0o600


__all__ = ["restrict_to_owner", "is_owner_only"]
