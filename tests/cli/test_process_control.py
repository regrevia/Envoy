"""Process control must behave the same on Unix and Windows.

Two shipped defects live here: on Windows ``os.kill(pid, 0)`` terminates the
process instead of probing it, and a cancel path written against
``os.getpgid``/``os.killpg``/``signal.SIGTERM`` raises ``AttributeError`` on
Windows, so the pause was never recorded as canceled.

The Windows branches cannot run on this host, so the tests below swap the
module's ``os`` for a namespace that has only what Windows provides. A Unix
attribute left in the Windows path fails here instead of on a user's machine.
"""

from __future__ import annotations

import types

import pytest

from envoy_cli import auth_session, process_control


def _fake_windows_os(monkeypatch):
    fake = types.SimpleNamespace(name="nt", getpid=lambda: 1)
    monkeypatch.setattr(process_control, "os", fake)
    return fake


def _fake_taskkill(monkeypatch, code=0):
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=code)

    monkeypatch.setattr(
        process_control, "subprocess", types.SimpleNamespace(run=_run, DEVNULL=-3)
    )
    return calls


def test_coerce_pid_rejects_non_positive_and_garbage():
    assert process_control.coerce_pid("4321") == 4321
    assert process_control.coerce_pid(7) == 7
    for bad in (None, "", "abc", 0, -3, 1.2, {}):
        assert process_control.coerce_pid(bad) is None


def test_posix_probe_distinguishes_missing_and_permission(monkeypatch):
    monkeypatch.setattr(process_control, "_is_windows", lambda: False)

    def _gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(process_control.os, "kill", _gone)
    assert process_control.pid_alive(123) is False

    def _denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(process_control.os, "kill", _denied)
    assert process_control.pid_alive(123) is True


def test_windows_probe_never_terminates(monkeypatch):
    """The Windows probe must not call os.kill (which terminates there)."""

    _fake_windows_os(monkeypatch)
    killed: list[tuple] = []
    monkeypatch.setattr(
        process_control,
        "os",
        types.SimpleNamespace(
            name="nt", kill=lambda *a: killed.append(a), getpid=lambda: 1
        ),
    )
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: True)

    assert process_control.pid_alive(99) is True
    assert killed == []


def test_windows_stop_uses_taskkill_without_unix_attributes(monkeypatch):
    _fake_windows_os(monkeypatch)  # no killpg, no getpgid, no SIGTERM
    calls = _fake_taskkill(monkeypatch)
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: True)

    assert process_control.stop_process(4321) is True
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_windows_stop_dead_pid_starts_nothing(monkeypatch):
    _fake_windows_os(monkeypatch)
    calls = _fake_taskkill(monkeypatch)
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: False)

    assert process_control.stop_process(4321) is False
    assert calls == []


def test_windows_stop_reports_taskkill_failure(monkeypatch):
    _fake_windows_os(monkeypatch)
    _fake_taskkill(monkeypatch, code=1)
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: True)

    assert process_control.stop_process(4321) is False


def test_stop_rejects_missing_pid_without_error(monkeypatch):
    monkeypatch.setattr(process_control, "_is_windows", lambda: False)
    assert process_control.stop_process(None) is False
    assert process_control.stop_process("") is False


def test_auth_session_cancel_on_windows_records_canceled(monkeypatch):
    """Canceling a sign-in pause must mark it canceled on every platform."""

    _fake_windows_os(monkeypatch)
    calls = _fake_taskkill(monkeypatch)
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: True)

    written: dict = {}
    monkeypatch.setattr(auth_session, "read_state", lambda action_id: {"pid": 4321})
    monkeypatch.setattr(
        auth_session, "_write_state", lambda action_id, payload: written.update(payload)
    )
    updated: dict = {}
    monkeypatch.setattr(
        auth_session.authflow,
        "update_auth_action",
        lambda action_id, **kwargs: updated.update(kwargs),
    )

    result = auth_session.stop("action-1")

    assert result == {
        "action_id": "action-1",
        "status": "canceled",
        "browser_stopped": True,
    }
    assert written["status"] == "canceled"
    assert written["pid"] is None
    assert updated == {"status": "canceled"}
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_auth_session_cancel_on_windows_survives_dead_browser(monkeypatch):
    _fake_windows_os(monkeypatch)
    calls = _fake_taskkill(monkeypatch)
    monkeypatch.setattr(process_control, "_windows_pid_alive", lambda pid: False)
    written: dict = {}
    monkeypatch.setattr(auth_session, "read_state", lambda action_id: {"pid": 4321})
    monkeypatch.setattr(
        auth_session, "_write_state", lambda action_id, payload: written.update(payload)
    )
    monkeypatch.setattr(
        auth_session.authflow, "update_auth_action", lambda action_id, **kwargs: None
    )

    result = auth_session.stop("action-1")

    assert result["browser_stopped"] is False
    assert written["status"] == "canceled"
    assert calls == []


def test_platform_status_is_reported(monkeypatch):
    monkeypatch.setattr(process_control, "_is_windows", lambda: False)
    status = process_control.describe_backend()
    assert status["probe"] == "kill(pid, 0)"
    assert "killpg" in status["stop"]


@pytest.mark.parametrize("pid", [None, "", "nope", 0])
def test_alive_of_unusable_pid_is_false(pid, monkeypatch):
    monkeypatch.setattr(process_control, "_is_windows", lambda: False)
    assert process_control.pid_alive(pid) is False
