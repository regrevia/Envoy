from __future__ import annotations

import sys

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))


def test_select_package_index_honors_explicit_override(monkeypatch):
    from envoy_cli import package_install

    monkeypatch.setenv("ENVOY_PYPI_INDEX_URL", "https://mirror.example/simple/")
    monkeypatch.setattr(
        package_install,
        "_probe_index",
        lambda *_args, **_kwargs: pytest.fail("explicit index must not be probed"),
    )

    result = package_install.select_package_index()

    assert result.url == "https://mirror.example/simple/"
    assert result.source == "explicit"


def test_probe_targets_package_name_under_simple_index(monkeypatch):
    from envoy_cli import package_install

    requested = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_request(request, timeout):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr(package_install, "urlopen", open_request)

    result = package_install._probe_index(
        package_install.PackageIndex("https://mirror.example/pypi/simple/", "test")
    )

    assert result.ok is True
    assert requested == [
        "https://mirror.example/pypi/simple/openai-whisper/",
        "https://mirror.example/pypi/simple/torch/",
    ]


def test_select_package_index_prefers_fast_healthy_candidate(monkeypatch):
    from envoy_cli import package_install

    candidates = [
        package_install.PackageIndex("https://slow.example/simple/", "configured"),
        package_install.PackageIndex("https://fast.example/simple/", "configured"),
    ]
    monkeypatch.setattr(package_install, "default_package_indexes", lambda: candidates)
    monkeypatch.setattr(
        package_install,
        "_probe_index",
        lambda index, **_kwargs: (
            package_install.IndexProbe(index=index, ok=True, latency_ms=220)
            if "slow" in index.url
            else package_install.IndexProbe(index=index, ok=True, latency_ms=40)
        ),
    )

    result = package_install.select_package_index()

    assert result.url == "https://fast.example/simple/"
    assert result.source == "adaptive"


def test_install_media_dependencies_retries_next_index(monkeypatch):
    from envoy_cli import package_install

    indexes = [
        package_install.PackageIndex("https://first.example/simple/", "configured"),
        package_install.PackageIndex("https://second.example/simple/", "configured"),
    ]
    calls = []

    monkeypatch.setattr(package_install, "select_package_indexes", lambda: indexes)
    monkeypatch.setattr(package_install, "resolve_uv", lambda: "/usr/local/bin/uv")

    def run(command, **_kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0 if len(calls) == 2 else 1, "stdout": "", "stderr": "failed"})()

    monkeypatch.setattr(package_install.subprocess, "run", run)
    monkeypatch.setattr(package_install.time, "sleep", lambda *_args: None)

    result = package_install.install_media_dependencies(model="base")

    assert result["index_url"] == indexes[1].url
    assert calls[0][calls[0].index("--index-url") + 1] == indexes[0].url
    assert calls[1][calls[1].index("--index-url") + 1] == indexes[1].url
    assert "openai-whisper==20250625" in calls[1]


def test_asr_install_command_installs_engine_before_preparing_model(
    monkeypatch,
    capsys,
):
    from envoy_cli import cli

    calls = []
    monkeypatch.setattr(
        cli,
        "install_media_dependencies",
        lambda *, model: calls.append(("install", model)) or {"index_url": "https://mirror.example/simple/"},
    )
    monkeypatch.setattr(
        cli,
        "prepare_asr_model",
        lambda model: calls.append(("prepare", model)) or {"available": True},
    )

    result = cli.main(["asr", "install", "--model", "base"])
    import json
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert calls == [("install", "base"), ("prepare", "base")]
    assert payload["index_url"] == "https://mirror.example/simple/"
    assert payload["available"] is True
