from __future__ import annotations

import json
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


def test_select_package_index_honors_existing_uv_default_index(monkeypatch):
    from envoy_cli import package_install

    monkeypatch.delenv("ENVOY_PYPI_INDEX_URL", raising=False)
    monkeypatch.delenv("UV_INDEX_URL", raising=False)
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://managed.example/simple/")
    monkeypatch.setattr(
        package_install,
        "_probe_index",
        lambda *_args, **_kwargs: pytest.fail("explicit index must not be probed"),
    )

    result = package_install.select_package_index()

    assert result.url == "https://managed.example/simple/"
    assert result.source == "explicit"


def test_package_index_rejects_embedded_credentials():
    from envoy_cli.package_install import _normalize_index

    with pytest.raises(ValueError, match="credential-free"):
        _normalize_index("https://user:password@mirror.example/simple/")


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


def test_install_media_dependencies_retries_next_index(monkeypatch, capsys):
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
    assert "source 1/2" in capsys.readouterr().err


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


def test_doctor_verifies_asr_only_when_explicitly_requested(monkeypatch, capsys):
    from envoy_cli import cli

    calls = []
    monkeypatch.setattr(cli, "media_runtime_status", lambda *, verify_asr=False: (
        calls.append(verify_asr)
        or {
            "asr_installed": True,
            "asr_model": "base",
            "asr_model_available": verify_asr,
            "asr_operational": "verified" if verify_asr else "not_checked",
        }
    ))
    monkeypatch.setattr(cli.browser_runtime, "browser_runtime_status", lambda: {})

    assert cli.main(["doctor", "--json"]) == 0
    assert cli.main(["doctor", "--json", "--verify-asr"]) == 0
    assert calls == [False, True]
    capsys.readouterr()


def test_doctor_returns_failure_when_requested_asr_check_fails(monkeypatch, capsys):
    from envoy_cli import cli

    monkeypatch.setattr(cli, "media_runtime_status", lambda *, verify_asr=False: {
        "asr_installed": True,
        "asr_model": "base",
        "asr_model_available": False,
        "asr_operational": "failed",
    })
    monkeypatch.setattr(cli.browser_runtime, "browser_runtime_status", lambda: {})

    assert cli.main(["doctor", "--json", "--verify-asr"]) == 2
    assert json.loads(capsys.readouterr().out)["runtime"]["asr_operational"] == "failed"


def test_asr_model_selection_persists_outside_replaceable_tool_environment(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import media

    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)
    assert media.selected_asr_model() == "base"

    checkpoint = tmp_path / "whisper" / "small.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"selected checkpoint")
    media.persist_asr_selection("small", media._sha256_file(checkpoint))
    monkeypatch.setenv("ENVOY_ASR_MODEL", "tiny")

    assert media.selected_asr_model() == "tiny"
    monkeypatch.delenv("ENVOY_ASR_MODEL")
    assert media.selected_asr_model() == "small"


def test_media_status_distinguishes_configured_engine_and_model_file(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import media

    cache = tmp_path / "whisper"
    cache.mkdir()
    (cache / "small.pt").write_bytes(b"fixture checkpoint")
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(cache))
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)
    media.persist_asr_selection("small", media._sha256_file(cache / "small.pt"))
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())

    status = media.media_runtime_status()

    assert status["asr_configured"] is True
    assert status["asr_installed"] is True
    assert status["asr_model"] == "small"
    assert status["asr_model_file_present"] is True
    assert status["asr_model_verified"] is True
    assert status["asr_model_available"] is True
    assert status["asr_operational"] == "not_checked"


def test_media_status_rejects_model_file_with_wrong_expected_digest(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import media

    cache = tmp_path / "whisper"
    cache.mkdir()
    (cache / "small.pt").write_bytes(b"corrupt checkpoint")
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(cache))
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)
    media.persist_asr_selection("small", "0" * 64)
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())

    status = media.media_runtime_status()

    assert status["asr_model_file_present"] is True
    assert status["asr_model_verified"] is False
    assert status["asr_model_available"] is False
    assert status["asr_operational"] == "not_checked"


def test_media_status_load_checks_engine_and_verified_model(monkeypatch, tmp_path):
    from envoy_cli import media

    cache = tmp_path / "whisper"
    cache.mkdir()
    (cache / "small.pt").write_bytes(b"checkpoint")
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(cache))
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)
    media.persist_asr_selection("small")
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media, "_model_expected_sha256", lambda _name: media._sha256_file(cache / "small.pt"))
    monkeypatch.setattr(media, "_verify_whisper_inference", lambda _name: None)

    status = media.media_runtime_status(verify_asr=True)

    assert status["asr_model_verified"] is True
    assert status["asr_model_available"] is True
    assert status["asr_operational"] == "verified"
    assert media._read_asr_config()["sha256"] == media._sha256_file(cache / "small.pt")


def test_asr_operational_check_runs_short_transcription(monkeypatch):
    import sys
    from types import SimpleNamespace
    from envoy_cli import media

    observed = {}

    class FakeModel:
        def transcribe(self, audio, *, fp16):
            observed["sample_count"] = len(audio)
            observed["fp16"] = fp16
            return {"text": ""}

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        float32="float32",
        zeros=lambda size, dtype: [0] * size,
    ))
    monkeypatch.setattr(media, "_load_whisper_model", lambda _name: FakeModel())

    media._verify_whisper_inference("base")

    assert observed == {"sample_count": 16_000, "fp16": False}


def test_doctor_reports_configured_asr_dependency_lost_after_tool_reinstall(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import media

    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ENVOY_ASR_MODEL_CACHE", str(tmp_path / "whisper"))
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)
    media.persist_asr_selection("small")
    monkeypatch.setattr(media.importlib.util, "find_spec", lambda _name: None)

    status = media.media_runtime_status()

    assert status["asr_configured"] is True
    assert status["asr_installed"] is False
    assert status["asr_model"] == "small"
    assert "rerun envoy asr install" in status["asr_next_step"]
