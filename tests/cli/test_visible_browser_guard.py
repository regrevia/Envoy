"""Visible browser mode must stay a human-at-a-terminal action.

The MCP host talks to Envoy over pipes, so a window opened there lands on a
screen nobody is watching. These tests pin the guard: the opt-in only takes
effect when both stdio streams are terminals.
"""

from __future__ import annotations

from envoy_cli import browser


def _tty(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(browser.sys, "stdin", _Stream(value))
    monkeypatch.setattr(browser.sys, "stdout", _Stream(value))


class _Stream:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_visible_mode_off_by_default_even_at_a_terminal(monkeypatch):
    monkeypatch.delenv("ENVOY_VISIBLE_BROWSER", raising=False)
    _tty(monkeypatch, True)

    assert browser._visible_browser_allowed(None) is False


def test_visible_mode_on_for_an_interactive_terminal(monkeypatch):
    monkeypatch.setenv("ENVOY_VISIBLE_BROWSER", "1")
    _tty(monkeypatch, True)

    assert browser._visible_browser_allowed(None) is True


def test_visible_mode_is_refused_when_stdio_is_a_pipe(monkeypatch):
    monkeypatch.setenv("ENVOY_VISIBLE_BROWSER", "1")
    _tty(monkeypatch, False)

    assert browser._visible_browser_allowed(None) is False


def test_visible_mode_is_refused_when_only_stdin_is_a_pipe(monkeypatch):
    monkeypatch.setenv("ENVOY_VISIBLE_BROWSER", "1")
    monkeypatch.setattr(browser.sys, "stdin", _Stream(False))
    monkeypatch.setattr(browser.sys, "stdout", _Stream(True))

    assert browser._visible_browser_allowed(None) is False


def test_explicit_override_does_not_bypass_the_terminal_requirement(monkeypatch):
    monkeypatch.delenv("ENVOY_VISIBLE_BROWSER", raising=False)
    _tty(monkeypatch, False)

    assert browser._visible_browser_allowed(True) is False


def test_visible_mode_accepts_the_documented_env_spellings(monkeypatch):
    _tty(monkeypatch, True)
    for value in ("1", "true", "yes", "on", "TRUE", " On "):
        monkeypatch.setenv("ENVOY_VISIBLE_BROWSER", value)
        assert browser._visible_browser_allowed(None) is True


def test_visible_mode_is_refused_when_the_terminal_probe_raises(monkeypatch):
    monkeypatch.setenv("ENVOY_VISIBLE_BROWSER", "1")

    class _Broken:
        def isatty(self):
            raise ValueError("closed")

    monkeypatch.setattr(browser.sys, "stdin", _Broken())
    monkeypatch.setattr(browser.sys, "stdout", _Broken())

    assert browser._visible_browser_allowed(None) is False
