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
    tools = {tool["name"]: tool for tool in lines[1]["result"]["tools"]}
    assert set(tools) == {
        "envoy_discover",
        "envoy_collection_checkpoint",
        "envoy_read_local_video",
        "envoy_read",
        "envoy_expand",
        "envoy_replay",
        "envoy_search",
        "envoy_doctor",
        "envoy_observe_visual",
        "envoy_visual_history",
    }
    assert "needs_host response is a handoff, not a terminal state" in tools["envoy_read"]["description"]
    assert "inspect every" in tools["envoy_read"]["description"]
    assert "one observation per inspected resource" in tools["envoy_observe_visual"]["description"]
    assert "idempotency_key" in tools["envoy_read_local_video"]["inputSchema"]["required"]


def test_mcp_replay_dispatches_bounded_request_to_new_session(monkeypatch, capsys):
    from envoy_cli import mcp
    from envoy_core.schemas.read_v1 import ExecutionState, ReadResponse, ReadResult

    observed = {}

    def replay(request):
        observed.update(request.model_dump())
        return ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            material_id="mat-" + "a" * 24,
            source_revision=request.source_revision,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        )

    monkeypatch.setattr(mcp, "replay_material", replay)
    mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "envoy_replay",
                "arguments": {
                    "request_id": "mcp-replay-1",
                    "material_id": "mat-" + "b" * 24,
                    "source_revision": "c" * 64,
                    "source_session_id": "session-old",
                    "session_id": "session-new",
                    "max_chars": 1000,
                },
            },
        },
    ])

    response = [json.loads(line) for line in capsys.readouterr().out.splitlines()][1]
    assert response["result"]["structuredContent"]["result"] == "partial"
    assert observed["source_session_id"] == "session-old"
    assert observed["session_id"] == "session-new"


def test_mcp_local_video_read_forwards_selected_path_and_goal(monkeypatch, tmp_path, capsys):
    from envoy_cli import mcp
    from envoy_core.schemas.read_v1 import (
        ExecutionState,
        ReadResponse,
        ReadResult,
    )

    video_path = tmp_path / "user-selected.mp4"
    video_path.write_bytes(b"fixture")
    observed = {}

    def read(request):
        observed.update({
            "path": request.path,
            "goal": request.goal,
            "idempotency_key": request.idempotency_key,
        })
        return ReadResponse(
            request_id=request.request_id,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        )

    monkeypatch.setattr(mcp, "read_local_video", read)
    mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "envoy_read_local_video",
                "arguments": {
                    "request_id": "mcp-local-video",
                    "idempotency_key": "local-operation-1",
                    "path": str(video_path),
                    "goal": "Extract spoken product claims",
                },
            },
        },
    ])

    response = [json.loads(line) for line in capsys.readouterr().out.splitlines()][1]
    assert response["result"]["structuredContent"]["result"] == "partial"
    assert observed == {
        "path": str(video_path),
        "goal": "Extract spoken product claims",
        "idempotency_key": "local-operation-1",
    }


def test_mcp_collection_checkpoint_create_update_and_get(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "envoy_collection_checkpoint",
                "arguments": {
                    "action": "create",
                    "collection_id": "mcp-collection",
                    "run_id": "mcp-run",
                    "profile_url": "https://www.douyin.com/user/creator",
                    "operation_id": "create-op",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "envoy_collection_checkpoint",
                "arguments": {
                    "action": "discovery_snapshot",
                    "collection_id": "mcp-collection",
                    "run_id": "mcp-run",
                    "expected_revision": 1,
                    "operation_id": "discovery-op",
                    "usage": {"snapshot": {
                        "discovery_revision": "d" * 20,
                        "candidates": [{"candidate_id": "douyin-candidate"}],
                    }},
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "envoy_collection_checkpoint",
                "arguments": {
                    "action": "select",
                    "collection_id": "mcp-collection",
                    "run_id": "mcp-run",
                    "expected_revision": 2,
                    "operation_id": "select-op",
                    "candidate_ids": ["douyin-candidate"],
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "envoy_collection_checkpoint",
                "arguments": {
                    "action": "get",
                    "collection_id": "mcp-collection",
                    "run_id": "mcp-run",
                },
            },
        },
    ])

    responses = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    stored = responses[-1]["result"]["structuredContent"]
    assert stored["manifest_revision"] == 3
    assert stored["selected_candidate_ids"] == ["douyin-candidate"]
    assert responses[3]["result"]["structuredContent"]["manifest_revision"] == 3


def test_mcp_discover_forwards_bounded_request(monkeypatch, capsys):
    from envoy_cli import mcp

    monkeypatch.setattr(mcp, "discover_profile", lambda url, **kwargs: {
        "profile_url": url,
        "discovery_revision": "a" * 20,
        "candidates": [],
        **kwargs,
    })
    mcp.run_lines([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "envoy_discover",
                "arguments": {
                    "profile_url": "https://www.douyin.com/user/creator",
                    "limit": 12,
                    "max_work_seconds": 45,
                },
            },
        },
    ])

    response = [json.loads(line) for line in capsys.readouterr().out.splitlines()][1]
    payload = response["result"]["structuredContent"]
    assert payload["limit"] == 12
    assert payload["max_work_seconds"] == 45
    assert "discovered 0 candidate" in response["result"]["content"][0]["text"]
def test_mcp_bridge_hands_direct_video_to_native_video_host(monkeypatch, capsys):
    from envoy_cli import mcp, reader

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args: (_ for _ in ()).throw(
        reader.DetectedMedia({
            "url": "https://cdn.example.org/clip.mp4",
            "mime_type": "video/mp4",
        })
    ))
    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "video",
        "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": "envoy_read",
            "arguments": {
                "protocol_version": "envoy.read/1",
                "request_id": "mcp-direct-video",
                "source": {
                    "url": "https://cdn.example.org/clip.mp4",
                    "kind": "video",
                },
                "goal": "Answer the user's question about this video",
                "host_capabilities": {"video_input": "native"},
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    payload = response["result"]["structuredContent"]
    assert "inspect the delivered video" in response["result"]["content"][0]["text"]
    assert payload["result"] == "needs_host"
    assert payload["needs"][0]["type"] == "inspect_video"
    assert payload["resources"][0]["delivery"]["uri"].endswith("clip.mp4")


def test_mcp_needs_host_visual_text_requires_observe_for_each_resource():
    from envoy_cli.mcp import _tool_content_text

    text = _tool_content_text({
        "result": "needs_host",
        "needs": [{
            "type": "inspect_visual",
            "resource_ids": ["frame-a", "frame-b"],
            "source_block_ids": ["block-a", "block-b"],
        }],
    }, modern=True)

    assert "each listed resource" in text
    assert "envoy_observe_visual" in text
    assert "material_id" in text
    assert "resource_id" in text
    assert "source_block_id" in text
    assert "session_id" in text
    assert "source URL" in text
    assert "not a terminal state" in text
    assert "do not repeat envoy_read" in text
    assert "uninspected frames" in text


def test_observation_source_url_accepts_content_addressed_local_video_source():
    from envoy_cli.mcp import _observation_source_url

    value = "envoy-local-video://sha256/" + "a" * 64 + "/clip.mp4"

    assert _observation_source_url(value) == value


def test_observation_source_url_rejects_malformed_local_video_identity():
    import pytest
    from envoy_cli.mcp import _observation_source_url

    with pytest.raises(ValueError, match="content-addressed"):
        _observation_source_url("envoy-local-video://sha256/not-a-hash/clip.mp4")


def test_mcp_bridge_rejects_unlinked_visual_observation(capsys):
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
    assert payload["result"] == "blocked"
    assert payload["blocks"] == []
    assert payload["observation_persistence"]["reason"] == "material_id_missing"


def test_mcp_visual_observation_infers_source_from_needs_resource_binding(
    tmp_path,
    monkeypatch,
    capsys,
):
    from envoy_cli import mcp, reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "c" * 24
    source_url = "https://example.org/video"
    reader._store_material(material_id, {
        "source_url": source_url,
        "source_revision": "d" * 64,
        "session_id": "visual-session",
        "text": "",
        "visual_resources": [{
            "resource_id": "frame-1",
            "source_block_id": "blk-frame-1",
            "source_url": source_url,
            "frame_path": str(tmp_path / "frame-1.jpg"),
            "timestamp": 2.0,
        }],
    })
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
                "request_id": "observe-from-needs",
                "material_id": material_id,
                "session_id": "visual-session",
                "resource_id": "frame-1",
                "source_block_id": "blk-frame-1",
                "observation": "At 00:02 the frame shows a three-column diagram.",
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload["protocol_version"] == "envoy.observe/1"
    assert payload["result"] == "ready"
    assert payload["blocks"][0]["locator"]["source_url"] == source_url
    assert payload["blocks"][0]["content"]["text"] == (
        "At 00:02 the frame shows a three-column diagram."
    )


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


def test_mcp_bridge_advertises_goal_directed_material_search(capsys):
    from envoy_cli import mcp

    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "tools",
        "method": "tools/list",
        "params": {},
    }])
    tools = json.loads(capsys.readouterr().out.splitlines()[1])["result"]["tools"]
    search = next(tool for tool in tools if tool["name"] == "envoy_search")
    assert "query" in search["inputSchema"]["properties"]


def test_mcp_bridge_search_returns_source_located_goal_match(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp, reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "a" * 24
    source = "Overview.\n\nDeployment command: envoy start --worker.\n\nWrap up."
    reader._store_material(material_id, {
        "source_url": "https://example.org/guide",
        "source_revision": "b" * 64,
        "title_text": "Guide",
        "text": source,
        "budget_state": {
            "max_total_response_chars": 2000,
            "max_total_response_tokens": 500,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })
    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "search",
        "method": "tools/call",
        "params": {
            "name": "envoy_search",
            "arguments": {
                "protocol_version": "envoy.search/1",
                "request_id": "mcp-search-1",
                "material_id": material_id,
                "query": "deployment command",
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["blocks"][0]["content"]["text"] == "Deployment command: envoy start --worker."
    assert payload["blocks"][0]["locator"]["source_url"] == "https://example.org/guide"


def test_mcp_bridge_rejects_mismatched_visual_observation_as_error(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp, reader

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    material_id = "mat-" + "d" * 24
    reader._store_material(material_id, {
        "source_url": "https://example.org/page",
        "source_revision": "d" * 64,
        "text": "A page.",
        "visual_resources": [{
            "resource_id": "resource-owned",
            "source_block_id": "blk-image-0001",
            "source_url": "https://cdn.example.org/chart.png",
        }],
    })
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
                "request_id": "observe-mismatch",
                "material_id": material_id,
                "source_url": "https://cdn.example.org/other.png",
                "resource_id": "resource-not-owned",
                "source_block_id": "blk-image-0009",
                "observation": "A purported chart observation.",
            },
        },
    }])

    response = json.loads(capsys.readouterr().out.splitlines()[1])
    result = response["result"]
    payload = result["structuredContent"]
    assert result["isError"] is True
    assert payload["result"] == "blocked"
    assert payload["blocks"] == []
    assert payload["observation_persistence"]["reason"] == "resource_mismatch"


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
    assert len(json.dumps(read_schema)) < 2700
    assert "idempotency_key" in read_schema["properties"]
    assert "ocr_language" in read_schema["properties"]
    assert read_schema["properties"]["ocr_language"]["pattern"] == "^[A-Za-z0-9_-]+$"
    assert "max_response_chars" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_response_tokens" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_response_chars" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_visual_pixels" in read_schema["properties"]["budget"]["properties"]
    assert "max_visual_pixels_per_input" in read_schema["properties"]["budget"]["properties"]
    assert "max_visual_clip_seconds" in read_schema["properties"]["budget"]["properties"]
    assert "max_total_visual_clip_seconds" in read_schema["properties"]["budget"]["properties"]
    assert "max_work_seconds" in read_schema["properties"]["budget"]["properties"]
    assert "max_expansion_rounds" in read_schema["properties"]["budget"]["properties"]
    assert "max_optional_model_calls" in read_schema["properties"]["budget"]["properties"]
    image_capability = read_schema["properties"]["host_capabilities"]["properties"]["image_input"]
    assert image_capability["enum"] == ["none", "local_file", "uri", "inline"]
    video_capability = read_schema["properties"]["host_capabilities"]["properties"]["video_input"]
    assert video_capability["enum"] == ["none", "native", "frames"]
    scope = read_schema["properties"]["scope"]["properties"]
    assert scope["kind"]["enum"] == ["current_resource", "range", "collection"]
    assert "include_visual" in scope
    assert "start_seconds" in scope
    assert "end_seconds" in scope
    assert "locator" in scope
    assert "follow_links" in scope
    assert "max_items" in scope
    assert "max_depth" in scope
    assert "video" in read_schema["properties"]["source"]["properties"]["kind"]["enum"]
    assert "raw_media" in read_schema["properties"]["retention"]["properties"]
    host = read_schema["properties"]["host_capabilities"]["properties"]
    assert host["image_input"]["enum"] == ["none", "local_file", "uri", "inline"]
    assert "tool_execution" in host
    assert "usage_reporting" in host
    assert "async_execution" in host
    assert host["usage_reporting"]["enum"] == ["actual", "estimated", "unknown"]
    retention = read_schema["properties"]["retention"]
    assert retention["additionalProperties"] is False
    assert retention["properties"]["raw_media"]["enum"] == [
        "ephemeral",
        "session",
        "persistent",
    ]
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
    structured = response["result"]["structuredContent"]
    assert structured["build"]["channel"] == "source-checkout"
    assert structured["build"]["version"] == structured["cli_version"]
    catalog = structured["capabilities"]
    runtime = structured["runtime"]
    assert catalog["schema_version"] == "envoy.capabilities/1"
    assert "static_html" in catalog["inputs"]["supported"]
    assert "standalone_audio" in catalog["inputs"]["unsupported"]
    assert any(
        item["id"] == "direct_video_handoff"
        for item in catalog["inputs"]["conditional"]
    )
    assert any(
        item["id"] == "platform_video_subtitles"
        for item in catalog["inputs"]["conditional"]
    )
    assert any(
        item["id"] == "local_video_asr"
        for item in catalog["inputs"]["conditional"]
    )
    assert any(
        item["id"] == "platform_video_frames"
        for item in catalog["inputs"]["conditional"]
    )
    assert "timed_video_transcript" in catalog["outputs"]
    assert "leased_source_timed_video_frames" in catalog["outputs"]
    assert runtime["yt_dlp"]
    assert "yt_dlp_supported" in runtime
    assert "asr_installed" in runtime


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


def test_unexpected_tool_failure_returns_internal_error_and_keeps_stream_alive(
    monkeypatch,
):
    from envoy_cli import mcp

    monkeypatch.setattr(
        mcp,
        "discover_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private diagnostic detail")),
    )
    incoming = io.StringIO("\n".join([
        json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }),
        json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "envoy_discover",
                "arguments": {"profile_url": "https://www.douyin.com/user/creator"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}),
        "",
    ]))
    outgoing = io.StringIO()

    assert mcp.run_stream(incoming, outgoing) == 0
    responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]

    assert responses[1]["error"]["code"] == -32603
    assert responses[1]["error"]["message"] == "Internal error while handling MCP tool call"
    assert responses[2]["result"]["tools"]
    assert all("private diagnostic detail" not in line for line in outgoing.getvalue().splitlines())


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
