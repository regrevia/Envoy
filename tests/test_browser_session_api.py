import sys
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_browser_session_api_exposes_create_discover_and_close_contract():
    from fastapi.testclient import TestClient
    from app.main import app

    fake = {
        "session_id": "session-1",
        "source_url": "https://v.douyin.com/example/",
        "platform": "douyin",
        "source_kind": "share",
        "status": "waiting_for_user",
        "requires_user_action": True,
    }
    with patch("app.api.v1.tasks.browser_session_manager.create", return_value=fake) as create, \
         patch("app.api.v1.tasks.browser_session_manager.discover", return_value={
             **fake,
             "status": "ready",
             "requires_user_action": False,
             "items": [{"source_url": "https://www.douyin.com/video/1", "title": "Demo"}],
             "item_count": 1,
         }) as discover, \
         patch("app.api.v1.tasks.browser_session_manager.close", return_value={"status": "closed", "session_id": "session-1"}) as close:
        client = TestClient(app)
        created = client.post(
            "/api/v1/video-playlists/browser-sessions",
            json={"source_url": "https://v.douyin.com/example/", "limit": 10},
        )
        result = client.post("/api/v1/video-playlists/browser-sessions/session-1/discover")
        closed = client.delete("/api/v1/video-playlists/browser-sessions/session-1")

    assert created.status_code == 200
    assert created.json()["requires_user_action"] is True
    assert result.status_code == 200
    assert result.json()["item_count"] == 1
    assert closed.status_code == 200
    create.assert_called_once()
    discover.assert_called_once_with("session-1")
    close.assert_called_once_with("session-1")
