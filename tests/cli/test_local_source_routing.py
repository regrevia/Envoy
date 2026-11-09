"""A local file passed as ``source.url`` must be read as a local video.

``envoy_read`` documents ``source.url`` as accepting a local path or a
``file://`` URL, and the skill tells agents to use it for a video the user
dropped into the chat. Before this contract existed, such a path was resolved
as a host name, rejected by the public-URL guard, and reported as a proxy/DNS
problem. These tests pin the routing decision itself, so the two entry points
cannot drift apart again.
"""

from __future__ import annotations

from pathlib import Path

from envoy_cli import local_video, reader
from envoy_core.schemas.read_v1 import ReadRequest


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate(
        {
            "request_id": "req-local-routing",
            "source": {"url": url, "kind": "auto"},
            "goal": "read the video",
        }
    )


def test_a_bare_absolute_path_is_a_local_source(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")

    assert reader._local_file_source_path(str(video)) == video


def test_a_file_url_is_a_local_source(tmp_path: Path):
    video = tmp_path / "clip with space.mp4"
    video.write_bytes(b"not really a video")

    assert reader._local_file_source_path(video.as_uri()) == video


def test_a_public_url_is_not_a_local_source():
    assert reader._local_file_source_path("https://example.com/clip.mp4") is None


def test_a_missing_path_is_not_a_local_source(tmp_path: Path):
    assert reader._local_file_source_path(str(tmp_path / "absent.mp4")) is None


def test_a_relative_path_is_not_a_local_source():
    assert reader._local_file_source_path("clip.mp4") is None


def test_a_local_source_never_reaches_the_public_url_guard(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    seen: list[Path] = []

    def _fake_local_read(request, path):
        seen.append(path)
        return "local-video-response"

    def _guard_must_not_run(url, **_kwargs):
        raise AssertionError("a local path must not be validated as a public URL")

    monkeypatch.setattr(reader, "_reclaim_expired_media", lambda: None)
    monkeypatch.setattr(reader, "_is_public_http_url", _guard_must_not_run)
    monkeypatch.setattr(
        local_video, "read_local_video_from_read_request", _fake_local_read
    )

    assert reader.read_public_page(_request(str(video))) == "local-video-response"
    assert seen == [video]


def test_a_public_url_still_goes_through_the_public_reader(monkeypatch):
    monkeypatch.setattr(reader, "_reclaim_expired_media", lambda: None)
    monkeypatch.setattr(
        reader, "_read_public_page_with_retries", lambda request: "public-response"
    )

    assert (
        reader.read_public_page(_request("https://example.com/article"))
        == "public-response"
    )


def test_the_read_request_translates_into_a_local_video_request(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    captured: list = []

    monkeypatch.setattr(
        local_video, "read_local_video", lambda request: captured.append(request)
    )

    request = ReadRequest.model_validate(
        {
            "request_id": "req-local-ticket",
            "source": {"url": str(video), "kind": "auto"},
            "goal": "summarise this",
            "scope": {"start_seconds": 5, "end_seconds": 30},
        }
    )
    local_video.read_local_video_from_read_request(request, video)

    assert len(captured) == 1
    translated = captured[0]
    assert translated.path == str(video)
    assert translated.goal == "summarise this"
    assert translated.scope.start_seconds == 5
    assert translated.scope.end_seconds == 30
    assert translated.request_id == "req-local-ticket"
    # ``source`` belongs to the read envelope, not the local-video contract.
    assert "source" not in type(translated).model_fields
