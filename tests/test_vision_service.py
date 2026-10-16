import base64
import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_chat_vision_adapter_sends_multiple_images_without_exposing_config(tmp_path):
    from app.services.vision_service import understand_window

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"fake-image")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "识别到一个操作步骤"}}]}
    client = Mock()
    client.post.return_value = response

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test/v1",
            "HERMES_VISION_TOKEN": "secret-not-in-payload",
            "HERMES_VISION_MODEL": "deepseek-v4.1flash",
            "HERMES_VISION_PROTOCOL": "chat",
        },
        clear=False,
    ):
        result = understand_window("提取操作", "打开菜单", [str(frame)], client=client)

    payload = client.post.call_args.kwargs["json"]
    assert result["status"] == "success"
    assert payload["model"] == "deepseek-v4.1flash"
    assert "secret-not-in-payload" not in str(payload)
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image")
    assert base64.b64encode(b"fake-image").decode("ascii") in payload["messages"][0]["content"][1]["image_url"]["url"]


def test_messages_vision_adapter_parses_anthropic_style_response(tmp_path):
    from app.services.vision_service import understand_window

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"fake")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"content": [{"type": "text", "text": "表格中的关键条目"}]}
    client = Mock()
    client.post.return_value = response

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test",
            "HERMES_VISION_TOKEN": "token",
            "HERMES_VISION_PROTOCOL": "messages",
        },
        clear=False,
    ):
        result = understand_window("提取表格", "", [str(frame)], client=client)

    assert result["status"] == "success"
    assert result["text"] == "表格中的关键条目"
    assert client.post.call_args.args[0].endswith("/messages")
