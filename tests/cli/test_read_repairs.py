from __future__ import annotations

import json
from pathlib import Path
import sys
import types

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import cache, mcp, media, reader
from envoy_core.schemas.read_v1 import (
    ErrorCode,
    ExecutionState,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadRequest,
    ReadResponse,
    ReadResult,
    TextContent,
    VideoLocator,
)


def _request(url: str = "https://example.org/video") -> ReadRequest:
    return ReadRequest.model_validate({
        "request_id": "repair-test",
        "source": {"url": url},
    })


def _blocked_challenge() -> ReadResponse:
    return reader._blocked(
        _request(),
        ErrorCode.CHALLENGE_REQUIRED,
        "The page still requires login or security verification.",
        action_required=True,
    )


def _chapter_block(index: int, start: float, end: float) -> MaterialBlock:
    return MaterialBlock(
        id=f"blk-video-chapter-{index:03d}",
        kind=MaterialKind.VIDEO_SEGMENT,
        content=TextContent(type="text", text=f"chapter {index}"),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=VideoLocator(
            type="video_time",
            source_url="https://example.org/video",
            start_seconds=start,
            end_seconds=end,
        ),
        order=index,
    )


def test_browser_media_prefix_is_derived_from_url_and_revision():
    first = reader._browser_media_prefix("https://example.org/video?id=1", "mat-a", "rev-1")
    same = reader._browser_media_prefix("https://example.org/video?id=1", "mat-a", "rev-1")
    other_revision = reader._browser_media_prefix(
        "https://example.org/video?id=1", "mat-a", "rev-2"
    )

    assert first == same
    assert first != other_revision
    assert first.startswith("source-")
    assert first.endswith("-")


def test_prune_browser_media_keeps_only_the_current_prefix(tmp_path):
    keep = tmp_path / "source-current-video.mp4"
    stale = tmp_path / "source-older-video.mp4"
    unrelated = tmp_path / "frames-abc"
    for path in (keep, stale, unrelated):
        path.write_bytes(b"x")

    reader._prune_browser_media(tmp_path, keep_prefix="source-current-")

    assert keep.is_file()
    assert not stale.exists()
    assert unrelated.is_file()


def test_cache_status_and_prune_cover_raw_video_media(tmp_path):
    root = tmp_path / "data"
    materials = root / "materials"
    populated = materials / "video-mat-000000000000000000000001"
    empty = materials / "video-mat-000000000000000000000002"
    populated.mkdir(parents=True)
    empty.mkdir()
    (populated / "source-abc-video.mp4").write_bytes(b"0" * 32)
    home = tmp_path / "home"
    home.mkdir()

    report = cache.cache_status(root=root, home=home)

    assert report["categories"]["video_media"]["orphan_count"] == 2
    assert report["categories"]["video_media"]["orphan_bytes"] == 32

    pruned = cache.prune_cache(root=root)

    assert pruned["freed_bytes"] == 32
    assert not populated.exists()
    assert not empty.exists()


def test_tail_frame_seek_is_clamped_inside_the_video(monkeypatch, tmp_path):
    seeks: list[str] = []
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    output_dir = tmp_path / "frames"
    output_dir.mkdir()

    monkeypatch.setattr(
        media.shutil, "which", lambda name: f"/usr/bin/{name}"
    )

    def fake_run(command, **kwargs):
        if "-show_entries" in command:
            return types.SimpleNamespace(
                stdout=json.dumps({"streams": [{"width": 640, "height": 480}]}),
                stderr="",
                returncode=0,
            )
        seeks.append(command[command.index("-ss") + 1])
        Path(command[-1]).write_bytes(b"jpeg")
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    frames = media.extract_video_frames(
        source,
        output_dir,
        [0.0, 690.608],
        max_visual_pixels_per_input=1_000_000,
        max_work_seconds=30,
        duration_seconds=690.608,
    )

    assert seeks[0] == "0.000"
    assert seeks[1] == "690.558"
    assert frames[1]["timestamp"] == pytest.approx(690.558, abs=1e-3)


def test_selected_asr_model_keeps_an_explicit_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "asr_model_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(media, "_read_asr_config", lambda: {})
    monkeypatch.delenv("ENVOY_ASR_MODEL", raising=False)

    (tmp_path / "large-v3-turbo.pt").write_bytes(b"x" * 40)

    assert media.selected_asr_model() == "base"
    assert media.cached_asr_models()[:1] == ["large-v3-turbo"]

    monkeypatch.setattr(media, "_read_asr_config", lambda: {"model": "small"})
    assert media.selected_asr_model() == "small"


def test_tool_declarations_expose_advanced_blocks_and_session():
    read_tool = next(tool for tool in mcp.TOOLS if tool["name"] == "envoy_read")
    description = read_tool["description"]

    assert "budget" in description and "retention" in description
    assert "refresh" in description
    # The declaration is in every request prefix, so plumbing and budget stay in
    # the description instead of the JSON schema. The frame capability is
    # declared: hiding it in prose made a text-only read look like a hard limit.
    assert len(__import__("json").dumps(mcp.READ_SCHEMA)) < 2400
    assert "host_capabilities" in mcp.READ_SCHEMA["properties"]
    assert "retention" in mcp.READ_SCHEMA["properties"]
    assert "session_id" in mcp.OBSERVE_VISUAL_SCHEMA["properties"]


def test_challenge_blocked_result_is_retryable():
    blocked = _blocked_challenge()

    assert blocked.result is ReadResult.BLOCKED
    assert blocked.errors[0].retryable is True


def test_public_read_retries_a_challenge_once(monkeypatch):
    responses = [_blocked_challenge(), _blocked_challenge()]
    calls: list[int] = []

    def fake_once(request):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr(reader, "_read_public_page_once", fake_once)
    monkeypatch.setattr(reader.time, "sleep", lambda seconds: None)

    response = reader.read_public_page(_request())

    assert len(calls) == 2
    assert response.errors[0].details["internal_retry_attempts"] == 1
    assert response.errors[0].retryable is True


def test_public_read_does_not_retry_a_delivered_response(monkeypatch):
    calls: list[int] = []

    def fake_once(request):
        calls.append(1)
        return ReadResponse(
            request_id="repair-test",
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL,
        )

    monkeypatch.setattr(reader, "_read_public_page_once", fake_once)

    response = reader.read_public_page(_request())

    assert len(calls) == 1
    assert response.result is ReadResult.PARTIAL


def test_chapter_span_marks_a_short_list_incomplete():
    complete = [_chapter_block(1, 0.0, 300.0), _chapter_block(2, 300.0, 690.0)]
    short = [_chapter_block(1, 0.0, 60.0)]

    assert reader._chapter_span_state(complete, 690.0) == ((0.0, 690.0), False)
    span, incomplete = reader._chapter_span_state(short, 690.0)

    assert span == (0.0, 60.0)
    assert incomplete is True
    assert reader._chapter_span_state([], 690.0) == (None, False)


def test_login_user_action_is_a_human_action_without_a_runnable_command():
    action = reader._login_user_action("https://www.douyin.com/video/123")

    assert action["requires_human"] is True
    for option in action["options"]:
        assert option["requires_human"] is True
        assert "command" not in option
    serialized = json.dumps(action, ensure_ascii=False)
    assert "ENVOY_VISIBLE_BROWSER=" not in serialized
    assert "--request-json" not in serialized
