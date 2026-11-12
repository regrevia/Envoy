"""The batch queue must import and lock on Windows too.

Windows has no ``fcntl``, so a module-level import of it breaks every import of
``envoy_cli.cli`` (and the whole CLI) on that platform. The lock falls back to
``msvcrt`` there; both branches live inside ``_locked``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_BATCH = Path(__file__).resolve().parents[2] / "packages" / "envoy-cli" / "src" / "envoy_cli" / "batch.py"
_POSIX_ONLY = {"fcntl", "msvcrt", "termios", "pwd", "grp"}


def _module_level_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_batch_does_not_import_platform_modules_at_module_level():
    tree = ast.parse(_BATCH.read_text(encoding="utf-8"))
    assert not (_module_level_imports(tree) & _POSIX_ONLY)


def test_cli_imports_when_fcntl_is_unavailable():
    script = "\n".join(
        [
            "import sys",
            "class _Blocked:",
            "    def find_spec(self, name, path=None, target=None):",
            "        if name == 'fcntl':",
            "            raise ImportError('no fcntl on this platform')",
            "        return None",
            "sys.meta_path.insert(0, _Blocked())",
            "import envoy_cli.cli",
            "from envoy_cli import batch",
            "assert 'fcntl' not in sys.modules",
            "print('ok')",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


def test_locked_still_round_trips_on_this_platform(tmp_path):
    from envoy_cli import batch

    state = tmp_path / "batches" / "state.json"
    with batch._locked(state):
        assert state.with_name(state.name + ".lock").exists()
    assert state.with_name(state.name + ".lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="simulated Windows branch needs msvcrt stubs")
def test_windows_branch_locks_first_byte(tmp_path, monkeypatch):
    import types

    from envoy_cli import batch

    calls: list[int] = []
    fake = types.ModuleType("msvcrt")
    fake.LK_LOCK = 2
    fake.LK_UNLCK = 0
    fake.locking = lambda handle, mode, size: calls.append(mode)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(batch.os, "name", "nt")

    state = tmp_path / "state.json"
    with batch._locked(state):
        lock = state.with_name(state.name + ".lock")
        assert lock.stat().st_size == 1
    assert calls == [2, 0]
