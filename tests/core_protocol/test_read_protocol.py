import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


CORE_SRC = Path(__file__).resolve().parents[2] / "packages" / "envoy-core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))


def test_read_protocol_models_validate_and_roundtrip_article_blocks():
    from envoy_core.schemas.read_v1 import (
        MaterialBlock,
        ReadRequest,
        ReadResponse,
        TextContent,
        WebLocator,
    )

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-1",
        "source": {"url": "https://example.org/article"},
        "goal": "Find the article's deployment steps",
    })
    block = MaterialBlock(
        id="blk-1",
        kind="paragraph",
        content=TextContent(type="text", text="Use a separate worker."),
        origin="source_text",
        locator=WebLocator(type="web", source_url="https://example.org/article", block_id="blk-1"),
    )
    response = ReadResponse(
        protocol_version=request.protocol_version,
        request_id=request.request_id,
        execution_state="completed",
        result="ready",
        blocks=[block],
    )

    restored = ReadResponse.model_validate_json(response.model_dump_json())

    assert restored.blocks[0].content.text == "Use a separate worker."
    assert restored.blocks[0].locator.source_url == request.source.url
    assert restored.result == "ready"


def test_read_protocol_rejects_unversioned_requests_and_invalid_image_coordinates():
    from envoy_core.schemas.read_v1 import ImageRegionLocator, ReadRequest

    with pytest.raises(ValidationError):
        ReadRequest.model_validate({"source": {"url": "https://example.org/"}})

    with pytest.raises(ValidationError):
        ImageRegionLocator(
            type="image_region",
            resource_id="img-1",
            coordinate_system="normalized_top_left",
            bbox=[0.8, 0.1, 0.2, 0.9],
        )


def test_video_visual_scope_requires_explicit_host_frame_and_time_range():
    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "video-frames",
        "source": {"url": "https://video.example/watch/1", "kind": "video"},
        "scope": {
            "include_visual": True,
            "start_seconds": 120,
            "end_seconds": 150,
        },
        "host_capabilities": {
            "image_input": "local_file",
            "video_input": "frames",
        },
    })

    assert request.scope.include_visual is True
    assert request.scope.start_seconds == 120
    assert request.scope.end_seconds == 150
    assert request.host_capabilities.video_input == "frames"


def test_read_protocol_accepts_request_scoped_ocr_language():
    from envoy_core.schemas.read_v1 import ReadRequest

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "ocr-language",
        "source": {"url": "https://example.org/note.png", "kind": "image"},
        "ocr_profile": "balanced",
        "ocr_language": "chi_sim",
    })

    assert request.ocr_language == "chi_sim"
    assert ReadRequest.model_validate_json(request.model_dump_json()).ocr_language == "chi_sim"


def test_read_protocol_rejects_unsafe_or_unbounded_ocr_language():
    from envoy_core.schemas.read_v1 import ReadRequest

    base = {
        "protocol_version": "envoy.read/1",
        "request_id": "ocr-language-invalid",
        "source": {"url": "https://example.org/note.png", "kind": "image"},
    }
    for language in ("x", "../eng", "eng;--psm 0", "a" * 33):
        with pytest.raises(ValidationError):
            ReadRequest.model_validate({**base, "ocr_language": language})


def test_video_visual_scope_rejects_incomplete_or_reversed_ranges():
    from envoy_core.schemas.read_v1 import ReadRequest

    base = {
        "protocol_version": "envoy.read/1",
        "request_id": "video-frames-invalid",
        "source": {"url": "https://video.example/watch/1", "kind": "video"},
    }
    with pytest.raises(ValidationError):
        ReadRequest.model_validate({**base, "scope": {"start_seconds": 10}})
    with pytest.raises(ValidationError):
        ReadRequest.model_validate({
            **base,
            "scope": {"start_seconds": 20, "end_seconds": 10},
        })


def test_visual_observation_request_is_bounded_and_roundtrips():
    from envoy_core.schemas.read_v1 import VisualObservationRequest

    request = VisualObservationRequest.model_validate({
        "protocol_version": "envoy.observe/1",
        "request_id": "observe-1",
        "material_id": "mat-" + "a" * 24,
        "source_url": "https://example.org/chart.png",
        "resource_id": "resource-image",
        "source_block_id": "blk-image-0001",
        "observation": "The chart shows three deployment stages.",
    })

    assert request.observation == "The chart shows three deployment stages."
    assert VisualObservationRequest.model_validate_json(request.model_dump_json()).protocol_version == "envoy.observe/1"


def test_visual_observation_request_can_bind_source_from_retained_material():
    from envoy_core.schemas.read_v1 import VisualObservationRequest

    request = VisualObservationRequest.model_validate({
        "protocol_version": "envoy.observe/1",
        "request_id": "observe-material-bound",
        "material_id": "mat-" + "a" * 24,
        "resource_id": "resource-frame",
        "source_block_id": "blk-frame",
        "observation": "The frame shows a three-column diagram.",
    })

    assert request.source_url is None


def test_material_replay_request_requires_distinct_session_and_bounded_budget():
    from pydantic import ValidationError
    from envoy_core.schemas.read_v1 import MaterialReplayRequest

    request = MaterialReplayRequest.model_validate({
        "request_id": "replay-01",
        "material_id": "mat-" + "a" * 24,
        "source_revision": "b" * 64,
        "source_session_id": "session-a",
        "session_id": "session-b",
        "max_chars": 2000,
        "max_total_response_chars": 4000,
        "include_visual": True,
        "max_visual_inputs": 2,
    })
    assert request.protocol_version == "envoy.read/1"
    assert request.session_id != request.source_session_id

    with pytest.raises(ValidationError):
        MaterialReplayRequest.model_validate({
            **request.model_dump(),
            "max_chars": 4001,
            "max_total_response_chars": 4000,
        })
def test_total_token_budget_may_be_lower_than_per_response_ceiling():
    from envoy_core.schemas.read_v1 import Budget

    budget = Budget.model_validate({
        "max_response_tokens": 512,
        "max_total_response_tokens": 128,
    })

    assert budget.max_response_tokens == 512
    assert budget.max_total_response_tokens == 128


def test_visual_observation_history_request_has_bounded_query_limits():
    from envoy_core.schemas.read_v1 import VisualObservationHistoryRequest

    request = VisualObservationHistoryRequest.model_validate({
        "protocol_version": "envoy.observe/1",
        "request_id": "history-1",
        "material_id": "mat-" + "f" * 24,
        "resource_id": "resource-image",
        "max_items": 4,
        "max_chars": 1200,
    })

    assert request.max_items == 4
    assert request.max_chars == 1200


def test_material_search_request_has_bounded_query_limits():
    from envoy_core.schemas.read_v1 import MaterialSearchRequest

    request = MaterialSearchRequest.model_validate({
        "protocol_version": "envoy.search/1",
        "request_id": "search-1",
        "material_id": "mat-" + "a" * 24,
        "query": "部署命令",
        "max_results": 3,
        "max_chars": 1200,
        "max_response_tokens": 256,
    })

    assert request.query == "部署命令"
    assert request.max_results == 3


def test_schema_json_is_published_and_does_not_import_web_application():
    script = """
import json
import sys
import envoy_core
from envoy_core.schemas.read_v1 import read_request_json_schema

schema = read_request_json_schema()
assert schema["title"] == "ReadRequest"
assert "envoy.read/1" in json.dumps(schema)
assert not any(name.startswith("fastapi") for name in sys.modules)
assert not any(name.startswith("sqlalchemy") for name in sys.modules)
assert envoy_core.__version__ == "0.1.28"
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CORE_SRC)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
