"""Query and stop a detached background process without Unix-only assumptions.

``auth_session`` spawns a detached browser and later asks two questions about
it: is it still running, and can I stop it. The Unix answers (``os.kill(pid,
0)`` and ``os.killpg(os.getpgid(pid), SIGTERM)``) are not portable:

* On Windows ``os.kill(pid, 0)`` is *not* a side-effect-free probe — CPython
  routes it to ``TerminateProcess``, so a "status" call would kill the browser
  it was asked about.
* Windows has no ``os.getpgid``/``os.killpg``/``signal.SIGTERM`` handle, so a
  cancel path that references them raises ``AttributeError`` and never records
  the canceled state.

This module keeps both operations behind one interface. Windows uses
``OpenProcess`` + ``GetExitCodeProcess`` for the probe and ``taskkill /T`` for
the stop; POSIX keeps the process-group signal path. The Windows branches are
exercised on a Windows host separately (see DEV/C-REPORT.md); the unit tests
here drive the same code through a fake backend, so a missing Unix attribute
fails a test instead of a user's cancel button.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5


def _is_windows() -> bool:
    return os.name == "nt"


def coerce_pid(pid: object) -> int | None:
    if isinstance(pid, bool) or pid is None:
        return None
    if isinstance(pid, int):
        value = pid
    elif isinstance(pid, str):
        stripped = pid.strip()
        if not stripped.isdigit():
            return None
        value = int(stripped)
    else:
        return None
    return value if value > 0 else None


def _posix_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but belongs to someone else.
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    """Probe a Windows pid without terminating it."""

    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        # Access denied means the process is there; anything else (invalid
        # parameter) means the pid is gone.
        return int(kernel32.GetLastError()) == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: object) -> bool:
    """True when ``pid`` is a live process. Never terminates it."""

    value = coerce_pid(pid)
    if value is None:
        return False
    try:
        if _is_windows():
            return _windows_pid_alive(value)
        return _posix_pid_alive(value)
    except Exception:
        return False


def _posix_stop(pid: int, timeout: float) -> bool:
    signaled = False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        signaled = True
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
            signaled = True
        except (ProcessLookupError, PermissionError, OSError):
            return False
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not _posix_pid_alive(pid):
            return True
        time.sleep(0.05)
    # The process ignored SIGTERM: ask once more, harder.
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            return signaled
    return True


def _windows_stop(pid: int, timeout: float) -> bool:
    """Stop a Windows process and its children."""

    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(5.0, timeout + 5.0),
        )
    except Exception:
        return False
    return int(result.returncode) == 0


def stop_process(pid: object, *, timeout: float = 5.0) -> bool:
    """Stop ``pid`` (and its group/children) and report whether it was asked."""

    value = coerce_pid(pid)
    if value is None:
        return False
    if not pid_alive(value):
        return False
    try:
        if _is_windows():
            return _windows_stop(value, timeout)
        return _posix_stop(value, timeout)
    except Exception:
        return False


def describe_backend() -> dict:
    """Capability report for doctor: which mechanism this platform uses."""

    return {
        "platform": "windows" if _is_windows() else sys.platform,
        "probe": "OpenProcess/GetExitCodeProcess" if _is_windows() else "kill(pid, 0)",
        "stop": "taskkill /T /F" if _is_windows() else "killpg(SIGTERM) then SIGKILL",
    }
