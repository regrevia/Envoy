"""What the ~180 MB install promises when a capability is not there yet.

The default install is the runtime alone: Chromium (about 683 MB) and the
Whisper weights (about 140 MB) are fetched on first use. A missing capability
must read as "one command away" - never as an unreadable page, a crash or a
silent skip - and the command it names has to be the one that fetches it.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from envoy_cli import browser, media, reader


def _request():
    return SimpleNamespace(
        request_id="req-1",
        session_id="sess-1",
        source=SimpleNamespace(url="https://example.com/page"),
    )


def test_a_missing_browser_carries_its_fetch_command():
    unavailable = browser.BrowserRuntimeUnavailable(
        "Envoy's browser is not installed yet; run `envoy browser setup` once.",
        setup_required=True,
    )
    assert unavailable.next_action == browser.setup_command() == "envoy browser setup"


def test_an_unrecoverable_browser_failure_offers_no_command():
    """A broken runtime is not fixed by downloading Chromium, so nothing is
    promised."""
    unavailable = browser.BrowserRuntimeUnavailable("the runtime is broken")
    assert unavailable.next_action is None


def test_a_read_without_chromium_names_the_setup_command(monkeypatch):
    monkeypatch.setattr(browser, "_chromium_executable_exists", lambda: False)
    monkeypatch.setattr(browser, "_AUTO_BROWSER_SETUP_ATTEMPTED", False)
    monkeypatch.setenv("ENVOY_DISABLE_AUTO_BROWSER_SETUP", "1")

    with pytest.raises(browser.BrowserRuntimeUnavailable) as caught:
        browser.read_page("https://example.com/article", max_work_seconds=30)

    assert caught.value.setup_required is True
    assert caught.value.next_action == "envoy browser setup"
    assert "envoy browser setup" in str(caught.value)
    assert "683 MB" in str(caught.value)


def test_a_missing_browser_is_reported_as_a_user_action():
    response = reader._blocked(
        _request(),
        reader.ErrorCode.MISSING_CAPABILITY,
        "Envoy's browser is not installed yet; run `envoy browser setup` once and retry.",
        action_required=True,
    )
    action = response.errors[0].details["needs_user_action"]
    assert action == {
        "kind": "install_capability",
        "capability": "browser",
        "command": "envoy browser setup",
    }


def test_a_missing_asr_model_is_reported_as_a_user_action():
    response = reader._blocked(
        _request(),
        reader.ErrorCode.MISSING_CAPABILITY,
        "Local video ASR is unavailable; run `envoy asr install --model base` once.",
        action_required=True,
    )
    action = response.errors[0].details["needs_user_action"]
    assert action["command"] == "envoy asr install"
    assert action["capability"] == "speech_to_text"


def test_an_ordinary_page_failure_gains_no_install_action():
    """Only a capability gap earns an install command; a page that simply had no
    text must not send anyone to `pip install`."""
    response = reader._blocked(
        _request(),
        reader.ErrorCode.EMPTY_CONTENT,
        "The page returned no readable text",
    )
    assert "needs_user_action" not in response.errors[0].details


def test_the_asr_status_names_the_fetch_command(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "whisper":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    # whisper.cpp is the preferred backend when its binary is on PATH, so a
    # host that has it would otherwise report ASR as installed.
    monkeypatch.setattr(media, "whisper_cpp_binary", lambda: None)
    monkeypatch.setattr(media, "_read_asr_config", lambda: {"enabled": True})

    status = media.media_runtime_status()

    assert status["asr_installed"] is False
    assert "envoy asr install" in str(status["asr_next_step"])


def test_transcription_without_whisper_names_the_fetch_command(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "whisper":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(media, "whisper_cpp_binary", lambda: None)

    with pytest.raises(media.MediaCapabilityMissing) as caught:
        media.transcribe_audio_file("not-there.wav")

    assert "envoy asr install --model base" in str(caught.value)
