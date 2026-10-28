import pytest


@pytest.fixture(autouse=True)
def isolate_envoy_local_materials(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    # ASR checkpoints are keyed by audio digest, so two tests that fabricate the
    # same bytes would otherwise resume each other's work through ~/.envoy/asr
    # and make the suite order- and machine-dependent.
    monkeypatch.setenv("ENVOY_ASR_CHECKPOINT_DIR", str(tmp_path / "asr"))
    # Feature switches live beside ~/.envoy by default; a test must never write
    # (or read) the real user's config file.
    monkeypatch.setenv("ENVOY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ENVOY_CREDENTIAL_HANDOFF", raising=False)
