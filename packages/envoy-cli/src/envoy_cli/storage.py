"""Where Envoy keeps materials, and what happens when it cannot write them.

A confined host (a sandboxed coding agent, a read-only container, a CI job that
mounts ``$HOME`` read-only) can run Envoy with a perfectly usable browser and
network while denying writes under ``~/.envoy``. Failing the whole read with
``Operation not permitted`` hides that: nothing about the source was wrong, only
the scratch directory. Resolution therefore walks the candidate roots and takes
the first writable one, falling back to the operating system temporary
directory, and reports the fallback so callers can tell the user where state
went instead of silently losing it.
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

MATERIAL_DIR = "materials"
STATE_DIR = ".envoy"
FALLBACK_PREFIX = "envoy-materials-"
PROBE_NAME = ".envoy-write-probe"

# Errors that mean "this root cannot hold state" rather than "something is
# broken": permission, read-only file system, a file in the way, a missing
# parent that cannot be created.
UNUSABLE_ERRNOS = frozenset({
    errno.EACCES,
    errno.EPERM,
    errno.EROFS,
    errno.ENOTDIR,
    errno.ENOSPC,
    errno.EDQUOT,
})

# Resolution is cached per (configured root, home) pair: tests and hosts that
# move either one get a fresh answer instead of the first winner in the process.
_CACHE: dict[tuple[str | None, str], tuple[Path, dict | None]] = {}


class StateUnavailable(RuntimeError):
    """No candidate state directory accepts writes."""


def configured_root() -> Path | None:
    configured = os.getenv("ENVOY_LOCAL_DATA_DIR", "").strip()
    return Path(configured).expanduser() if configured else None


def preferred_root() -> Path:
    configured = configured_root()
    return configured if configured is not None else Path.home() / STATE_DIR / MATERIAL_DIR


def fallback_root() -> Path:
    identity = str(getattr(os, "getuid", lambda: "user")())
    base = Path(tempfile.gettempdir()) / f"{FALLBACK_PREFIX}{identity}"
    return base


def _uid_suffix(root: Path) -> Path:
    # The temporary directory is shared; keep state per user so two accounts on
    # one machine never read each other's retained material.
    if root.name.startswith(FALLBACK_PREFIX):
        return root
    return root


def _usable(root: Path) -> tuple[bool, str]:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    probe = root / PROBE_NAME
    try:
        with open(probe, "ab"):
            pass
        probe.unlink()
    except OSError as exc:
        return False, f"{exc.__class__.__name__}: {exc}"
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return True, ""


def _candidates() -> list[Path]:
    roots: list[Path] = []
    configured = configured_root()
    if configured is not None:
        roots.append(configured)
    roots.append(Path.home() / STATE_DIR / MATERIAL_DIR)
    roots.append(fallback_root())
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(_uid_suffix(root))
    return unique


def _cache_key() -> tuple[str | None, str]:
    configured = configured_root()
    return (str(configured) if configured is not None else None, str(Path.home()))


def resolve_material_root() -> Path:
    """Return the first writable material root, remembering the resolution."""

    key = _cache_key()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached[0]
    requested = preferred_root()
    failures: list[str] = []
    for root in _candidates():
        usable, reason = _usable(root)
        if usable:
            fallback = None
            if root != requested:
                fallback = {
                    "requested": str(requested),
                    "active": str(root),
                    "reason": failures[-1] if failures else "unwritable",
                    "kind": "state_not_writable",
                }
            _CACHE[key] = (root, fallback)
            return root
        failures.append(f"{root}: {reason}")
    raise StateUnavailable(
        "Envoy cannot write its state directory. Tried: "
        + "; ".join(failures)
        + ". Point ENVOY_LOCAL_DATA_DIR at a writable directory, or allow writes "
        "to the default location."
    )


def fallback_report() -> dict | None:
    """Describe the fallback in use, or ``None`` when the preferred root works."""

    cached = _CACHE.get(_cache_key())
    if cached is None or cached[1] is None:
        return None
    return dict(cached[1])


def state_root_status() -> dict:
    """Non-mutating status for doctor/cache output."""

    cached = _CACHE.get(_cache_key())
    return {
        "preferred": str(preferred_root()),
        "active": str(cached[0]) if cached is not None else None,
        "fallback": fallback_report(),
    }


def reset_root_cache() -> None:
    """Forget the cached resolution (tests, and after the environment changes)."""

    _CACHE.clear()
