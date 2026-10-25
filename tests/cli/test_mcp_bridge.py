import json
import io
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


import pytest


@pytest.fixture(autouse=True)
def _isolate_retained_reads():
    """The bridge retains read material per process, so each test starts empty."""
    from envoy_cli import mcp

    mcp._RETAINED_READS.clear()
    yield
    mcp._RETAINED_READS.clear()


def test_mcp_bridge_initialize_and_list_tools(monkeypatch, capsys):
    from envoy_cli import mcp

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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


def test_core_toolset_filters_tools_and_rejects_hidden_tool(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "envoy_doctor", "arguments": {}},
            },
        ],
        toolset="core",
    )

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    assert set(lines[1]["result"]["tools"][0]) >= {"name", "description"}
    names = {tool["name"] for tool in lines[1]["result"]["tools"]}
    assert names == {
        "envoy_read",
        "envoy_expand",
        "envoy_search",
        "envoy_observe_visual",
    }
    error = lines[2]["error"]
    assert error["code"] == -32602
    assert "not exposed by the 'core' MCP toolset" in error["message"]
    assert error["data"]["toolset"] == "core"
    assert error["data"]["available"] == [
        "envoy_read",
        "envoy_expand",
        "envoy_search",
        "envoy_observe_visual",
    ]
    assert "--toolset full" in error["data"]["hint"]


def test_unknown_tool_is_distinct_from_hidden_tool(capsys):
    from envoy_cli import mcp

    exit_code = mcp.run_lines(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "envoy_nope", "arguments": {}},
            },
        ],
        toolset="full",
    )

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 0
    error = lines[1]["error"]
    assert error["code"] == -32602
    assert error["message"] == "Unknown tool: 'envoy_nope'"
    assert "not exposed" not in error["message"]
    assert error["data"]["toolset"] == "full"


def test_mcp_toolset_normalization_and_errors(monkeypatch):
    from envoy_cli import mcp

    assert mcp.normalize_toolset(" CORE ") == "core"
    assert mcp.normalize_toolset(None) == "core"
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")
    assert mcp.normalize_toolset(None) == "full"
    monkeypatch.setenv(mcp.TOOLSET_ENV, "core")
    assert mcp.normalize_toolset(None) == "core"
    monkeypatch.setenv(mcp.TOOLSET_ENV, "bogus")
    try:
        mcp.normalize_toolset(None)
    except ValueError as exc:
        assert "unknown MCP toolset 'bogus'" in str(exc)
    else:
        raise AssertionError("unknown toolset must raise ValueError")


def test_mcp_replay_dispatches_bounded_request_to_new_session(monkeypatch, capsys):
    from envoy_cli import mcp

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")
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

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")
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

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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


def test_mcp_visual_tool_description_explains_needs_index_mapping(capsys):
    from envoy_cli import mcp

    mcp.run_lines([
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        },
        {"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
    ])

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    tools = {tool["name"]: tool for tool in lines[1]["result"]["tools"]}
    description = tools["envoy_observe_visual"]["description"]
    assert "resource_ids" in description
    assert "source_block_ids" in description
    assert "same index" in description
    assert "action_id" in description
    assert "purpose" in description


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

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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


def test_mcp_bridge_modern_tools_list_requires_metadata_and_is_cacheable(monkeypatch, capsys):
    from envoy_cli import mcp

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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
    # The declaration is part of every request prefix, so it carries only what a
    # caller must send. Plumbing and budget stay server-side; `host_capabilities`
    # and `retention` are declared because they gate frame delivery, and a switch
    # that only lived in prose was not discoverable (a model read the text-only
    # reply as "Envoy cannot see video" and opened the page in its own browser).
    assert len(json.dumps(read_schema)) < 2400
    assert read_schema["required"] == ["source", "goal"]
    assert "video" in read_schema["properties"]["source"]["properties"]["kind"]["enum"]
    assert "start_seconds" in read_schema["properties"]
    assert "end_seconds" in read_schema["properties"]
    assert "include_visual" in read_schema["properties"]
    for undeclared in (
        "idempotency_key",
        "protocol_version",
        "request_id",
        "session_id",
        "budget",
        "ocr_language",
        "scope",
    ):
        assert undeclared not in read_schema["properties"]
    assert "frames" in read_schema["properties"]["host_capabilities"]["properties"]["video_input"]["enum"]
    assert "persistent" in read_schema["properties"]["retention"]["properties"]["raw_media"]["enum"]
    declared_bytes = len(json.dumps(first["result"]["tools"], ensure_ascii=False).encode())
    assert declared_bytes < 25_000
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


def test_mcp_bridge_modern_doctor_uses_short_status_text(monkeypatch, capsys):
    from envoy_cli import mcp

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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

    # These cover advanced tools, which the bounded toolset hides.
    monkeypatch.setenv(mcp.TOOLSET_ENV, "full")

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


def test_mcp_bridge_compact_arguments_are_completed_server_side(monkeypatch, capsys):
    """A declaration-only call must reach the handler without plumbing fields."""

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
        "id": "compact-read",
        "method": "tools/call",
        "params": {
            **metadata,
            "name": "envoy_read",
            "arguments": {
                "source": {"url": "https://example.org/"},
                "goal": "find the deployment guidance",
                "start_seconds": 0,
                "end_seconds": 30,
            },
        },
    }])
    response = json.loads(capsys.readouterr().out)

    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["result"] == "ready"


def test_mcp_bridge_flattened_range_becomes_a_scope(monkeypatch):
    from envoy_cli import mcp
    from envoy_core.schemas.read_v1 import ReadRequest

    prepared = mcp._server_arguments(
        "envoy_read",
        {
            "source": {"url": "https://example.org/"},
            "goal": "g",
            "start_seconds": 10,
            "end_seconds": 60,
            "include_visual": True,
        },
    )
    request = ReadRequest.model_validate(prepared)
    assert request.scope.kind == "range"
    assert request.scope.start_seconds == 10
    assert request.scope.end_seconds == 60
    assert request.scope.include_visual is True
    # The envelope is server-owned and unique per call.
    assert prepared["request_id"].startswith("mcp-")
    assert mcp._server_arguments("envoy_read", {"source": {"url": "u"}, "goal": "g"})["request_id"] != prepared["request_id"]


def test_mcp_bridge_explicit_scope_and_budget_win_over_defaults(monkeypatch):
    from envoy_cli import mcp
    from envoy_core.schemas.read_v1 import ReadRequest

    prepared = mcp._server_arguments(
        "envoy_read",
        {
            "source": {"url": "https://example.org/"},
            "goal": "g",
            "scope": {"kind": "range", "start_seconds": 5, "end_seconds": 15},
            "budget": {"max_response_chars": 20_000},
            "session_id": "session-a",
            "request_id": "caller-chosen",
        },
    )
    request = ReadRequest.model_validate(prepared)
    assert request.scope.start_seconds == 5
    assert request.budget.max_response_chars == 20_000
    assert request.session_id == "session-a"
    assert request.request_id == "caller-chosen"


def test_mcp_bridge_undeclared_advanced_blocks_are_still_accepted():
    from envoy_cli import mcp
    from envoy_core.schemas.read_v1 import (
        MaterialSearchRequest,
        VisualObservationHistoryRequest,
    )

    material_id = "mat-" + "a" * 24
    model = MaterialSearchRequest
    model.model_validate(
        mcp._server_arguments("envoy_search", {"material_id": material_id, "query": "q"})
    )
    VisualObservationHistoryRequest.model_validate(
        mcp._server_arguments("envoy_visual_history", {"material_id": material_id})
    )
    schema = next(
        tool for tool in mcp.tools_for_toolset("core") if tool["name"] == "envoy_search"
    )["inputSchema"]
    assert schema["required"] == ["material_id", "query"]


def test_mcp_bridge_reuses_one_session_identity_per_process():
    """Follow-up calls must address the materials the same bridge persisted."""

    from envoy_cli import mcp

    first = mcp._server_arguments("envoy_read", {"source": {"url": "https://example.org/"}, "goal": "g"})
    second = mcp._server_arguments("envoy_read", {"source": {"url": "https://example.org/"}, "goal": "g"})
    assert first["session_id"] == second["session_id"]
    assert first["session_id"]
    # The request envelope stays per-call so nothing is mistaken for a replay.
    assert first["request_id"] != second["request_id"]

    explicit = mcp._server_arguments(
        "envoy_read", {"source": {"url": "https://example.org/"}, "goal": "g", "session_id": "mine"}
    )
    assert explicit["session_id"] == "mine"

    # Replay is defined by the difference between two sessions; never invented.
    replay = mcp._server_arguments("envoy_replay", {"material_id": "mat-x", "source_revision": "r"})
    assert replay.get("session_id") is None


class _LocalReadStub:
    protocol_version = "envoy.read/1"

    def __init__(self):
        self.path = None

    def model_dump(self, **_kwargs):
        return {
            "protocol_version": "envoy.read/1",
            "result": "ready",
            "blocks": [{"id": "blk-1", "kind": "paragraph", "content": {"type": "text", "text": "local"}}],
            "errors": [],
        }


def _call_tool(capsys, mcp, name, arguments, modern=False):
    params = {"name": name, "arguments": arguments}
    if modern:
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    mcp.run_lines([{
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {"protocolVersion": "2026-07-28" if modern else "2025-11-25"},
    }, {
        "jsonrpc": "2.0",
        "id": "call",
        "method": "tools/call",
        "params": params,
    }])
    return json.loads(capsys.readouterr().out.splitlines()[1])["result"]


def test_envoy_read_routes_a_file_url_to_the_local_pipeline(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    captured = {}

    def fake_read_local_video(request):
        captured["request"] = request
        return _LocalReadStub()

    def fail_public(*_args, **_kwargs):
        raise AssertionError("a local file must not be fetched as a public URL")

    monkeypatch.setattr(mcp, "read_local_video", fake_read_local_video)
    monkeypatch.setattr(mcp, "read_public_page", fail_public)

    result = _call_tool(
        capsys,
        mcp,
        "envoy_read",
        {"source": {"url": f"file://{video}"}, "goal": "what does this clip say"},
    )

    request = captured["request"]
    assert Path(request.path) == video
    assert request.goal == "what does this clip say"
    # Crash recovery needs a stable identity derived from the file, not a random one.
    same = mcp._local_video_arguments({"request_id": "r", "goal": "g"}, str(video))["idempotency_key"]
    assert request.idempotency_key == same
    assert request.idempotency_key.startswith("local-")
    assert result["isError"] is False
    assert result["structuredContent"]["result"] == "ready"


def test_envoy_read_accepts_an_absolute_local_path(tmp_path, monkeypatch, capsys):
    from envoy_cli import mcp

    video = tmp_path / "绝对 路径.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr(mcp, "read_local_video", lambda request: _LocalReadStub())
    monkeypatch.setattr(
        mcp,
        "read_public_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no fetch")),
    )

    result = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": str(video)}, "goal": "g"})

    assert result["isError"] is False
    assert mcp._local_source_path({"source": {"url": str(video)}}) == str(video)


def test_envoy_read_reports_a_missing_local_file_and_its_reason(capsys):
    from envoy_cli import mcp

    missing = Path("/nonexistent/envoy-does-not-exist.mp4")
    result = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": f"file://{missing}"}, "goal": "g"})

    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["result"] == "blocked"
    error = payload["errors"][0]
    assert error["code"] == "source_unavailable"
    assert error["details"]["kind"] == "local_file"
    assert error["details"]["reason"] == "not_found"
    # The reason has to reach the model, not just structuredContent.
    text = result["content"][0]["text"]
    assert "not found" in text
    assert error["message"] in text


def test_envoy_read_leaves_public_urls_on_the_public_reader(monkeypatch, capsys):
    from envoy_cli import mcp
    from envoy_cli import reader

    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        # Readable body on purpose: the public reader keeps pages of 20+ characters
        # on the plain HTTP path, so this test does not depend on a browser install.
        lambda _url, _max_bytes, _cache=None: (
            "https://example.org/",
            "<html><body><p>Hello there. This page keeps a readable paragraph.</p></body></html>",
        ),
    )
    monkeypatch.setattr(
        mcp,
        "read_local_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no local read")),
    )

    result = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": "https://example.org/"}, "goal": "g"})

    assert result["isError"] is False
    assert mcp._local_source_path({"source": {"url": "https://example.org/"}}) is None


class _PublicReadStub:
    protocol_version = "envoy.read/1"

    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, **_kwargs):
        return dict(self._payload)


def test_envoy_read_serves_retained_material_when_a_reread_is_blocked(capsys, monkeypatch):
    from envoy_cli import mcp
    from envoy_cli.reader import PublicPageError
    from envoy_core.schemas.read_v1 import ErrorCode

    mcp._RETAINED_READS.clear()
    first_payload = {
        "protocol_version": "envoy.read/1",
        "request_id": "r1",
        "session_id": mcp._PROCESS_SESSION_ID,
        "material_id": "mat-retained",
        "source_revision": "rev-1",
        "execution_state": "completed",
        "result": "partial",
        "blocks": [{"id": "blk-1", "kind": "paragraph", "content": {"type": "text", "text": "evidence"}}],
        "resources": [],
        "errors": [{"code": "source_unavailable", "message": "ransom note: only page text was available"}],
    }
    calls = {"n": 0}

    def fake_read_public_page(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return _PublicReadStub(first_payload)
        raise PublicPageError("Douyin requires sign-in", ErrorCode.AUTH_REQUIRED)

    monkeypatch.setattr(mcp, "read_public_page", fake_read_public_page)
    url = "https://v.douyin.com/HXOTfJ1neq4/"

    first = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": url}, "goal": "g"}, modern=True)
    second = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": url}, "goal": "g"}, modern=True)

    assert first["isError"] is False
    assert second["isError"] is False, "a blocked re-read must fall back to the retained material"
    payload = second["structuredContent"]
    assert payload["served_from_retained_material"] is True
    assert payload["material_id"] == "mat-retained"
    assert payload["source_revision"] == "rev-1"
    assert payload["result"] == "partial"
    assert payload["blocks"][0]["content"]["text"] == "evidence"
    notice = payload["notices"][-1]
    assert notice["code"] == "served_from_retained_material"
    assert "nothing was re-fetched" in notice["message"]
    text = second["content"][0]["text"]
    assert "material_id=mat-retained" in text
    assert "not re-fetched" in text
    # The repeat read answered from the retained material: the source was not fetched again.
    assert calls["n"] == 1

    refreshed = _call_tool(
        capsys,
        mcp,
        "envoy_read",
        {"source": {"url": url}, "goal": "g", "refresh": True},
        modern=True,
    )
    assert refreshed["isError"] is False, "refresh must fall back to the retained material when refetching is blocked"
    assert calls["n"] == 2
    refetch_notice = refreshed["structuredContent"]["notices"][-1]
    assert refetch_notice["refetch_error"]["code"] == "auth_required"


def test_envoy_read_keeps_different_sources_apart_when_serving_retained_material(capsys, monkeypatch):
    from envoy_cli import mcp
    from envoy_cli.reader import PublicPageError
    from envoy_core.schemas.read_v1 import ErrorCode

    mcp._RETAINED_READS.clear()
    payload = {
        "protocol_version": "envoy.read/1",
        "request_id": "r1",
        "material_id": "mat-a",
        "source_revision": "rev-a",
        "execution_state": "completed",
        "result": "ready",
        "blocks": [],
        "resources": [],
    }
    monkeypatch.setattr(mcp, "read_public_page", lambda _request: _PublicReadStub(payload))
    _call_tool(capsys, mcp, "envoy_read", {"source": {"url": "https://example.org/a"}, "goal": "g"})

    def blocked(_request):
        raise PublicPageError("gone", ErrorCode.SOURCE_UNAVAILABLE)

    monkeypatch.setattr(mcp, "read_public_page", blocked)
    other = _call_tool(capsys, mcp, "envoy_read", {"source": {"url": "https://example.org/b"}, "goal": "g"})

    assert other["isError"] is True
    assert other["structuredContent"]["result"] == "blocked"
    assert "served_from_retained_material" not in other["structuredContent"]


def test_envoy_read_text_always_carries_the_material_handle_and_replay_path():
    from envoy_cli import mcp

    text = mcp._tool_content_text(
        {
            "result": "partial",
            "material_id": "mat-x",
            "source_revision": "rev-x",
            "blocks": [{"content": {"text": "abc"}}],
            "resources": [{}, {}],
        },
        modern=True,
    )
    assert "material_id=mat-x" in text
    assert "source_revision=rev-x" in text
    assert "do not re-read" in text

    blocked = mcp._tool_content_text(
        {
            "result": "blocked",
            "material_id": "mat-x",
            "errors": [{"code": "auth_required", "message": "sign-in required"}],
        },
        modern=True,
    )
    assert "auth_required" in blocked
    assert "sign-in required" in blocked
    assert "envoy_replay" in blocked
