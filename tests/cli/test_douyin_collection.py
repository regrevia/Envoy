from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))

from envoy_cli import collections
from envoy_cli import operations


def test_collection_checkpoint_updates_are_revision_checked_and_idempotent(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    collections.create_collection(
        "col-creator",
        "run-jobs",
        "https://www.douyin.com/user/creator",
        operation_id="create-1",
    )
    collections.update_collection(
        "col-creator",
        run_id="run-jobs",
        expected_revision=1,
        operation_id="discover-1",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "d" * 20,
            "candidates": [{"candidate_id": "vid-1"}],
        }},
    )

    first = collections.update_collection(
        "col-creator",
        run_id="run-jobs",
        expected_revision=2,
        operation_id="select-1",
        action="select",
        candidate_ids=["vid-1"],
    )
    replay = collections.update_collection(
        "col-creator",
        run_id="run-jobs",
        expected_revision=2,
        operation_id="select-1",
        action="select",
        candidate_ids=["vid-1"],
    )

    assert first["manifest_revision"] == 3
    assert replay == first
    with pytest.raises(collections.CollectionRevisionConflict):
        collections.update_collection(
            "col-creator",
            run_id="run-jobs",
            expected_revision=2,
            operation_id="select-2",
            action="select",
            candidate_ids=["vid-2"],
        )


def test_checkpoint_operation_id_rejects_reuse_with_different_input(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    collections.create_collection(
        "col-operation-fingerprint",
        "run-1",
        "https://www.douyin.com/user/creator",
        operation_id="create-1",
    )
    collections.update_collection(
        "col-operation-fingerprint",
        run_id="run-1",
        expected_revision=1,
        operation_id="save-snapshot",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "f" * 20,
            "candidates": [{"candidate_id": "candidate-1"}],
        }},
    )

    with pytest.raises(collections.CollectionError, match="different checkpoint input"):
        collections.update_collection(
            "col-operation-fingerprint",
            run_id="run-1",
            expected_revision=2,
            operation_id="save-snapshot",
            action="set_cursor",
            candidate_id_value="candidate-1",
        )


def test_collection_retry_budget_limits_processing_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    collections.create_collection(
        "col-retry-budget",
        "run-1",
        "https://www.douyin.com/user/creator",
        operation_id="create-1",
        budget=collections.CollectionBudget(max_retries=0),
    )
    collections.update_collection(
        "col-retry-budget",
        run_id="run-1",
        expected_revision=1,
        operation_id="save-snapshot",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "a" * 20,
            "candidates": [{"candidate_id": "candidate-1"}],
        }},
    )
    collections.update_collection(
        "col-retry-budget",
        run_id="run-1",
        expected_revision=2,
        operation_id="select",
        action="select",
        candidate_ids=["candidate-1"],
    )
    collections.update_collection(
        "col-retry-budget",
        run_id="run-1",
        expected_revision=3,
        operation_id="start-first",
        action="item_state",
        candidate_id_value="candidate-1",
        item_state="processing",
    )
    collections.update_collection(
        "col-retry-budget",
        run_id="run-1",
        expected_revision=4,
        operation_id="fail-first",
        action="item_state",
        candidate_id_value="candidate-1",
        item_state="failed",
    )

    with pytest.raises(collections.CollectionError, match="retry budget exhausted"):
        collections.update_collection(
            "col-retry-budget",
            run_id="run-1",
            expected_revision=5,
            operation_id="start-retry",
            action="item_state",
            candidate_id_value="candidate-1",
            item_state="processing",
        )


def test_collection_checkpoint_survives_process_restart(tmp_path):
    env = {
        **os.environ,
        "ENVOY_LOCAL_DATA_DIR": str(tmp_path),
        "PYTHONPATH": os.pathsep.join((
            str(ROOT / "packages" / "envoy-core" / "src"),
            str(ROOT / "packages" / "envoy-cli" / "src"),
        )),
    }
    create = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from envoy_cli.collections import create_collection; "
                "create_collection('col-restart','run-1',"
                "'https://www.douyin.com/user/creator',operation_id='create')"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    read = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from envoy_cli.collections import get_collection; "
                "print(json.dumps(get_collection('col-restart','run-1')))"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert create.returncode == 0, create.stderr
    assert read.returncode == 0, read.stderr
    manifest = json.loads(read.stdout)
    assert manifest["collection_id"] == "col-restart"
    assert manifest["run_id"] == "run-1"
    assert manifest["manifest_revision"] == 1


def test_collection_get_makes_expired_processing_item_retryable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    now = [1_000.0]
    monkeypatch.setattr(collections.time, "time", lambda: now[0])
    collections.create_collection(
        "col-lease",
        "run-lease",
        "https://www.douyin.com/user/creator",
        operation_id="create-lease",
    )
    collections.update_collection(
        "col-lease",
        run_id="run-lease",
        expected_revision=1,
        operation_id="discover-lease",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "e" * 20,
            "candidates": [{"candidate_id": "candidate-1"}],
        }},
    )
    collections.update_collection(
        "col-lease",
        run_id="run-lease",
        expected_revision=2,
        operation_id="select-lease",
        action="select",
        candidate_ids=["candidate-1"],
    )
    collections.update_collection(
        "col-lease",
        run_id="run-lease",
        expected_revision=3,
        operation_id="start-lease",
        action="item_state",
        candidate_id_value="candidate-1",
        item_state="processing",
    )
    now[0] += 301

    manifest = collections.get_collection("col-lease", "run-lease")

    assert manifest["items"]["candidate-1"]["state"] == "selected"
    assert manifest["items"]["candidate-1"]["retryable"] is True
    assert manifest["items"]["candidate-1"]["lease_expires_at"] is None
    assert manifest["manifest_revision"] == 5


def test_collection_checkpoint_tracks_partial_frame_observations(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    collections.create_collection(
        "col-observations",
        "run-observations",
        "https://www.douyin.com/user/creator",
        operation_id="create-observations",
    )
    collections.update_collection(
        "col-observations",
        run_id="run-observations",
        expected_revision=1,
        operation_id="save-candidates",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "b" * 20,
            "candidates": [{"candidate_id": "candidate-1"}],
        }},
    )
    collections.update_collection(
        "col-observations",
        run_id="run-observations",
        expected_revision=2,
        operation_id="select-observation",
        action="select",
        candidate_ids=["candidate-1"],
    )
    collections.update_collection(
        "col-observations",
        run_id="run-observations",
        expected_revision=3,
        operation_id="partial-handoff",
        action="record_handoff",
        candidate_id_value="candidate-1",
        pending_resource_ids=["frame-1", "frame-2"],
    )
    manifest = collections.update_collection(
        "col-observations",
        run_id="run-observations",
        expected_revision=4,
        operation_id="observe-first",
        action="record_observation",
        candidate_id_value="candidate-1",
        observed_resource_ids=["frame-1"],
    )

    assert manifest["items"]["candidate-1"]["pending_resource_ids"] == ["frame-2"]
    assert manifest["items"]["candidate-1"]["observed_resource_ids"] == ["frame-1"]
    assert manifest["items"]["candidate-1"]["state"] == "partial"


def test_collection_get_marks_evidence_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    now = [2_000.0]
    monkeypatch.setattr(collections.time, "time", lambda: now[0])
    collections.create_collection(
        "col-evidence",
        "run-evidence",
        "https://www.douyin.com/user/creator",
        operation_id="create-evidence",
    )
    collections.update_collection(
        "col-evidence",
        run_id="run-evidence",
        expected_revision=1,
        operation_id="save-evidence-candidate",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "c" * 20,
            "candidates": [{"candidate_id": "candidate-1"}],
        }},
    )
    collections.update_collection(
        "col-evidence",
        run_id="run-evidence",
        expected_revision=2,
        operation_id="select-evidence-candidate",
        action="select",
        candidate_ids=["candidate-1"],
    )
    collections.update_collection(
        "col-evidence",
        run_id="run-evidence",
        expected_revision=3,
        operation_id="record-expiring-evidence",
        action="record_evidence",
        candidate_id_value="candidate-1",
        material_id="mat-example",
        source_revision="revision-example",
        expires_at=2_001.0,
    )
    now[0] = 2_002.0

    manifest = collections.get_collection("col-evidence", "run-evidence")

    assert manifest["evidence"]["candidate-1"]["availability"] == "expired"
    assert manifest["manifest_revision"] == 5


def test_candidate_canonicalization_ignores_tracking_query_parameters():
    urls = [
        "https://www.douyin.com/video/123456?from=share&modal_id=123456",
        "https://www.douyin.com/video/123456?source=copy_link",
        "https://www.douyin.com/note/123456?from=web",
    ]

    assert [collections.canonical_candidate_url(url) for url in urls] == [
        "https://www.douyin.com/video/123456",
        "https://www.douyin.com/video/123456",
        "https://www.douyin.com/note/123456",
    ]


def test_manifest_accumulates_discovery_pages_without_duplicate_candidates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    collections.create_collection(
        "col-pages",
        "run-pages",
        "https://www.douyin.com/user/creator",
        operation_id="create-pages",
    )
    first_candidate = {
        "candidate_id": "douyin-a",
        "source_url": "https://www.douyin.com/video/1",
    }
    second_candidate = {
        "candidate_id": "douyin-b",
        "source_url": "https://www.douyin.com/note/2",
    }
    first = collections.update_collection(
        "col-pages",
        run_id="run-pages",
        expected_revision=1,
        operation_id="page-1",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "a" * 20,
            "candidates": [first_candidate],
            "next_cursor": "cursor-2",
        }},
    )
    second = collections.update_collection(
        "col-pages",
        run_id="run-pages",
        expected_revision=2,
        operation_id="page-2",
        action="discovery_snapshot",
        usage={"snapshot": {
            "discovery_revision": "a" * 20,
            "candidates": [first_candidate, second_candidate],
            "next_cursor": None,
        }},
    )

    assert [item["candidate_id"] for item in second["candidates"]] == [
        "douyin-a",
        "douyin-b",
    ]
    assert second["usage"]["scanned_pages"] == 2
    assert second["usage"]["scanned_candidates"] == 2
    assert second["cursor"] is None
    assert first["manifest_revision"] == 2


def test_collection_budget_accumulates_actual_usage_and_stops_at_limit():
    budget = collections.CollectionBudget(
        max_items=2,
        max_download_bytes=100,
    )
    current = {"selected_items": 1, "downloaded_bytes": 70}

    assert budget.can_add(current, selected_items=1, downloaded_bytes=20)
    assert not budget.can_add(current, selected_items=2, downloaded_bytes=20)


def test_idempotent_operation_replays_result_after_host_checkpoint_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    calls = []

    first = operations.run_idempotent(
        "read-operation-1",
        fingerprint="source-and-scope",
        execute=lambda: calls.append("expensive-read") or {"result": "partial", "material_id": "mat-1"},
    )
    replay = operations.run_idempotent(
        "read-operation-1",
        fingerprint="source-and-scope",
        execute=lambda: calls.append("unexpected-repeat") or {},
    )

    assert first == replay
    assert calls == ["expensive-read"]


def test_idempotent_operation_rejects_reuse_for_different_input(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    operations.run_idempotent(
        "read-operation-2",
        fingerprint="first-source",
        execute=lambda: {"result": "ready"},
    )

    with pytest.raises(operations.OperationConflict):
        operations.run_idempotent(
            "read-operation-2",
            fingerprint="different-source",
            execute=lambda: pytest.fail("mismatched idempotency key must not run"),
        )


def test_douyin_note_gallery_keeps_order_and_excludes_recommendations():
    from envoy_cli.reader import extract_douyin_note_gallery

    images, total_count = extract_douyin_note_gallery(
        """
        <main>
          <article data-e2e="note-detail">
            <div class="swiper-slide"><img data-src="/note/one.jpg" alt="第一张"></div>
            <div class="swiper-slide"><img data-lazy-src="/note/two.jpg" alt="第二张"></div>
          </article>
          <section class="related-list-item-in-small-card">
            <div class="swiper-slide"><img src="/recommend/cover.jpg" alt="推荐"></div>
          </section>
          <footer><div class="swiper-slide"><img src="/footer/ui.png"></div></footer>
        </main>
        """,
        "https://www.douyin.com/note/123",
    )

    assert [image["url"] for image in images] == [
        "https://www.douyin.com/note/one.jpg",
        "https://www.douyin.com/note/two.jpg",
    ]
    assert [image["alt"] for image in images] == ["第一张", "第二张"]
    assert total_count is None


def test_douyin_note_read_preserves_album_order_and_never_calls_asr(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://www.douyin.com/note/123"
    html = """
    <html><title>图文岗位信息</title><body>
      <p>这是一篇包含岗位信息的图文笔记，正文说明工作内容与要求。</p>
      <main data-e2e="note-detail"><article class="note-detail-container">
        <div class="swiper-slide"><img data-src="/note/one.jpg" alt="第一页"></div>
        <div class="swiper-slide"><img data-lazy-src="/note/two.jpg" alt="第二页"></div>
      </article></main>
      <section class="related-list-item-in-small-card">
        <div class="swiper-slide"><img src="/recommend/cover.jpg"></div>
      </section>
    </body></html>
    """
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda *_args: pytest.fail(
        "Douyin note pages must not enter the single-video extractor"
    ))
    monkeypatch.setattr(reader, "_read_video_asr", lambda *_args, **_kwargs: pytest.fail(
        "an image note must never invoke ASR"
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "douyin-note-read",
        "source": {"url": source_url},
        "host_capabilities": {"image_input": "uri"},
    }))

    image_coverage = response.coverage.modalities["images"]
    assert response.result.value == "partial"
    assert [resource.delivery.uri for resource in response.resources] == [
        "https://www.douyin.com/note/one.jpg",
        "https://www.douyin.com/note/two.jpg",
    ]
    assert image_coverage.details["item_type"] == "image_note"
    assert image_coverage.details["album_total_known"] is False
    assert image_coverage.details["read_image_count"] == 2
    assert "Some image resources could not be safely delivered" not in image_coverage.unresolved


def test_profile_discovery_normalizes_bounded_flat_metadata_without_media_work(
    monkeypatch,
):
    import contextlib
    from envoy_cli import video

    captured = {}

    class Downloader:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download):
            captured["url"] = url
            captured["download"] = download
            return {
                "entries": [
                    {
                        "id": "123",
                        "title": "招聘岗位分享",
                        "ie_key": "Douyin",
                    },
                    {
                        "url": "https://www.douyin.com/note/456?from=share",
                        "title": "图文岗位",
                        "ie_key": "Douyin",
                    },
                ],
            }

    monkeypatch.setattr(collections, "_load_yt_dlp", lambda: (Downloader, []), raising=False)
    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (Downloader, []))
    monkeypatch.setattr("envoy_cli.media.public_network_guard", contextlib.nullcontext)
    monkeypatch.setattr("envoy_cli.media.guard_yt_dlp_client", lambda _ydl: None)

    result = collections.discover_profile(
        "https://www.douyin.com/user/creator?from=share",
        limit=20,
    )

    assert result["candidates"][0]["source_url"] == "https://www.douyin.com/video/123"
    assert result["candidates"][0]["item_type"] == "video"
    assert result["candidates"][1]["item_type"] == "image_note"
    assert result["has_more"] is None
    assert captured["download"] is False
    assert captured["options"]["ignoreconfig"] is True
    assert captured["options"]["playlistend"] <= 100


def test_profile_discovery_cursor_replays_persisted_snapshot(
    tmp_path,
    monkeypatch,
):
    import contextlib
    from envoy_cli import video

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    calls = []

    class Downloader:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            calls.append(download)
            return {
                "entries": [
                    {"id": str(index), "title": f"岗位 {index}", "ie_key": "Douyin"}
                    for index in range(25)
                ],
            }

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (Downloader, []))
    monkeypatch.setattr("envoy_cli.media.public_network_guard", contextlib.nullcontext)
    monkeypatch.setattr("envoy_cli.media.guard_yt_dlp_client", lambda _ydl: None)

    first = collections.discover_profile(
        "https://www.douyin.com/user/creator",
        limit=10,
    )
    second = collections.discover_profile(
        "https://www.douyin.com/user/creator",
        limit=10,
        cursor=first["next_cursor"],
    )

    assert len(first["candidates"]) == 10
    assert len(second["candidates"]) == 10
    assert first["candidates"][0]["candidate_id"] != second["candidates"][0]["candidate_id"]
    assert first["discovery_revision"] == second["discovery_revision"]
    assert second["cursor_mode"] == "persisted_snapshot"
    assert calls == [False]


def test_profile_discovery_uses_isolated_browser_when_ytdlp_does_not_support_profile(
    monkeypatch,
    tmp_path,
):
    import contextlib
    from types import SimpleNamespace
    from envoy_cli import video

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))

    class Downloader:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, download):
            raise RuntimeError("Unsupported URL")

    page = SimpleNamespace(
        url="https://www.douyin.com/user/creator",
        title="creator",
        text="公开作品列表",
        html="""
        <main class="userNewUi">
          <img src="/avatar.jpg">
          <a href="/video/101"><span>岗位视频标题</span></a>
          <a href="/note/202"><span>岗位图文标题</span></a>
          <a href="/video/101?from=share">重复分享链接</a>
          <a href="/video/303">第三条</a>
        </main>
        <section class="related-list-item-in-small-card">
          <a href="/video/404">不得出现在候选中</a>
        </section>
        <footer>
          <a href="/video/505">footer 不得出现在候选中</a>
        </footer>
        """,
        media_urls=(),
        page_video_urls=(),
    )
    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (Downloader, []))
    monkeypatch.setattr("envoy_cli.media.public_network_guard", contextlib.nullcontext)
    monkeypatch.setattr("envoy_cli.media.guard_yt_dlp_client", lambda _ydl: None)
    monkeypatch.setattr("envoy_cli.browser.read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setattr("envoy_cli.reader._is_public_http_url", lambda *_args, **_kwargs: True)

    result = collections.discover_profile(
        "https://www.douyin.com/user/creator",
        limit=10,
    )

    assert [item["source_url"] for item in result["candidates"]] == [
        "https://www.douyin.com/video/101",
        "https://www.douyin.com/note/202",
        "https://www.douyin.com/video/303",
    ]
    assert [item["item_type"] for item in result["candidates"]] == [
        "video",
        "image_note",
        "video",
    ]
    assert result["has_more"] is None
