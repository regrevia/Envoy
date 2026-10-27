"""A missing Chromium binary is provisioned on first use.

A runtime installed by the DSH bundle has Playwright but no browser binary.
Without this, the first page that needs a browser ends in "run envoy browser
setup" — a shell command a plugin user has no terminal for. The install is
bounded: one attempt per process, never under pytest, and skippable with
ENVOY_DISABLE_AUTO_BROWSER_SETUP=1.
"""

from __future__ import annotations

import sys

from envoy_cli import browser


class _FakeResult:
    returncode = 0


def test_installs_when_binary_missing(monkeypatch):
    calls: list[list[str]] = []
    state = {"ready": False}
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: state["ready"])
    def _install(argv, **kw):
        calls.append(argv)
        state["ready"] = True
        return _FakeResult()

    monkeypatch.setattr(browser.subprocess, "run", _install)
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delenv("ENVOY_DISABLE_AUTO_BROWSER_SETUP", raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert browser.ensure_browser_ready() is True
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium"]]


def test_installs_once_per_process(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    browser.ensure_browser_ready()
    browser.ensure_browser_ready()
    assert len(calls) == 1


def test_disabled_by_env(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.setenv("ENVOY_DISABLE_AUTO_BROWSER_SETUP", "1")
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert browser.ensure_browser_ready() is False
    assert calls == []


def test_present_binary_is_not_installed(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: True)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert browser.ensure_browser_ready() is True
    assert calls == []


def test_read_judges_capability_instead_of_downloading(monkeypatch):
    """A read must not spend the host's tool deadline on a bootstrap download."""

    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delenv("ENVOY_AUTO_BROWSER_SETUP", raising=False)
    monkeypatch.delenv("ENVOY_DISABLE_AUTO_BROWSER_SETUP", raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    def _factory():
        raise AssertionError("the browser must not launch when it is not installed")

    with __import__("pytest").raises(browser.BrowserRuntimeUnavailable) as caught:
        browser.read_page("https://example.com/a", playwright_factory=_factory, max_work_seconds=30)

    assert caught.value.setup_required is True
    assert "envoy browser setup" in str(caught.value)
    assert calls == []


def test_read_download_is_bounded_when_opted_in(monkeypatch):
    """Opting in still caps the download well below the read deadline."""

    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)

    def _install(argv, **kw):
        calls.append([argv, kw.get("timeout")])
        return _FakeResult()

    monkeypatch.setattr(browser.subprocess, "run", _install)
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.setenv("ENVOY_AUTO_BROWSER_SETUP", "1")
    monkeypatch.delenv("ENVOY_DISABLE_AUTO_BROWSER_SETUP", raising=False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    def _factory():
        raise AssertionError("never launched")

    with __import__("pytest").raises(browser.BrowserRuntimeUnavailable):
        browser.read_page("https://example.com/a", playwright_factory=_factory, max_work_seconds=30)

    assert len(calls) == 1
    assert 0 < calls[0][1] <= browser._READ_SETUP_BUDGET_SECONDS
    assert calls[0][1] <= 30


def test_zero_setup_budget_skips_download(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert browser.ensure_browser_ready(timeout_seconds=0, auto_setup=True) is False
    assert calls == []


def test_explicit_no_auto_setup_skips_download(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser.subprocess, "run", lambda argv, **kw: calls.append(argv) or _FakeResult())
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert browser.ensure_browser_ready(auto_setup=False) is False
    assert calls == []
