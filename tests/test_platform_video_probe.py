import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_probe_url_builds_cookie_free_bounded_command(monkeypatch):
    import probe_platform_urls as probe

    captured = {}

    def fake_run(command, timeout):
        captured["command"] = command
        captured["timeout"] = timeout
        return 0, json.dumps({
            "id": "sample",
            "extractor_key": "Vimeo",
            "title": "Public sample",
            "duration": 12.5,
            "subtitles": {"en": [{}]},
            "automatic_captions": {"fr": [{}]},
        }), ""

    monkeypatch.setattr(probe, "_run_probe", fake_run)
    result = probe.probe_url("https://vimeo.example/video/1", timeout_seconds=17)

    assert result["status"] == "accepted"
    assert result["classification"] == "metadata"
    assert result["extractor"] == "Vimeo"
    assert result["subtitle_languages"] == ["en"]
    assert result["automatic_subtitle_languages"] == ["fr"]
    assert captured["timeout"] == 17
    assert "--ignore-config" in captured["command"]
    assert "--no-playlist" in captured["command"]
    assert "--dump-single-json" in captured["command"]
    assert "--cookies" not in " ".join(captured["command"])
    assert "--cookies-from-browser" not in " ".join(captured["command"])


def test_probe_url_classifies_access_network_and_unsupported_failures(monkeypatch):
    import probe_platform_urls as probe

    errors = {
        "auth": "Sign in to confirm you are not a bot; fresh cookies are needed",
        "network": "SSL: CERTIFICATE_VERIFY_FAILED HTTP Error 403",
        "unsupported": "Unsupported URL: https://example.invalid/video",
    }

    for key, message in errors.items():
        monkeypatch.setattr(
            probe,
            "_run_probe",
            lambda _command, _timeout, message=message: (1, "", message),
        )
        result = probe.probe_url(f"https://example.invalid/{key}", timeout_seconds=3)
        assert result["status"] == "blocked"
        assert result["classification"] == {
            "auth": "auth_required",
            "network": "network_error",
            "unsupported": "unsupported",
        }[key]


def test_probe_collection_continues_after_one_failure(monkeypatch):
    import probe_platform_urls as probe

    def fake_probe(url, timeout_seconds=30):
        if url.endswith("/bad"):
            return {
                "url": url,
                "status": "blocked",
                "classification": "network_error",
            }
        return {
            "url": url,
            "status": "accepted",
            "classification": "metadata",
        }

    monkeypatch.setattr(probe, "probe_url", fake_probe)
    report = probe.probe_urls(
        ["https://example.invalid/good", "https://example.invalid/bad"],
        timeout_seconds=9,
    )

    assert report["schema_version"] == "envoy.platform-video-probe.v1"
    assert report["summary"] == {
        "total": 2,
        "accepted": 1,
        "blocked": 1,
    }
    assert [item["status"] for item in report["results"]] == ["accepted", "blocked"]
