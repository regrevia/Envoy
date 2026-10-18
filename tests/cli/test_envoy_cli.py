import json
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT_DIR / "packages" / "envoy-core" / "src"
CLI_DIR = ROOT_DIR / "packages" / "envoy-cli" / "src"
for path in (CORE_DIR, CLI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_cli_read_returns_protocol_material_for_public_html(monkeypatch, capsys):
    from envoy_cli import cli

    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = "https://example.org/article"
        content = b"""
        <html><head><title>Deployment Guide</title></head>
        <body><nav>Menu</nav><main><h1>Deployment Guide</h1>
        <p>Run the worker before starting the API.</p>
        <img src="/diagram.png" alt="Architecture diagram">
        </main><footer>Footer</footer></body></html>
        """
        text = content.decode("utf-8")

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-static-1",
        "source": {"url": "https://example.org/article"},
        "goal": "Extract the deployment steps",
    }

    exit_code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))

    assert exit_code == 0
    response = json.loads(capsys.readouterr().out)
    assert response["protocol_version"] == "envoy.read/1"
    assert response["result"] == "ready"
    assert response["execution_state"] == "completed"
    assert any("Run the worker before starting the API." in block["content"].get("text", "") for block in response["blocks"])
    assert response["resources"][0]["delivery"]["uri"] == "https://example.org/diagram.png"


def test_cli_read_returns_structured_blocked_response_for_private_url(capsys):
    from envoy_cli import cli

    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-private-1",
        "source": {"url": "http://127.0.0.1:8140/video"},
    }

    exit_code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))

    assert exit_code == 2
    response = json.loads(capsys.readouterr().out)
    assert response["result"] == "blocked"
    assert response["errors"][0]["code"] == "source_unavailable"
    assert response["errors"][0]["action_required"] is False


def test_cli_doctor_is_json_and_does_not_report_secrets(capsys):
    from envoy_cli import cli

    exit_code = cli.main(["doctor", "--json"])

    assert exit_code == 0
    response = json.loads(capsys.readouterr().out)
    assert response["tool"] == "envoy"
    assert response["protocol_version"] == "envoy.read/1"
    assert "api_key" not in json.dumps(response).lower()
