"""Switches for the two different ways a read can use a sign-in.

They are not the same power and do not share a default:

``sign_in_enabled()`` (on by default)
    Envoy opens the site's own sign-in page in its own isolated profile and
    hands the user a picture of it (usually a QR code). The user scans; Envoy's
    profile keeps the session. Nothing is read from the user's own browsers, so
    the default can be on: the human is the one who signs in, once, in the chat.

``credential_handoff_enabled()`` (opt-in, off by default)
    Envoy copies a session that already exists elsewhere -- the user's Chrome,
    Edge or Safari cookies, or a session from an imported store. That reads
    data the user already owns, so it stays off until they turn it on.

The environment wins over ``~/.envoy/config.json``. Secrets are not part of the
config: this file only stores booleans.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .permissions import restrict_to_owner

CONFIG_NAME = "config.json"
DIR_MODE = 0o700
ENV_FLAG = "ENVOY_CREDENTIAL_HANDOFF"
KEY = "credential_handoff"
SIGN_IN_ENV_FLAG = "ENVOY_IN_CHAT_SIGN_IN"
SIGN_IN_KEY = "sign_in"

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
    restrict_to_owner(path.parent, directory=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        restrict_to_owner(Path(temporary))
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


def sign_in_enabled() -> bool:
    """True unless the user turned the in-chat sign-in itself off."""

    override = os.getenv(SIGN_IN_ENV_FLAG)
    if override is not None:
        parsed = _parse_flag(override)
        if parsed is not None:
            return parsed
    try:
        stored = read_config().get(SIGN_IN_KEY)
    except ConfigError:
        return True
    parsed = _parse_flag(stored)
    return True if parsed is None else bool(parsed)


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
        "in_chat_sign_in": sign_in_enabled(),
    }


def set_credential_handoff(enabled: bool) -> dict:
    """Persist the switch; returns the same status payload the CLI prints."""

    write_config({KEY: bool(enabled)})
    payload = credential_handoff_status()
    payload["changed"] = True
    return payload
