"""Owner-only permissions are real on both POSIX and Windows."""

from __future__ import annotations

import subprocess

import pytest

from envoy_cli import permissions


def test_a_persisted_file_is_owner_only(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    permissions.restrict_to_owner(path)
    assert permissions.is_owner_only(path)


def test_a_directory_is_owner_only(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    permissions.restrict_to_owner(path, directory=True)
    assert permissions.is_owner_only(path)


def test_windows_uses_an_acl_instead_of_posix_mode_bits(monkeypatch, tmp_path):
    """NTFS ignores chmod, so the intent has to be stated with icacls."""

    emitted: list[list[str]] = []

    def _run(command, **kwargs):
        emitted.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(permissions, "_running_on_windows", lambda: True)
    monkeypatch.setattr(permissions, "_owner_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(permissions.subprocess, "run", _run)

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    permissions.restrict_to_owner(path)

    assert emitted
    command = emitted[0]
    assert command[0] == "icacls"
    assert "/inheritance:r" in command
    assert "*S-1-5-21-1-2-3-1001:F" in command


def test_windows_acl_reader_accepts_only_the_running_account(monkeypatch, tmp_path):
    monkeypatch.setattr(permissions, "_running_on_windows", lambda: True)
    monkeypatch.setattr(permissions, "_owner_sid", lambda: "S-1-5-21-1-2-3-1001")
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")

    def _owner_only(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{path} S-1-5-21-1-2-3-1001:(F)\n\nSuccessfully processed 1 files.\n",
            stderr="",
        )

    monkeypatch.setattr(permissions.subprocess, "run", _owner_only)
    assert permissions.is_owner_only(path) is True

    def _everyone(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                f"{path} S-1-5-21-1-2-3-1001:(F)\n"
                f"{path} BUILTIN\\Users:(R)\n\nSuccessfully processed 1 files.\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(permissions.subprocess, "run", _everyone)
    assert permissions.is_owner_only(path) is False


def test_a_mode_that_leaves_a_file_world_readable_is_reported(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    if pytest.importorskip("os").name == "nt":  # pragma: no cover - POSIX assertion
        pytest.skip("NTFS does not carry POSIX mode bits")
    assert permissions.is_owner_only(path) is False
