"""Opt-in extensions that sit on top of the material reader.

Core reading never imports this module's switch to decide whether a read
works: every extension is off until a user turns it on, and an unreadable
config makes it off rather than fatal. That keeps the promise that installing
or not installing an extension leaves ordinary reads unchanged.

Three tiers, in the order they can be enabled:

``core``
    No flag at all. Web, GitHub, PDF, video, OCR, ASR, retention, resume,
    budgets and the evidence locators are available with no extension.

``structure`` / ``knowledge`` / ``integration``
    Only add derived artifacts: a structure view, knowledge units, an export
    pack, an adapter for another tool. They read materials through the same
    identity as Core and never rewrite or replace one.

The environment wins over ``~/.envoy/config.json``, matching the sign-in
switches in :mod:`envoy_cli.features`.
"""

from __future__ import annotations

import os

from . import features

ENV_FLAG = "ENVOY_EXTENSIONS"
KEY = "extensions"

EVIDENCE_PACK_EXPORT = "evidence_pack_export"
STRUCTURE_ANALYSIS = "structure_analysis"
KNOWLEDGE_UNITS = "knowledge_units"
SKILL_DISTILLATION = "skill_distillation"
KNOWLEDGE_GRAPH = "knowledge_graph"
CANGJIE_ADAPTER = "cangjie_adapter"

# name -> (tier, one-line description shown by `envoy extensions status`)
FLAGS = {
    EVIDENCE_PACK_EXPORT: ("structure", "export a material as an Evidence Pack"),
    STRUCTURE_ANALYSIS: ("structure", "group a material into sections and an outline"),
    KNOWLEDGE_UNITS: ("knowledge", "store candidate knowledge units with source refs"),
    SKILL_DISTILLATION: ("knowledge", "compile promoted units into a skill"),
    KNOWLEDGE_GRAPH: ("knowledge", "link knowledge units with evidenced relations"),
    CANGJIE_ADAPTER: ("integration", "convert an Evidence Pack into Cangjie input"),
}
TIERS = ("structure", "knowledge", "integration")

_TRUTHY = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSY = {"0", "false", "no", "off", "disable", "disabled"}


class UnknownExtension(ValueError):
    """Raised when a flag name is not one of :data:`FLAGS`."""


class ExtensionDisabled(RuntimeError):
    """Raised by a command that needs an extension the user has not enabled."""


def _flag_values() -> dict:
    raw = os.getenv(ENV_FLAG, "")
    if not raw.strip():
        return {}
    wanted = {}
    for token in raw.split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name not in FLAGS:
            continue
        wanted[name] = True
    return wanted


def _stored_values() -> dict:
    try:
        config = features.read_config()
    except (OSError, features.ConfigError):
        # A broken config must not break a read path that merely asks whether
        # an optional capability is on; treated as off, the core is unaffected.
        return {}
    stored = config.get(KEY)
    if not isinstance(stored, dict):
        return {}
    return {name: value for name, value in stored.items() if name in FLAGS}


def enabled(name: str) -> bool:
    """Whether one extension is on. Unknown names are a programming error."""

    if name not in FLAGS:
        raise UnknownExtension(name)
    overrides = _flag_values()
    if name in overrides:
        return overrides[name]
    value = _stored_values().get(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUTHY:
            return True
        if lowered in _FALSY:
            return False
    return False


def require(name: str) -> None:
    """Raise :class:`ExtensionDisabled` when a command needs a flag that is off."""

    if not enabled(name):
        raise ExtensionDisabled(
            f"{name} is off; enable it with: envoy extensions enable {name}"
        )


def set_enabled(name: str, value: bool) -> dict:
    """Persist one flag, leaving the rest of the config untouched."""

    if name not in FLAGS:
        raise UnknownExtension(name)
    current = features.read_config()
    stored = current.get(KEY)
    settings = dict(stored) if isinstance(stored, dict) else {}
    settings[name] = bool(value)
    features.write_config({KEY: settings})
    return status()


def status() -> dict:
    """The full flag table, so a user can see what is off and how to turn it on."""

    rows = []
    for name, (tier, description) in FLAGS.items():
        rows.append(
            {
                "name": name,
                "tier": tier,
                "description": description,
                "enabled": enabled(name),
            }
        )
    ordered = sorted(rows, key=lambda row: (TIERS.index(row["tier"]), row["name"]))
    return {"extensions": ordered, "env_flag": ENV_FLAG, "config_key": KEY}


def render_status(report: dict) -> str:
    lines = ["Extensions (off unless enabled; core reads never need them):"]
    for row in report["extensions"]:
        mark = "on " if row["enabled"] else "off"
        lines.append(f"  [{mark}] {row['name']:<20} {row['tier']:<11} {row['description']}")
    lines.append("")
    lines.append("Enable one with: envoy extensions enable <name>")
    return "\n".join(lines)
