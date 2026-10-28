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


def _run_capture(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _owner_sid() -> str:
    """The SID of the account running Envoy, or "" when it cannot be read."""

    for line in _run_capture(["whoami", "/user", "/fo", "csv", "/nh"]).splitlines():
        for part in line.split(","):
            candidate = part.strip().strip('"').strip()
            if candidate.upper().startswith("S-1-"):
                return candidate
    return ""


def _owner_name() -> str:
    """The ``DOMAIN\\user`` name of the account running Envoy, or ""."""

    lines = [line.strip() for line in _run_capture(["whoami"]).splitlines() if line.strip()]
    return lines[0] if lines else ""


# Windows always lets the OS account and local administrators in; that is not
# "another user can read it", so it does not count as exposure here.
_SYSTEM_ACCOUNTS = {"system", "administrators", "trustedinstaller"}


def _account_of(principal: str) -> str:
    """The account part of ``DOMAIN\\user``, lowercased."""

    return principal.strip().rstrip(":").lower().split("\\")[-1]


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
        return _windows_acl_is_owner_only(path)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    if path.is_dir():
        return mode == 0o700
    return mode == 0o600


def _windows_acl_is_owner_only(path: Path) -> bool:
    """Read the ACL back: every grant must name the running account only."""

    account = _account_of(_owner_name())
    if not account:
        return False
    output = _run_capture(["icacls", str(path)])
    if not output:
        return False
    grants = 0
    for line in output.splitlines():
        if "(" not in line or "Successfully processed" in line:
            continue
        if "Mandatory Label" in line:
            continue
        head = line[: line.index("(")].rstrip(": ").split()
        if not head:
            return False
        principal = _account_of(head[-1])
        if principal != account and principal not in _SYSTEM_ACCOUNTS:
            return False
        grants += 1
    return grants >= 1


__all__ = ["restrict_to_owner", "is_owner_only"]
