import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_local_frontend_port_3001_is_allowed_by_cors(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cors.db'}")
    monkeypatch.setenv("ENVOY_STORAGE_MODE", "local")
    monkeypatch.setenv("ENVOY_LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    for name in list(sys.modules):
        if name.startswith("app."):
            sys.modules.pop(name, None)

    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:3001",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3001"
