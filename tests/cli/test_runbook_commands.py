"""Every command in the runbook must exist in the tool it names.

RUN-WHEN-PRESENT.md is the one deliverable the human actually pastes when the
machine is finally free. A renamed flag there costs another round of "it died
in argparse, what now", so the commands are checked against the real parsers
instead of being trusted as prose.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "DEV" / "RUN-WHEN-PRESENT.md"
SCRIPT_DIR = ROOT / "scripts"
FLAG = re.compile(r"(?<![\w-])--([a-z][a-z0-9-]*)")
ENVOY = re.compile(r"\benvoy ([a-z][a-z0-9-]*)")
PYTHON_TOOL = re.compile(r"\bpython3? (?:scripts/)?([a-z_]+\.py)")


def _surface(parser: argparse.ArgumentParser) -> dict[str, object]:
    """Map each first subcommand to its own flags plus its subcommands' flags."""
    table: dict[str, object] = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            flags = {opt for act in child._actions for opt in act.option_strings}
            nested: dict[str, set[str]] = {}
            for act in child._actions:
                if isinstance(act, argparse._SubParsersAction):
                    for sub_name, sub in act.choices.items():
                        nested[sub_name] = flags | {
                            opt for item in sub._actions for opt in item.option_strings
                        }
            table[name] = nested or flags
    return table


@pytest.fixture(scope="module")
def envoy_surface() -> dict[str, object]:
    from envoy_cli.cli import _parser

    return _surface(_parser())


@pytest.fixture(scope="module")
def runbook_lines() -> list[str]:
    return RUNBOOK.read_text(encoding="utf-8").splitlines()


def _allowed_flags(node: object) -> set[str]:
    if isinstance(node, set):
        return node
    flags: set[str] = set()
    for value in node.values():  # type: ignore[union-attr]
        flags |= value
    return flags


def test_runbook_exists_and_has_commands(runbook_lines: list[str]) -> None:
    assert RUNBOOK.is_file()
    assert any("envoy " in line for line in runbook_lines)


def test_every_envoy_command_names_a_real_subcommand(
    envoy_surface: dict[str, object], runbook_lines: list[str]
) -> None:
    unknown = []
    for line in runbook_lines:
        for name in ENVOY.findall(line):
            if name not in envoy_surface:
                unknown.append((name, line.strip()[:80]))
    assert not unknown, unknown


def wrong_flags(
    lines: list[str], surface: dict[str, object]
) -> list[tuple[str, str, str]]:
    """Name every documented envoy command whose flags the parser would reject."""
    wrong = []
    for line in lines:
        match = ENVOY.search(line)
        if not match:
            continue
        allowed = _allowed_flags(surface.get(match.group(1)))
        for flag in FLAG.findall(line):
            if f"--{flag}" not in allowed:
                wrong.append((match.group(1), f"--{flag}", line.strip()[:80]))
    return wrong


def test_every_envoy_flag_on_a_documented_line_is_accepted(
    envoy_surface: dict[str, object], runbook_lines: list[str]
) -> None:
    assert not wrong_flags(runbook_lines, envoy_surface)


def test_the_check_rejects_a_flag_the_cli_never_had(
    envoy_surface: dict[str, object]
) -> None:
    bad = ["envoy read --request-json - --pages 1-5"]
    assert wrong_flags(bad, envoy_surface) == [("read", "--pages", bad[0])]


def test_every_python_tool_flag_in_the_runbook_exists(
    runbook_lines: list[str], tmp_path: Path
) -> None:
    import subprocess
    import sys

    wrong = []
    seen: set[str] = set()
    for line in runbook_lines:
        for script in PYTHON_TOOL.findall(line):
            if script in seen:
                continue
            seen.add(script)
            path = SCRIPT_DIR / script
            assert path.is_file(), f"runbook names {script}, which is not in scripts/"
            help_text = subprocess.run(
                [sys.executable, str(path), "--help"],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(tmp_path),
            ).stdout
            for flag in FLAG.findall(line):
                if f"--{flag}" not in help_text:
                    wrong.append((script, f"--{flag}", line.strip()[:80]))
    assert not wrong, wrong
    assert seen, "the runbook no longer runs any shipped script"
