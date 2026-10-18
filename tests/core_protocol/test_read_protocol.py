import json
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
assert envoy_core.__version__ == "0.1.0"
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
