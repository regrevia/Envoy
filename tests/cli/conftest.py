import pytest


@pytest.fixture(autouse=True)
def isolate_envoy_local_materials(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
