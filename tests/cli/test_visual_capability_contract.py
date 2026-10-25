"""Frames are gated on a caller-declared capability; the gap must be loud.

Reported: an agent asked Envoy for the visuals of a Douyin video, got text only,
read the reply as "Envoy cannot see video", and opened the page in its own
browser panel instead (landing on a captcha interstitial). Envoy had fine frame
extraction; it was waiting for ``host_capabilities.video_input="frames"`` and
``image_input="local_file"``, a switch the MCP schema never declared and the
partial text never named. These tests pin the declaration, the recorded
requirement, and the retry instruction the model sees.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import mcp, reader
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ExecutionState,
    ReadRequest,
    ReadResponse,
    ReadResult,
)

VISUAL_REQUIREMENT = {
    "host_capabilities": {"video_input": "frames", "image_input": "local_file"},
    "retention": {"raw_media": "persistent"},
}


def _request(include_visual: bool = True, **capabilities) -> ReadRequest:
    payload: dict = {
        "protocol_version": "envoy.read/1",
        "request_id": "visual-capability-test",
        "source": {"url": "https://www.douyin.com/video/7697696177046736143"},
    }
    if include_visual:
        payload["scope"] = {"include_visual": True}
    if capabilities:
        payload["host_capabilities"] = capabilities
    return ReadRequest.model_validate(payload)


def _video_response() -> ReadResponse:
    return ReadResponse(
        request_id="visual-capability-test",
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.READY,
        coverage=Coverage(
            modalities={
                "video": CoverageItem(
                    discovered=1,
                    extracted=1,
                    extraction="complete",
                    details={"duration_seconds": 60.0},
                )
            }
        ),
    )


def _read_tool() -> dict:
    return next(tool for tool in mcp.TOOLS if tool["name"] == "envoy_read")


def test_read_schemas_declare_the_frame_capability():
    for schema in (mcp.READ_SCHEMA, mcp.LOCAL_VIDEO_SCHEMA):
        properties = schema["properties"]
        video_input = properties["host_capabilities"]["properties"]["video_input"]
        image_input = properties["host_capabilities"]["properties"]["image_input"]
        raw_media = properties["retention"]["properties"]["raw_media"]
        assert "frames" in video_input["enum"]
        assert "local_file" in image_input["enum"]
        assert "persistent" in raw_media["enum"]

    description = _read_tool()["description"]
    assert "host_capabilities" in description
    assert "image_input" in description or "local_file" in description


def test_read_description_refuses_the_browser_substitution():
    description = _read_tool()["description"]
    assert "separate browser view" in description
    assert "raw_media" in description


def test_missing_capability_records_the_retry_declaration():
    response = reader._attach_video_frames(
        _request(), "https://www.douyin.com/video/1", _video_response()
    )

    assert response.result is ReadResult.PARTIAL
    video = response.coverage.modalities["video"]
    assert video.details["visual_requirement"] == VISUAL_REQUIREMENT
    assert any("host_capabilities" in note for note in video.unresolved)


def test_declared_capability_is_not_reported_as_a_gap():
    response = _video_response()
    # No visual request at all: the helper is a no-op.
    assert reader._attach_video_frames(
        _request(include_visual=False), "https://example.org/v.mp4", response
    ) is response


def _payload(requirement: dict | None) -> dict:
    details: dict = {"media_fetched": False}
    if requirement:
        details["visual_requirement"] = requirement
    return {
        "result": "partial",
        "material_id": "mat-visual",
        "blocks": [],
        "resources": [],
        "coverage": {
            "modalities": {
                "video": {
                    "extraction": "partial",
                    "unresolved": [
                        "Visual frames were requested but the host did not declare "
                        "video_input=frames and image_input=local_file; send "
                        "host_capabilities {video_input: frames, image_input: "
                        "local_file} to receive frames"
                    ],
                    "details": details,
                }
            }
        },
    }


def test_visible_text_gives_the_exact_retry_request():
    request = SimpleNamespace(scope=SimpleNamespace(include_visual=True))
    text = mcp._tool_content_text(_payload(VISUAL_REQUIREMENT), True, request)

    assert '"video_input": "frames"' in text
    assert '"image_input": "local_file"' in text
    assert '"raw_media": "persistent"' in text
    assert "separate browser view" in text
    assert "Envoy read partial" in text


def test_visible_text_without_declaration_warns_instead_of_inventing_visuals():
    request = SimpleNamespace(scope=SimpleNamespace(include_visual=True))
    text = mcp._tool_content_text(_payload(None), True, request)

    assert "No frames or images were delivered" in text
    assert "visual_requirement" not in text
