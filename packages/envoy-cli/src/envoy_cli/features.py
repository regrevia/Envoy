"""Opt-in switches for behaviour that touches the user's own accounts.

Credential handoff is the one feature that can read a session the user owns:
Envoy opens a sign-in page for them, or copies their browser's cookies for one
site. That is powerful enough to stay off until the user turns it on, so this
module owns the single decision every such code path asks:

    ``credential_handoff_enabled()``

The value comes from ``ENVOY_CREDENTIAL_HANDOFF`` when set (so one shell can opt
in without editing a file), otherwise from ``~/.envoy/config.json``, otherwise
off. Secrets are not part of the config: this file only stores booleans.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG_NAME = "config.json"
CONFIG_MODE = 0o600
DIR_MODE = 0o700
ENV_FLAG = "ENVOY_CREDENTIAL_HANDOFF"
KEY = "credential_handoff"

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}


class ConfigError(RuntimeError):
    """Raised when the config file cannot be read or written."""


def config_path() -> Path:
    from .cache import envoy_home

    override = os.getenv("ENVOY_CONFIG_DIR")
    root = Path(override).expanduser() if override else envoy_home()
    return root / CONFIG_NAME


def read_config() -> dict:
    """Return the whole config mapping; an unreadable file is an error, not off."""

    path = config_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} does not hold a JSON object")
    return payload


def write_config(patch: dict) -> dict:
    """Merge ``patch`` into the config atomically, owner-only readable."""

    current = read_config()
    current.update(patch)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    try:
        os.chmod(path.parent, DIR_MODE)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, CONFIG_MODE)
        os.replace(temporary, path)
    except OSError as exc:
        raise ConfigError(f"cannot write {path}: {exc}") from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return current


def _parse_flag(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return None


def credential_handoff_enabled() -> bool:
    """True only when the user opted in; environment wins over the file."""

    override = os.getenv(ENV_FLAG)
    if override is not None:
        parsed = _parse_flag(override)
        if parsed is not None:
            return parsed
    try:
        stored = read_config().get(KEY)
    except ConfigError:
        return False
    return bool(_parse_flag(stored))


def credential_handoff_status() -> dict:
    """Describe the switch without ever describing credentials."""

    source = "default"
    enabled = False
    override = os.getenv(ENV_FLAG)
    if override is not None and _parse_flag(override) is not None:
        source = "env"
        enabled = bool(_parse_flag(override))
    else:
        try:
            stored = read_config().get(KEY)
            if _parse_flag(stored) is not None:
                source = "config"
                enabled = bool(_parse_flag(stored))
        except ConfigError as exc:
            return {"enabled": False, "source": "error", "error": str(exc)}
    return {
        "enabled": enabled,
        "source": source,
        "config_path": str(config_path()),
        "env_flag": ENV_FLAG,
    }


def set_credential_handoff(enabled: bool) -> dict:
    """Persist the switch; returns the same status payload the CLI prints."""

    write_config({KEY: bool(enabled)})
    payload = credential_handoff_status()
    payload["changed"] = True
    return payload
