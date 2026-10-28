"""A collection link is answered with a concrete next step, not a page render."""

import pytest

from envoy_cli import reader
from envoy_cli.platforms import is_collection_url
from envoy_core.schemas.read_v1 import ReadRequest


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate({"request_id": "guard", "source": {"url": url}})


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PL1234567890",
        "https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445",
        "https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718",
        "https://www.bilibili.com/list/watchlater?bvid=BV1xx411c7mD",
        "https://www.bilibili.com/medialist/play/ml123456",
    ],
)
def test_a_collection_link_is_recognized(url):
    assert is_collection_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
        "https://space.bilibili.com/2142762/video",
        "https://example.com/playlist?list=1",
    ],
)
def test_a_single_item_link_is_not_a_collection(url):
    assert not is_collection_url(url)


def test_a_collection_link_is_blocked_with_the_next_step(monkeypatch):
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)

    response = reader.read_public_page(
        _request("https://www.youtube.com/playlist?list=PL1234567890")
    )

    assert response.result == reader.ReadResult.BLOCKED
    assert response.errors and response.errors[0].code == reader.ErrorCode.UNSUPPORTED_SOURCE
    message = response.errors[0].message
    assert "envoy_discover" in message
    assert response.errors[0].details.get("reader") == "collection_guard"


def test_the_guard_runs_before_any_network_work(monkeypatch):
    calls: list[str] = []

    def no_dns(value, *_args, **_kwargs):
        calls.append(str(value))
        return True

    monkeypatch.setattr(reader, "_is_public_http_url", no_dns)
    monkeypatch.setattr(
        reader,
        "public_api_fallback",
        lambda *_a, **_k: calls.append("fallback") or {},
    )

    response = reader.read_public_page(
        _request("https://space.bilibili.com/2142762/channel/collectiondetail?sid=57445")
    )

    assert response.result == reader.ReadResult.BLOCKED
    assert "fallback" not in calls
