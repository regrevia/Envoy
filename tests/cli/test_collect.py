"""`envoy collect` must turn a profile into one resumable Markdown pack."""

import json

import pytest

from envoy_cli import collect as collect_module
from envoy_cli.cli import main
from envoy_cli.collections import CollectionError

PROFILE = "https://www.douyin.com/user/creator"
SPACE = "https://space.bilibili.com/123456"


@pytest.fixture(autouse=True)
def _credential_handoff_on(monkeypatch):
    # These tests pin the enabled mechanics; the shipped default (off) has its
    # own coverage in tests/cli/test_credential_handoff.py.
    monkeypatch.setenv("ENVOY_CREDENTIAL_HANDOFF", "1")


class _Block:
    def __init__(self, text):
        self._text = text

    def model_dump(self, mode="json"):
        return {"kind": "paragraph", "text": self._text}


class _Response:
    def __init__(self, result="ready", material_id="mat-1", text="原文内容", errors=None):
        self.result = result
        self.material_id = material_id
        self.source_revision = "rev-1"
        self.expires_at = None
        self.blocks = [_Block(text)]
        self.errors = errors or []


def _candidates(platform="douyin"):
    if platform == "bilibili":
        urls = [
            "https://www.bilibili.com/video/BV1aa411c7de",
            "https://www.bilibili.com/video/BV1bb411c7df",
        ]
    else:
        urls = [
            "https://www.douyin.com/video/7000000000000000001",
            "https://www.douyin.com/video/7000000000000000002",
        ]
    return [
        {
            "candidate_id": f"{platform}-{index}",
            "source_url": url,
            "platform": platform,
            "item_type": "video",
            "title": f"第 {index} 条",
            "creator": "creator",
            "duration_seconds": 90,
            "published_at": "2026-08-01",
            "position": index,
        }
        for index, url in enumerate(urls)
    ]


def _fake_discovery(platform):
    def discover(profile_url, *, limit=20, cursor=None, max_work_seconds=60):
        candidates = _candidates(platform)
        return {
            "profile_url": profile_url,
            "platform": platform,
            "discovery_revision": "d" * 20,
            "cursor_mode": "yt_dlp_flat_metadata",
            "ordering": "source",
            "candidates": candidates[:limit],
            "has_more": False,
            "next_cursor": None,
            "stop_reason": "source_end_unknown",
            "discovered_at": "2026-10-24T00:00:00+00:00",
        }

    return discover


def test_collect_writes_one_markdown_pack_anchored_to_each_source(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )
    monkeypatch.setattr(collect_module, "read_public_page", lambda request: _Response())
    out = tmp_path / "notes.md"

    summary = collect_module.collect(PROFILE, out, limit=5)

    assert summary["completed"] == 2
    assert summary["read_now"] == 2
    text = out.read_text(encoding="utf-8")
    for candidate in _candidates():
        assert candidate["source_url"] in text
    assert "material_id" in text or "mat-1" in text
    assert "原文内容" in text
    sources = json.loads((tmp_path / "notes.md.sources.json").read_text(encoding="utf-8"))
    assert sources["douyin-0"]["material_id"] == "mat-1"


def test_a_second_run_resumes_instead_of_reading_again(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )
    calls = []

    def read(request):
        calls.append(request.source.url)
        return _Response()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = tmp_path / "notes.md"

    first = collect_module.collect(PROFILE, out, limit=5)
    second = collect_module.collect(PROFILE, out, limit=5)

    assert first["read_now"] == 2
    assert second["read_now"] == 0
    assert second["skipped"] == 2
    assert second["resumed"] is True
    assert len(calls) == 2


def test_a_blocked_item_is_recorded_and_does_not_sink_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )
    responses = iter([
        _Response(result="blocked", material_id=None, text=""),
        _Response(text="第二条内容"),
    ])
    monkeypatch.setattr(
        collect_module, "read_public_page", lambda request: next(responses)
    )
    out = tmp_path / "notes.md"

    summary = collect_module.collect(PROFILE, out, limit=5)

    assert summary["completed"] == 1
    assert [item["state"] for item in summary["pending"]] == ["blocked"]
    text = out.read_text(encoding="utf-8")
    assert "## 未完成" in text
    assert "第二条内容" in text


def test_a_bilibili_space_is_supported(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("bilibili")
    )
    monkeypatch.setattr(collect_module, "read_public_page", lambda request: _Response())
    out = tmp_path / "bili.md"

    summary = collect_module.collect(SPACE, out, limit=5)

    assert summary["platform"] == "bilibili"
    assert "https://www.bilibili.com/video/BV1aa411c7de" in out.read_text(encoding="utf-8")


def test_an_unsupported_host_is_refused(tmp_path):
    with pytest.raises(CollectionError):
        collect_module.collect("https://example.com/user/creator", tmp_path / "x.md")


def test_the_cli_reports_the_written_pack(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )
    monkeypatch.setattr(collect_module, "read_public_page", lambda request: _Response())
    out = tmp_path / "cli.md"

    code = main(["collect", "--url", PROFILE, "--out", str(out), "--limit", "5"])

    assert code == 0
    printed = capsys.readouterr().out
    assert "Wrote" in printed
    assert out.exists()


class _RealTextBlock:
    """The payload shape a real MaterialBlock serialises to."""

    def model_dump(self, mode="json"):
        return {"kind": "text", "content": {"type": "text", "text": "真实正文内容"}}


class _RealResponse:
    def __init__(self, text="真实正文内容"):
        self.result = "ready"
        self.material_id = "mat-real"
        self.source_revision = "rev-1"
        self.expires_at = None
        self.blocks = [_RealTextBlock()]
        self.errors = []


def test_a_real_block_shape_is_excerpted_and_skipped_on_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )
    calls = []

    def read(request):
        calls.append(request.source.url)
        return _RealResponse()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = tmp_path / "notes.md"

    first = collect_module.collect(PROFILE, out, limit=5)
    second = collect_module.collect(PROFILE, out, limit=5)

    assert first["read_now"] == 2
    assert len(calls) == 2, "a completed item must not be read again"
    assert second["read_now"] == 0
    assert second["skipped"] == 2
    sources = json.loads((tmp_path / "notes.md.sources.json").read_text(encoding="utf-8"))
    assert any("真实正文内容" in str(item.get("excerpt") or "") for item in sources.values())


def test_lost_artifacts_are_rebuilt_from_the_material_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )

    from envoy_cli import reader

    root = reader._material_root()
    root.mkdir(parents=True, exist_ok=True)
    material_id = "mat-" + "a" * 24
    (root / f"{material_id}.json").write_text(
        json.dumps(
            {
                "operation_response": {
                    "blocks": [{"kind": "text", "content": {"type": "text", "text": "离线重建正文"}}]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / f"{material_id}.expires").write_text("9999999999", encoding="ascii")

    calls = []

    class _Resp(_RealResponse):
        def __init__(self):
            super().__init__()
            self.material_id = material_id

    def read(request):
        calls.append(request.source.url)
        return _Resp()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = tmp_path / "notes.md"
    first = collect_module.collect(PROFILE, out, limit=5)
    assert first["read_now"] == 2

    out.unlink()
    collect_module._sources_path(out).unlink()

    second = collect_module.collect(PROFILE, out, limit=5)

    assert len(calls) == 2, "losing the pack must not send us back to the sites"
    assert second["read_now"] == 0
    assert out.is_file() and out.stat().st_size > 0
    assert "离线重建正文" in out.read_text(encoding="utf-8")
    assert second["excerpts_filled"] >= 1


def test_one_site_pause_stops_the_rest_of_that_host(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVOY_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        collect_module.checkpoint, "discover_profile", _fake_discovery("douyin")
    )

    from envoy_core.schemas import ErrorCode, ReadRequest

    from envoy_cli import reader

    calls = []

    def read(request):
        calls.append(request.source.url)
        if len(calls) == 1:
            return reader._blocked(
                ReadRequest(request_id="r1", source={"url": request.source.url}),
                ErrorCode.AUTH_REQUIRED,
                "This page needs a signed-in session",
                action_required=True,
            )
        return _Response()

    monkeypatch.setattr(collect_module, "read_public_page", read)
    out = tmp_path / "notes.md"
    first = collect_module.collect(PROFILE, out, limit=5)

    assert first["read_now"] == 1, "the second post must not re-hit the same wall"
    assert first["auth_pause"]["action_id"].startswith("auth-")
    assert len(first["auth_pause"]["candidates"]) == 2
    blocked = [item for item in first["pending"] if item["state"] == "blocked"]
    assert len(blocked) == 2
    assert any("auth_pause" in str(item["reason"]) for item in blocked)

    second = collect_module.collect(PROFILE, out, limit=5)

    assert second["read_now"] == 0, "a paused batch must not retry by itself"

    third = collect_module.collect(PROFILE, out, limit=5, retry_reason="auth")

    assert len(calls) == 3
    assert third["read_now"] == 2
    assert third["completed"] == 2


YOUTUBE_PLAYLIST = "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"


def test_a_youtube_playlist_is_a_discoverable_collection():
    from envoy_cli.collections import canonical_profile_url

    url, platform = canonical_profile_url(YOUTUBE_PLAYLIST + "&si=abc")

    assert platform == "youtube"
    assert url == YOUTUBE_PLAYLIST


def test_a_youtube_playlist_without_a_list_id_is_refused():
    import pytest as _pytest

    from envoy_cli.collections import CollectionError, canonical_profile_url

    with _pytest.raises(CollectionError) as excinfo:
        canonical_profile_url("https://www.youtube.com/playlist")

    assert "list id" in str(excinfo.value)


def test_a_youtube_watch_link_is_not_a_collection():
    import pytest as _pytest

    from envoy_cli.collections import CollectionError, canonical_profile_url

    with _pytest.raises(CollectionError):
        canonical_profile_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_a_youtube_playlist_is_enumerated_through_flat_metadata(monkeypatch, tmp_path):
    from envoy_cli import collections

    monkeypatch.setenv("ENVOY_COLLECTION_DIR", str(tmp_path / "collections"))

    captured: dict = {}

    class _YDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, url, download=False):
            captured["url"] = url
            return {
                "_type": "playlist",
                "entries": [
                    {"id": "aaaaaaaaaaa", "title": "First", "webpage_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
                    {"id": "bbbbbbbbbbb", "title": "Second", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
                ],
            }

    monkeypatch.setattr(
        collections,
        "discover_profile",
        collections.discover_profile,
    )
    monkeypatch.setattr(
        "envoy_cli.video._load_yt_dlp",
        lambda: (_YDL, None),
    )
    monkeypatch.setattr("envoy_cli.media.guard_yt_dlp_client", lambda _ydl: None)
    monkeypatch.setattr(collections, "proxy_plan", lambda: [None])

    result = collections.discover_profile(YOUTUBE_PLAYLIST, limit=5)

    assert result["platform"] == "youtube"
    assert result["cursor_mode"] == "yt_dlp_flat_metadata"
    assert captured["url"] == YOUTUBE_PLAYLIST
    assert captured["skip_download"] is True
    assert [item["source_url"] for item in result["candidates"]] == [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    ]
    assert {item["platform"] for item in result["candidates"]} == {"youtube"}
