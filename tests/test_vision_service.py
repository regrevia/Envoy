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
    assert payload["max_tokens"] == 3000
    assert payload["reasoning_effort"] == "low"
    assert "secret-not-in-payload" not in str(payload)
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image")
    assert base64.b64encode(b"fake-image").decode("ascii") in payload["messages"][0]["content"][1]["image_url"]["url"]


def test_vision_adapter_caps_provider_image_count_at_four(tmp_path, monkeypatch):
    from app.services.vision_service import understand_window

    frames = []
    for index in range(6):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"fake-image")
        frames.append(str(frame))
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
    client = Mock()
    client.post.return_value = response

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test/v1",
            "HERMES_VISION_TOKEN": "secret",
            "HERMES_VISION_MODEL": "deepseek-v4.1-flash",
            "HERMES_VISION_MAX_IMAGES": "6",
            "HERMES_VISION_PROTOCOL": "chat",
        },
        clear=False,
    ):
        result = understand_window("提取画面", "", frames, client=client)

    assert result["status"] == "success"
    assert len(client.post.call_args.kwargs["json"]["messages"][0]["content"]) == 5


def test_chat_vision_retries_empty_reasoning_only_response_with_minimal_effort(tmp_path):
    from app.services.vision_service import understand_window

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"fake-image")
    empty_response = Mock()
    empty_response.raise_for_status.return_value = None
    empty_response.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": None, "reasoning_content": "internal"}}]
    }
    answer_response = Mock()
    answer_response.raise_for_status.return_value = None
    answer_response.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "识别到徽章列表"}}]
    }
    client = Mock()
    client.post.side_effect = [empty_response, answer_response]

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test/v1",
            "HERMES_VISION_TOKEN": "secret",
            "HERMES_VISION_MODEL": "deepseek-v4.1flash",
            "HERMES_VISION_PROTOCOL": "chat",
        },
        clear=False,
    ):
        result = understand_window("提取画面知识", "", [str(frame)], client=client)

    assert result["status"] == "success"
    assert result["text"] == "识别到徽章列表"
    assert client.post.call_count == 2
    assert client.post.call_args_list[1].kwargs["json"]["reasoning_effort"] == "minimal"


def test_chat_vision_reduces_frames_when_reasoning_fallback_is_empty(tmp_path):
    from app.services.vision_service import understand_window

    frames = []
    for index in range(4):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"fake-image")
        frames.append(str(frame))
    empty = Mock()
    empty.raise_for_status.return_value = None
    empty.json.return_value = {
        "choices": [{"finish_reason": "length", "message": {"content": None}}]
    }
    answer = Mock()
    answer.raise_for_status.return_value = None
    answer.json.return_value = {
        "choices": [{"finish_reason": "stop", "message": {"content": "两张代表帧识别结果"}}]
    }
    client = Mock()
    client.post.side_effect = [empty, empty, answer]

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test/v1",
            "HERMES_VISION_TOKEN": "secret",
            "HERMES_VISION_MODEL": "deepseek-v4.1flash",
            "HERMES_VISION_PROTOCOL": "chat",
        },
        clear=False,
    ):
        result = understand_window("提取画面知识", "", frames, client=client)

    assert result["status"] == "success"
    assert result["text"] == "两张代表帧识别结果"
    assert len(client.post.call_args_list[2].kwargs["json"]["messages"][0]["content"]) == 3


def test_chat_vision_uses_one_representative_frame_and_short_prompt_as_last_fallback(tmp_path):
    from app.services.vision_service import understand_window

    frames = []
    for index in range(4):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"fake-image")
        frames.append(str(frame))
    empty = Mock()
    empty.raise_for_status.return_value = None
    empty.json.return_value = {"choices": [{"message": {"content": None}}]}
    answer = Mock()
    answer.raise_for_status.return_value = None
    answer.json.return_value = {"choices": [{"message": {"content": "代表画面中可见球员徽章选项"}}]}
    client = Mock()
    client.post.side_effect = [empty, empty, empty, answer]

    with patch.dict(
        "os.environ",
        {
            "HERMES_VISION_BASE_URL": "https://vision.example.test/v1",
            "HERMES_VISION_TOKEN": "secret",
            "HERMES_VISION_MODEL": "deepseek-v4.1flash",
            "HERMES_VISION_PROTOCOL": "chat",
        },
        clear=False,
    ):
        result = understand_window("详细长提示", "口播连续文本" * 100, frames, client=client, context="前文" * 200)

    assert result["status"] == "success"
    assert result["text"] == "代表画面中可见球员徽章选项"
    fallback_content = client.post.call_args_list[3].kwargs["json"]["messages"][0]["content"]
    assert len(fallback_content) == 2
    assert len(fallback_content[0]["text"]) < 1000


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


def test_vision_settings_are_encrypted_and_status_never_returns_api_key(tmp_path, monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("ENVOY_VISION_CONFIG_PATH", str(tmp_path / "vision.enc"))
    monkeypatch.setenv("ENVOY_VISION_KEY_PATH", str(tmp_path / "vault.key"))
    saved = vision_service.save_vision_config({
        "base_url": "https://provider.example/v1",
        "api_key": "never-return-this-secret",
        "model": "official-model-id",
        "protocol": "chat",
    })
    encrypted = (tmp_path / "vision.enc").read_bytes()
    assert b"never-return-this-secret" not in encrypted
    assert saved["configured"] is True
    assert "api_key" not in saved
    assert saved["api_key_configured"] is True
    loaded = vision_service.vision_config()
    assert loaded["token"] == "never-return-this-secret"
    assert loaded["model"] == "official-model-id"
    assert loaded["base_url"] == "https://provider.example/v1"


def test_vision_settings_survive_service_reload_from_envoy_project_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_VISION_CONFIG_PATH", str(tmp_path / "envoy-vision.enc"))
    monkeypatch.setenv("ENVOY_VISION_KEY_PATH", str(tmp_path / "envoy-vision.key"))
    from app.services import vision_service

    vision_service.save_vision_config({
        "base_url": "https://provider.example/v1",
        "api_key": "reload-secret",
        "model": "reload-model",
        "protocol": "messages",
    })

    sys.modules.pop("app.services.vision_service", None)
    reloaded = __import__("app.services.vision_service", fromlist=["vision_config"])
    config = reloaded.vision_config()

    assert config["base_url"] == "https://provider.example/v1"
    assert config["model"] == "reload-model"
    assert config["protocol"] == "messages"
    assert config["token"] == "reload-secret"


def test_vision_chat_connection_test_uses_configured_model_and_auth(tmp_path, monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("ENVOY_VISION_CONFIG_PATH", str(tmp_path / "vision.enc"))
    monkeypatch.setenv("ENVOY_VISION_KEY_PATH", str(tmp_path / "vault.key"))
    vision_service.save_vision_config({
        "base_url": "https://provider.example/v1",
        "api_key": "private-key",
        "model": "official-model-id",
        "protocol": "chat",
    })
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    client = Mock()
    client.post.return_value = response

    result = vision_service.test_vision_connection(client=client)
    call = client.post.call_args
    assert call.args[0] == "https://provider.example/v1/chat/completions"
    assert call.kwargs["headers"]["Authorization"] == "Bearer private-key"
    assert call.kwargs["json"]["model"] == "official-model-id"
    assert result["status"] == "success"


def test_messages_adapter_uses_versioned_endpoint_and_official_headers(tmp_path, monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("ENVOY_VISION_CONFIG_PATH", str(tmp_path / "messages.enc"))
    monkeypatch.setenv("ENVOY_VISION_KEY_PATH", str(tmp_path / "messages.key"))
    vision_service.save_vision_config({
        "base_url": "https://api.anthropic.example",
        "api_key": "messages-secret",
        "model": "claude-model-exact-id",
        "protocol": "messages",
    })
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"content": [{"type": "text", "text": "OK"}]}
    client = Mock()
    client.post.return_value = response

    result = vision_service.test_vision_connection(client=client)
    call = client.post.call_args
    assert call.args[0] == "https://api.anthropic.example/v1/messages"
    assert call.kwargs["headers"]["x-api-key"] == "messages-secret"
    assert call.kwargs["headers"]["anthropic-version"]
    assert call.kwargs["json"]["model"] == "claude-model-exact-id"
    assert result["status"] == "success"


def test_vision_settings_api_saves_key_but_never_returns_it(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'vision-api.db'}")
    monkeypatch.setenv("ENVOY_STORAGE_MODE", "local")
    monkeypatch.setenv("ENVOY_VISION_CONFIG_PATH", str(tmp_path / "vision.enc"))
    monkeypatch.setenv("ENVOY_VISION_KEY_PATH", str(tmp_path / "vision.key"))
    for module_name in list(sys.modules):
        if module_name.startswith("app."):
            sys.modules.pop(module_name, None)

    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import vision_service

    response = TestClient(app).put(
        "/api/v1/health/vision/config",
        json={
            "base_url": "https://provider.example/v1",
            "api_key": "api-secret-must-not-return",
            "model": "vendor-model-id",
            "protocol": "chat",
        },
    )
    assert response.status_code == 200
    assert "api-secret-must-not-return" not in response.text
    loaded = TestClient(app).get("/api/v1/health/vision")
    assert loaded.status_code == 200
    assert loaded.json()["api_key_configured"] is True
    assert "api_key" not in loaded.json()
    assert vision_service.vision_config()["token"] == "api-secret-must-not-return"

    encrypted = (tmp_path / "vision.enc").read_bytes()
    assert b"api-secret-must-not-return" not in encrypted
