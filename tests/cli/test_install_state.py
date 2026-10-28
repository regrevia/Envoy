"""Tests for the install-state report behind `envoy doctor`."""

from __future__ import annotations

import json

from envoy_cli import install_state
from envoy_cli import cli


def _binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _pin(home, target):
    home.mkdir(parents=True, exist_ok=True)
    (home / install_state.PIN_FILENAME).write_text(str(target) + "\n", encoding="utf-8")


def test_missing_pin_reports_unpinned(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_UNPINNED
    assert report["pinned_runtime"] is None
    assert report["invoked_is_pinned"] is False
    # The hint has to be the command this platform can actually run.
    assert install_state.install_hint() in report["next_action"]


def test_pin_matching_running_binary_is_pinned(monkeypatch, tmp_path):
    home = tmp_path / "home"
    binary = _binary(tmp_path / "venv" / "bin" / "envoy")
    _pin(home, binary)
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: str(binary))
    monkeypatch.setattr("sys.argv", [str(binary), "doctor"])
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_PINNED
    assert report["invoked_is_pinned"] is True
    assert report["next_action"] is None


def test_pin_with_dot_segments_still_matches(monkeypatch, tmp_path):
    home = tmp_path / "home"
    binary = _binary(tmp_path / "bin" / "envoy")
    _pin(home, tmp_path / "bin" / "sub" / ".." / "envoy")
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: str(binary))
    monkeypatch.setattr("sys.argv", [str(binary), "doctor"])
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_PINNED
    assert report["invoked_is_pinned"] is True


def test_other_binary_on_path_is_shadowed(monkeypatch, tmp_path):
    home = tmp_path / "home"
    pinned = _binary(tmp_path / "pinned" / "envoy")
    stale = _binary(tmp_path / "old" / "envoy")
    _pin(home, pinned)
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: str(stale))
    monkeypatch.setattr("sys.argv", [str(stale), "doctor"])
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_SHADOWED
    assert report["pinned_runtime"] == str(pinned)
    assert report["path_envoy"] == str(stale.resolve())
    assert install_state.install_hint() in report["next_action"]


def test_missing_pinned_file_is_stale(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _pin(home, tmp_path / "gone" / "envoy")
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_STALE_PIN
    assert "gone" in report["next_action"]


def test_blank_pin_is_unpinned(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / install_state.PIN_FILENAME).write_text("  \n", encoding="utf-8")
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = install_state.install_report()
    assert report["status"] == install_state.STATUS_UNPINNED
    assert report["pinned_runtime"] is None


def test_envoy_home_override_is_reported(monkeypatch, tmp_path):
    home = tmp_path / "custom home"
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = install_state.install_report()
    assert report["home"] == str(home)


def test_install_hint_matches_platform():
    # A Windows user pasting `sh install.sh` in PowerShell fails; give install.ps1.
    assert "install.ps1" in install_state.install_hint("win32")
    assert "install.ps1" in install_state.install_hint("windows")
    assert "install.ps1" in install_state.install_hint("nt")
    assert "sh install.sh" in install_state.install_hint("darwin")
    assert "sh install.sh" in install_state.install_hint("linux")


def test_windows_hint_reaches_next_action(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("ENVOY_HOME", str(home))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(install_state, "install_hint", lambda *a: install_state.WINDOWS_INSTALL_HINT)
    report = install_state.install_report()
    assert "install.ps1" in report["next_action"]


def test_doctor_json_includes_install_status(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    _pin(home, tmp_path / "venv" / "bin" / "envoy")
    monkeypatch.setenv("ENVOY_HOME", str(home))
    assert cli.main(["doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    install = payload["runtime"]["install"]
    assert install["status"] in {
        install_state.STATUS_PINNED,
        install_state.STATUS_SHADOWED,
        install_state.STATUS_STALE_PIN,
        install_state.STATUS_UNPINNED,
    }
    assert set(install) >= {
        "home",
        "pinned_runtime",
        "invoked_executable",
        "path_envoy",
        "status",
        "next_action",
    }


def test_doctor_text_names_runtime_state(monkeypatch, tmp_path):
    pinned = _binary(tmp_path / "pinned" / "envoy")
    runtime = {
        "install": {
            "status": install_state.STATUS_PINNED,
            "pinned_runtime": str(pinned),
            "path_envoy": str(pinned),
            "next_action": None,
        }
    }
    text = cli._doctor_text(runtime)
    assert "runtime    pinned:" in text
    assert str(pinned) in text

    shadowed = {
        "install": {
            "status": install_state.STATUS_SHADOWED,
            "pinned_runtime": str(pinned),
            "path_envoy": "/usr/local/bin/envoy",
            "next_action": f"PATH runs /usr/local/bin/envoy; use {pinned} or run sh install.sh",
        }
    }
    text = cli._doctor_text(shadowed)
    assert "/usr/local/bin/envoy" in text
    assert "run sh install.sh" in text

    unpinned = {
        "install": {
            "status": install_state.STATUS_UNPINNED,
            "next_action": "run sh install.sh once to pin the runtime",
        }
    }
    text = cli._doctor_text(unpinned)
    assert "runtime    unpinned: run sh install.sh" in text
