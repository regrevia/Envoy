import json
import io
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_mcp_bridge_initialize_and_list_tools(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert lines[0]["result"]["protocolVersion"] == "2025-11-25"
    assert {tool["name"] for tool in lines[1]["result"]["tools"]} == {
        "envoy_read",
        "envoy_expand",
        "envoy_doctor",
        "envoy_observe_visual",
        "envoy_visual_history",
    }


def test_mcp_bridge_accepts_visual_observation_as_source_material(capsys):
    from envoy_cli import mcp

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "observe",
        "method": "tools/call",
        "params": {
            "name": "envoy_observe_visual",
            "arguments": {
                "protocol_version": "envoy.observe/1",
                "request_id": "observe-1",
                "source_url": "https://example.org/chart.png",
                "resource_id": "resource-image",
                "source_block_id": "blk-image-0001",
                "observation": "The chart shows three deployment stages.",
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["result"] == "ready"
    assert payload["blocks"][0]["origin"] == "host_visual"
    assert payload["blocks"][0]["locator"]["source_url"] == "https://example.org/chart.png"


def test_mcp_bridge_redacts_visual_observation_source_query(capsys):
    from envoy_cli import mcp

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "observe",
        "method": "tools/call",
        "params": {
            "name": "envoy_observe_visual",
            "arguments": {
                "protocol_version": "envoy.observe/1",
                "request_id": "observe-redact",
                "source_url": "https://example.org/chart.png?token=secret-value",
                "resource_id": "resource-image",
                "source_block_id": "blk-image-0001",
                "observation": "The chart is visible.",
            },
        },
    }])

    response = json.loads(capsys.readouterr().out)
    assert "secret-value" not in json.dumps(response)


def test_mcp_bridge_reads_persisted_visual_observations_with_budget(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp, reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "f" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/note",
        "source_revision": "a" * 64,
        "text": "A short note.",
        "visual_resources": [{
            "resource_id": "resource-chart",
            "source_block_id": "blk-image-0001",
            "source_url": "https://cdn.example.org/chart.png",
        }],
    })
    reader.record_visual_observation(
        material_id,
        "https://cdn.example.org/chart.png",
        "resource-chart",
        "blk-image-0001",
        "The chart shows three stages.",
    )

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "history",
        "method": "tools/call",
        "params": {
            "name": "envoy_visual_history",
            "arguments": {
                "protocol_version": "envoy.observe/1",
                "request_id": "history-1",
                "material_id": material_id,
                "resource_id": "resource-chart",
                "max_items": 2,
                "max_chars": 128,
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["result"] == "ready"
    assert payload["blocks"][0]["content"]["text"] == "The chart shows three stages."
    assert payload["blocks"][0]["origin"] == "host_visual"


def test_mcp_bridge_read_returns_protocol_result(monkeypatch, capsys):
    from envoy_cli import mcp
    from envoy_cli import reader

    html = "<html><head><title>Guide</title></head><body><p>Use the worker first.</p></body></html>"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda _url, _max_bytes, _cache=None: ("https://example.org/", html))

    exit_code = mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }, {
        "jsonrpc": "2.0",
        "id": "read-1",
        "method": "tools/call",
        "params": {
            "name": "envoy_read",
            "arguments": {
                "protocol_version": "envoy.read/1",
                "request_id": "mcp-read-1",
                "source": {"url": "https://example.org/"},
            },
        },
    }])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    response = lines[1]
    payload = json.loads(response["result"]["content"][0]["text"])
    assert exit_code == 0
    assert response["id"] == "read-1"
    assert payload["result"] == "ready"
    assert payload["blocks"][1]["content"]["text"] == "Use the worker first."


def test_mcp_bridge_rejects_unknown_tool_without_traceback(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "unknown", "arguments": {}},
    }])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    response = lines[1]
    assert exit_code == 0
    assert response["error"]["code"] == -32602


def test_mcp_bridge_discovery_advertises_modern_protocol(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "discover-1",
        "method": "server/discover",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }])
    response = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert response["result"]["resultType"] == "complete"
    assert response["result"]["supportedVersions"] == ["2026-07-28"]
    assert response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "envoy"
    assert response["result"]["ttlMs"] == 3_600_000
    assert response["result"]["cacheScope"] == "public"


def test_mcp_bridge_modern_tools_list_requires_metadata_and_is_cacheable(capsys):
    from envoy_cli import mcp

    metadata = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    exit_code = mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": metadata},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert exit_code == 0
    assert first["result"]["resultType"] == "complete"
    assert first["result"]["ttlMs"] == 3_600_000
    assert first["result"]["cacheScope"] == "public"
    assert first["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "envoy"
    assert second["error"]["code"] == -32600
    read_schema = next(tool for tool in first["result"]["tools"] if tool["name"] == "envoy_read")["inputSchema"]
    assert len(json.dumps(read_schema)) < 2500
    assert "max_response_chars" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_response_tokens" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_visual_pixels" not in read_schema["properties"]["budget"]["properties"]
    image_capability = read_schema["properties"]["host_capabilities"]["properties"]["image_input"]
    assert image_capability["enum"] == ["none", "uri"]
    descriptions = {tool["name"]: tool["description"] for tool in first["result"]["tools"]}
    assert "stated goal" in descriptions["envoy_read"].lower()
    assert "coverage" in descriptions["envoy_expand"].lower()
    assert "capabilit" in descriptions["envoy_doctor"].lower()


def test_mcp_bridge_modern_tool_call_returns_complete_structured_result(monkeypatch, capsys):
    from envoy_cli import mcp, reader

    html = "<html><head><title>Guide</title></head><body><p>Use a dedicated worker.</p></body></html>"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda _url, _max_bytes, _cache=None: ("https://example.org/", html))
    metadata = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "modern-read",
        "method": "tools/call",
        "params": {
            **metadata,
            "name": "envoy_read",
            "arguments": {
                "protocol_version": "envoy.read/1",
                "request_id": "modern-read-1",
                "source": {"url": "https://example.org/"},
            },
        },
    }])
    response = json.loads(capsys.readouterr().out)

    assert response["result"]["resultType"] == "complete"
    assert response["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "envoy"
    assert response["result"]["isError"] is False
    structured = response["result"]["structuredContent"]
    assert structured["result"] == "ready"
    assert structured["protocol_version"] == "envoy.read/1"
    assert len(json.dumps(structured)) < 1200
    paragraph = next(block for block in structured["blocks"] if block["kind"] == "paragraph")
    assert len(paragraph["locator"]["quote"]) <= 120


def test_mcp_bridge_modern_doctor_uses_short_status_text(capsys):
    from envoy_cli import mcp

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "doctor",
        "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": "envoy_doctor",
            "arguments": {},
        },
    }])
    response = json.loads(capsys.readouterr().out)

    assert response["result"]["content"][0]["text"] == "Envoy doctor ready."
    content_text = response["result"]["content"][0]["text"]
    assert len(content_text) < 200
    assert "Use a dedicated worker." not in content_text
    catalog = response["result"]["structuredContent"]["capabilities"]
    assert catalog["schema_version"] == "envoy.capabilities/1"
    assert "static_html" in catalog["inputs"]["supported"]
    assert "standalone_video" in catalog["inputs"]["unsupported"]


def test_legacy_mcp_tool_keeps_text_fallback_for_structured_material(monkeypatch, capsys):
    from envoy_cli import mcp, reader

    html = "<html><head><title>Guide</title></head><body><p>Use a dedicated worker.</p></body></html>"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda _url, _limit, _cache=None: ("https://example.org/", html))
    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "envoy_read",
            "arguments": {
                "protocol_version": "envoy.read/1",
                "request_id": "legacy-read",
                "source": {"url": "https://example.org/"},
            },
        },
    }])
    _initialize, response = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert "structuredContent" in response["result"]
    assert "Use a dedicated worker." in response["result"]["content"][0]["text"]


def test_mcp_bridge_rejects_unsupported_modern_protocol_with_supported_versions():
    from envoy_cli import mcp

    response = mcp.handle_message({
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "1900-01-01",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    })

    assert response["error"]["code"] == -32022
    assert response["error"]["data"]["supported"] == [
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
        "2024-10-07",
    ]
    assert response["error"]["data"]["requested"] == "1900-01-01"


def test_mcp_bridge_invalid_json_is_reported_without_traceback():
    from envoy_cli import mcp

    incoming = io.StringIO('{"jsonrpc":\n')
    outgoing = io.StringIO()
    exit_code = mcp.run_stream(incoming, outgoing)
    response = json.loads(outgoing.getvalue())

    assert exit_code == 0
    assert response["error"]["code"] == -32700


def test_mcp_bridge_requires_initialize_for_legacy_requests():
    from envoy_cli import mcp

    response = mcp.handle_message({
        "jsonrpc": "2.0",
        "id": "uninitialized",
        "method": "tools/list",
        "params": {},
    })

    assert response["error"]["code"] == -32602


def test_modern_server_discovery_and_legacy_initialize_can_coexist(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines([
        {
            "jsonrpc": "2.0",
            "id": "modern",
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "legacy",
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {
            "jsonrpc": "2.0",
            "id": "modern-tools",
            "method": "tools/list",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "legacy-tools",
            "method": "tools/list",
            "params": {},
        },
    ])
    modern, legacy, modern_tools, legacy_tools = [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
    ]

    assert exit_code == 0
    assert modern["result"]["supportedVersions"] == ["2026-07-28"]
    assert legacy["result"]["protocolVersion"] == "2025-11-25"
    assert modern_tools["result"]["resultType"] == "complete"
    assert "resultType" not in legacy_tools["result"]


def test_mcp_bridge_modern_requests_require_client_capabilities_metadata():
    from envoy_cli import mcp

    response = mcp.handle_message({
        "jsonrpc": "2.0",
        "id": "missing-capabilities",
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            }
        },
    })

    assert response["error"]["code"] == -32602


def test_mcp_bridge_negotiates_older_supported_legacy_version(capsys):
    from envoy_cli import mcp

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    }])
    response = json.loads(capsys.readouterr().out)

    assert response["result"]["protocolVersion"] == "2025-03-26"
