"""Replay must survive a process boundary.

A model only ever holds its own host session id; the id Envoy minted for the
retained material is server-side. Requiring the caller to echo that id made
replay unusable across a host restart, which is exactly when it is needed.
"""

import time

import pytest

from envoy_cli import mcp, reader
from envoy_core.schemas.read_v1 import MaterialReplayRequest

MATERIAL_ID = "mat-" + "a" * 24
REVISION = "b" * 64


def _material(tmp_path, monkeypatch, session_id: str = "mcp-original-process") -> None:
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    reader._store_material(MATERIAL_ID, {
        "source_url": "https://example.org/guide",
        "source_revision": REVISION,
        "session_id": session_id,
        "title_text": "Guide",
        "text": "Retained body text about caching and revalidation.",
        "expires_at": time.time() + 3600,
        "budget_state": {
            "max_total_response_chars": 4000,
            "max_total_response_tokens": 1000,
            "max_expansion_rounds": 8,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    })


def _request(**overrides):
    payload = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-replay-adopt",
        "material_id": MATERIAL_ID,
        "source_revision": REVISION,
        "session_id": "mcp-fresh-process",
    }
    payload.update(overrides)
    return MaterialReplayRequest.model_validate(payload)


def test_replay_derives_the_source_session_when_the_model_omits_it(tmp_path, monkeypatch) -> None:
    _material(tmp_path, monkeypatch)

    response = reader.replay_material(_request())

    assert response.result.value in {"ready", "partial"}
    assert any("caching" in block.content.text for block in response.blocks)
    assert response.material_id != MATERIAL_ID


def test_replay_still_rejects_an_explicit_wrong_source_session(tmp_path, monkeypatch) -> None:
    _material(tmp_path, monkeypatch)

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader.replay_material(_request(source_session_id="the-model-guessed-this"))

    assert "source session does not match" in str(excinfo.value).lower()


def test_mcp_replay_fills_a_fresh_session_and_omits_the_unknown_source() -> None:
    prepared = mcp._server_arguments("envoy_replay", {
        "material_id": MATERIAL_ID,
        "source_revision": REVISION,
    })

    assert prepared["session_id"].startswith("mcp-")
    assert "source_session_id" not in prepared
