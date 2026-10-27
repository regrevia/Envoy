from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "envoy-cli" / "src"))


def test_platform_classification_covers_xhs_and_common_video_pages():
    from envoy_cli.platforms import classify_public_url

    assert classify_public_url("https://www.xiaohongshu.com/explore/65abc123") == "xiaohongshu_note"
    assert classify_public_url("https://xhslink.com/a/abc123") == "xiaohongshu_share"
    assert classify_public_url("https://www.youtube.com/shorts/abc123") == "video_page"
    assert classify_public_url("https://vimeo.com/244405542") == "video_page"
    assert classify_public_url("https://www.dailymotion.com/video/x75iqs7") == "video_page"
    assert classify_public_url("https://x.com/user/status/123") == "platform_page"
    assert classify_public_url("https://www.instagram.com/p/abc123/") == "platform_page"


def test_platform_page_browser_fallback_reads_content_without_assuming_video(
    monkeypatch,
    tmp_path,
):
    from types import SimpleNamespace
    from envoy_cli import browser, reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://x.com/example/status/123"
    html = "<html><title>Public post</title><body><p>Public post text available from page.</p></body></html>"
    page = SimpleNamespace(
        url=source_url,
        title="Public post",
        text="Public post text available from page.",
        html=html,
        media_urls=(),
        page_video_urls=(),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda *_args: False)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "x-status-page",
        "source": {"url": source_url},
    }))

    # A 34-character platform page is not a complete read: thin platform pages
    # report partial so a browser/JS shell is never sold as the whole post.
    assert response.result.value == "partial"
    assert "Public post text" in " ".join(
        block.content.text for block in response.blocks if block.content.type == "text"
    )
    assert "video" not in response.coverage.modalities


def test_embedded_video_diagnostics_only_expose_media_origin(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://example.org/article"
    html = (
        "<html><title>Article</title><body>"
        "<p>Public article content with enough text to be extracted safely.</p>"
        '<video src="https://cdn.example.org/private/path.mp4?sign=secret&uifid=proof"></video>'
        "</body></html>"
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "embedded-video-url-redaction",
        "source": {"url": source_url},
    }))

    video_details = response.coverage.modalities["video"].details
    assert video_details["urls"] == ["https://cdn.example.org"]
    serialized = response.model_dump_json()
    assert "private/path.mp4" not in serialized
    assert "secret" not in serialized
    assert "proof" not in serialized


def test_http_404_returns_user_check_and_proxy_diagnostics(monkeypatch):
    import httpx
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://www.example.org/post/123"
    response_404 = httpx.Response(
        404,
        request=httpx.Request("GET", source_url),
    )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            class ResponseContext:
                def __enter__(self):
                    return response_404

                def __exit__(self, *_args):
                    response_404.close()
                    return False

            return ResponseContext()

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader.httpx, "Client", lambda *_args, **_kwargs: FakeClient())
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    result = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "read-404",
        "source": {"url": source_url},
    }))

    error = result.errors[0]
    assert error.code.value == "source_unavailable"
    assert error.details["http_status"] == 404
    assert error.details["user_check_recommended"] is True
    assert error.details["proxy_configured"] is False
    assert "user-approved trusted proxy" in error.details["suggested_next_action"]


def test_xhs_note_parser_preserves_order_and_excludes_non_note_images():
    from envoy_cli.platforms import extract_xhs_note_images

    images, total = extract_xhs_note_images(
        """
        <script id="state">{"note":{"noteId":"65abc123","imageList":[
          {"urlDefault":"https://sns.example/a.jpg"},
          {"urlDefault":"https://sns.example/b.jpg"}
        ]}}</script>
        <main class="note-container">
          <img class="avatar" src="https://sns.example/avatar.jpg">
          <img src="https://sns.example/a.jpg">
          <img data-src="https://sns.example/b.jpg">
        </main>
        <section class="recommend"><img src="https://sns.example/recommend.jpg"></section>
        <footer><img src="https://sns.example/footer.jpg"></footer>
        """,
        "https://www.xiaohongshu.com/explore/65abc123",
    )

    assert [item["url"] for item in images] == [
        "https://sns.example/a.jpg",
        "https://sns.example/b.jpg",
    ]
    assert total == 2


def test_xhs_state_is_bound_to_requested_note_and_album_only():
    from envoy_cli.platforms import extract_xhs_note_images

    html = """<script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{
      "65abc123":{"note":{"noteId":"65abc123","imageList":[
        {"urlDefault":"https://sns.example/one.jpg"},
        {"urlDefault":"https://sns.example/two.jpg"}],
        "user":{"avatar":"https://sns.example/avatar.jpg"}}},
      "other":{"note":{"imageList":[{"url":"https://sns.example/other.jpg"}]}}
    }},"recommend":{"url":"https://sns.example/recommend.jpg"}};</script>"""
    images, total = extract_xhs_note_images(
        html, "https://www.xiaohongshu.com/explore/65abc123",
    )
    assert [image["url"] for image in images] == [
        "https://sns.example/one.jpg", "https://sns.example/two.jpg",
    ]
    assert total == 2


def test_xhs_unbound_state_and_unrelated_image_classes_are_not_evidence():
    from envoy_cli.platforms import extract_xhs_note_images

    images, total = extract_xhs_note_images(
        """<script>{"note":{"imageList":[{"url":"https://sns.example/other.jpg"}]},
        "total":100}</script><section class="image"><img src="/unrelated.jpg"></section>""",
        "https://www.xiaohongshu.com/explore/65abc123",
    )
    assert images == []
    assert total is None


def test_xhs_avatar_void_element_does_not_hide_album_images():
    from envoy_cli.platforms import extract_xhs_note_images

    images, total = extract_xhs_note_images(
        '<main class="note-container"><img class="avatar" src="/avatar.jpg">'
        '<div class="author"><img src="/author.jpg"></div>'
        '<div class="note-image"><img src="/one.jpg"></div></main>'
        '<div class="image"><img src="/outside.jpg"></div>',
        "https://www.xiaohongshu.com/explore/65abc123",
    )
    assert [image["url"] for image in images] == ["https://www.xiaohongshu.com/one.jpg"]
    assert total is None


def test_generic_video_routing_is_preserved():
    from envoy_cli.reader import _url_is_video_page

    assert _url_is_video_page("https://www.douyin.com/video/123")
    assert _url_is_video_page("https://example.org/watch/123")


def test_public_read_same_key_recovers_after_network_failure(monkeypatch, tmp_path):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ErrorCode, ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    calls = []

    def fetch(url, *args, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise reader.PublicPageError("temporary outage", ErrorCode.NETWORK_ERROR)
        return url, "<html><title>Recovered</title><p>Public text after network recovery.</p></html>"

    monkeypatch.setattr(reader, "_fetch_html", fetch)
    request = ReadRequest.model_validate({
        "request_id": "retry-read",
        "idempotency_key": "same-network-read",
        "source": {"url": "https://example.org/article"},
    })
    assert reader.read_public_page(request).result.value == "blocked"
    assert reader.read_public_page(request).result.value == "ready"
    assert reader.read_public_page(request).result.value == "ready"
    assert len(calls) == 2


def test_public_read_recovers_persisted_response_without_refetching(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    calls = []
    source_url = "https://example.org/article"
    material_id = "mat-" + "a" * 24
    source_revision = "b" * 64

    response = {
        "protocol_version": "envoy.read/1",
        "request_id": "public-crash-first",
        "session_id": "public-crash-session",
        "material_id": material_id,
        "source_revision": source_revision,
        "execution_state": "completed",
        "result": "partial",
        "blocks": [],
        "resources": [],
        "coverage": {},
        "needs": [],
        "errors": [],
        "usage": {},
    }

    def fetch(url, *_args, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            reader._store_material(material_id, {
                "source_url": source_url,
                "source_revision": source_revision,
                "session_id": "public-crash-session",
                "operation_response": response,
                "operation_metadata": {"source_url": source_url},
            })
            raise KeyboardInterrupt("crash after material persistence")
        raise AssertionError("retry must recover the persisted public response")

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", fetch)
    request = ReadRequest.model_validate({
        "request_id": "public-crash-first",
        "idempotency_key": "public-crash-operation",
        "session_id": "public-crash-session",
        "source": {"url": source_url},
    })

    with pytest.raises(KeyboardInterrupt):
        reader.read_public_page(request)

    recovered = reader.read_public_page(
        request.model_copy(update={"request_id": "public-crash-retry"})
    )

    assert recovered.material_id == material_id
    assert recovered.request_id == "public-crash-retry"
    assert calls == [source_url]


def test_public_read_operation_metadata_redacts_source_credentials(
    monkeypatch,
    tmp_path,
):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ReadRequest

    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    source_url = "https://user:password@example.org/article"
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_args, **_kwargs: (
            source_url,
            "<html><title>Article</title><p>Public article text with enough content.</p></html>",
        ),
    )

    request = ReadRequest.model_validate({
        "request_id": "metadata-redaction",
        "idempotency_key": "metadata-redaction-operation",
        "session_id": "metadata-redaction-session",
        "source": {"url": source_url},
    })
    response = reader.read_public_page(request)
    material_path = tmp_path / "materials" / f"{response.material_id}.json"
    stored = material_path.read_text(encoding="utf-8")

    assert "operation_metadata" in stored
    assert "example.org/article" in stored
    assert "user@" not in stored
    assert "password" not in stored


def test_xhs_parser_marks_unknown_album_total():
    from envoy_cli.platforms import extract_xhs_note_images

    images, total = extract_xhs_note_images(
        '<article class="note-container"><div class="note-image"><img src="/one.jpg"></div></article>',
        "https://www.xiaohongshu.com/explore/65abc123",
    )

    assert [item["url"] for item in images] == [
        "https://www.xiaohongshu.com/one.jpg",
    ]
    assert total is None


def test_xhs_read_uses_note_images_without_video_extractor_or_asr(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from envoy_cli import browser, reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://xhslink.com/a/abc123"
    final_url = "https://www.xiaohongshu.com/explore/65abc123"
    html = """
    <html><head><title>周末咖啡店清单</title></head>
    <body><main class="note-container">
      <p>这是一篇公开分享的图文笔记，整理了三家咖啡店。</p>
      <div class="note-image"><img src="https://sns.example/one.jpg"></div>
      <div class="note-image"><img src="https://sns.example/two.jpg"></div>
    </main></body></html>
    """
    page = SimpleNamespace(
        url=final_url,
        title="周末咖啡店清单",
        text="这是一篇公开分享的图文笔记，整理了三家咖啡店。",
        html=html,
        media_urls=(),
        page_video_urls=("https://media.example/recommendation.mp4",),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (final_url, html))
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda *_args: pytest.fail(
        "image-note links must not enter the video extractor"
    ))
    monkeypatch.setattr(reader, "_read_video_asr", lambda *_args, **_kwargs: pytest.fail(
        "unbound recommendation video must not enter ASR"
    ))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_safe_visual_candidates", lambda candidates, *_args: (
        [
            {
                "image": item,
                "image_url": item["url"],
                "block_id": f"blk-image-{index}",
                "resource_id": f"resource-image-{index}",
            }
            for index, item in enumerate(candidates, 1)
        ],
        0,
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "xhs-note-read",
        "source": {"url": source_url},
        "host_capabilities": {"image_input": "uri"},
    }))

    assert response.coverage.modalities["images"].details["item_type"] == "image_note"
    assert response.coverage.modalities["images"].details["album_total_known"] is False
    assert [resource.delivery.uri for resource in response.resources] == [
        "https://sns.example/one.jpg",
        "https://sns.example/two.jpg",
    ]
    assert not response.coverage.modalities.get("video")


def test_public_image_note_can_return_source_bound_ocr_regions(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from envoy_cli import browser, reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://www.xiaohongshu.com/explore/65abc123"
    html = """
    <html><head><title>岗位图文</title></head><body>
      <main class="note-container"><div class="note-image">
        <img src="https://sns.example/job.png">
      </div></main></body></html>
    """
    page = SimpleNamespace(
        url=source_url,
        title="岗位图文",
        text="",
        html=html,
        media_urls=(),
        page_video_urls=(),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setattr(reader, "_download_image_for_ocr", lambda *_args, **_kwargs: (
        tmp_path / "job.png", "a" * 64,
    ))
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "tesseract",
        "regions": [{
            "text": "深圳岗位",
            "bbox": [0.1, 0.2, 0.8, 0.4],
            "confidence": 0.91,
        }],
    })
    monkeypatch.setattr(reader, "_safe_visual_candidates", lambda candidates, *_args: (
        [{
            "image": item,
            "image_url": item["url"],
            "block_id": "blk-image-0001",
            "resource_id": "resource-image-1",
        } for item in candidates],
        0,
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "xhs-ocr",
        "source": {"url": source_url},
        "ocr_profile": "balanced",
    }))

    ocr = [block for block in response.blocks if block.kind.value == "ocr"]
    assert len(ocr) == 1
    assert ocr[0].content.text == "深圳岗位"
    assert ocr[0].locator.type == "image_region"
    assert ocr[0].locator.resource_id == "resource-image-1"
    assert response.coverage.modalities["ocr"].details["region_count"] == 1


def test_public_image_note_marks_empty_ocr_as_partial_for_visual_review(
    monkeypatch,
    tmp_path,
):
    from types import SimpleNamespace
    from envoy_cli import browser, reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://www.xiaohongshu.com/explore/65abc123"
    image_url = "https://sns.example/no-readable-text.png"
    html = f"""
    <html><head><title>图文笔记</title></head><body>
      <main class="note-container"><div class="note-image">
        <img src="{image_url}">
      </div></main></body></html>
    """
    page = SimpleNamespace(
        url=source_url,
        title="图文笔记",
        text="",
        html=html,
        media_urls=(),
        page_video_urls=(),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setattr(
        reader,
        "_download_image_for_ocr",
        lambda *_args, **_kwargs: (tmp_path / "note.png", "a" * 64),
    )
    monkeypatch.setattr(reader, "_read_cached_image_ocr", lambda *_args, **_kwargs: {
        "backend": "paddleocr",
        "regions": [],
    })
    monkeypatch.setattr(reader, "_safe_visual_candidates", lambda candidates, *_args: (
        [{
            "image": item,
            "image_url": item["url"],
            "block_id": f"blk-image-{index:04d}",
            "resource_id": f"resource-image-{index}",
        } for index, item in enumerate(candidates, start=1)],
        0,
    ))
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))

    response = reader.read_public_page(ReadRequest.model_validate({
        "request_id": "xhs-ocr-empty",
        "source": {"url": source_url},
        "ocr_profile": "balanced",
        "host_capabilities": {"image_input": "uri"},
    }))

    ocr_coverage = response.coverage.modalities["ocr"]
    assert not [block for block in response.blocks if block.kind.value == "ocr"]
    assert ocr_coverage.extraction == "partial"
    assert any("no valid text regions" in item for item in ocr_coverage.unresolved)
    assert any(resource.resource_id == "resource-image-1" for resource in response.resources)
    assert response.needs and response.needs[0].type == "inspect_visual"


def test_xhslink_cn_short_link_is_xiaohongshu_share():
    from envoy_cli.platforms import classify_public_url

    assert classify_public_url("https://xhslink.cn/o/AAFFkhsOa1w") == "xiaohongshu_share"
    assert classify_public_url("https://www.xhslink.cn/a/abc123") == "xiaohongshu_share"


def _xhs_response(monkeypatch, tmp_path, html, request_id):
    from types import SimpleNamespace
    from envoy_cli import browser, reader
    from envoy_core.schemas.read_v1 import ReadRequest

    source_url = "https://www.xiaohongshu.com/explore/65abc123"
    page = SimpleNamespace(
        url=source_url,
        title="page",
        text="page",
        html=html,
        media_urls=(),
        page_video_urls=(),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader, "has_specialized_extractor", lambda *_args: False)
    monkeypatch.setattr(reader, "_fetch_html", lambda *_args, **_kwargs: (source_url, html))
    monkeypatch.setattr(browser, "read_page", lambda *_args, **_kwargs: page)
    monkeypatch.setenv("ENVOY_LOCAL_DATA_DIR", str(tmp_path / "materials"))
    return reader.read_public_page(ReadRequest.model_validate({
        "request_id": request_id,
        "source": {"url": source_url},
    }))


def test_platform_ip_risk_page_blocks_instead_of_reporting_ready(monkeypatch, tmp_path):
    html = (
        "<html><title>安全限制</title><body>"
        "<p>安全限制 IP存在风险，请切换可靠网络环境后重试 300012 我要反馈 返回首页</p>"
        "</body></html>"
    )
    response = _xhs_response(monkeypatch, tmp_path, html, "xhs-ip-risk")

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "challenge_required"
    assert response.errors[0].action_required is True


def test_missing_note_page_blocks_with_source_not_found(monkeypatch, tmp_path):
    html = (
        "<html><title>小红书 - 你访问的页面不见了</title><body>"
        "<p>发现 RED 直播 发布 通知 消息 © 2014-2026 行吟信息科技（上海）有限公司</p>"
        "</body></html>"
    )
    response = _xhs_response(monkeypatch, tmp_path, html, "xhs-missing")

    assert response.result.value == "blocked"
    assert response.errors[0].code.value == "source_unavailable"
    assert response.errors[0].details.get("diagnosis") == "source_not_found"


def test_short_links_and_chinese_content_sites_are_classified():
    """Share links and article hosts must not fall through to bare generic."""

    from envoy_cli.platforms import classify_public_url, is_platform_page

    assert classify_public_url("https://b23.tv/av170001") == "bilibili_share"
    assert classify_public_url("https://bili2233.cn/BV1GJ411x7h7") == "bilibili_share"
    assert classify_public_url("https://m.bilibili.com/video/BV1GJ411x7h7") == "video_page"
    assert (
        classify_public_url("https://www.zhihu.com/question/19550225/answer/123456")
        == "article_page"
    )
    assert classify_public_url("https://zhuanlan.zhihu.com/p/123456") == "article_page"
    assert classify_public_url("https://www.zhihu.com/question/19550225") == "platform_page"
    assert (
        classify_public_url("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn")
        == "article_page"
    )
    for url in (
        "https://b23.tv/av170001",
        "https://www.zhihu.com/question/19550225/answer/123456",
        "https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn",
    ):
        assert is_platform_page(url) is True
