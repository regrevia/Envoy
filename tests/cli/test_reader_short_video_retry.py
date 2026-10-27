from types import SimpleNamespace

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest


SHELL_HTML = (
    "<html><head><title>抖音</title></head><body><p>"
    "精选 推荐 关注 朋友 同城 直播 放映厅 长视频 搜索 消息 我 创作者服务中心 下载客户端 广告投放 用户服务协议 隐私政策 账号找回 举报中心"
    "</p></body></html>"
)
LONG_PAGE_HTML = "<html><head><title>Doc</title></head><body><p>" + ("plain body text " * 20) + "</p></body></html>"
CAPTION = "猫在键盘上踩出一段和弦，主人把这段旋律做成了完整的歌。 " * 6
CAPTION_HTML = f"<html><head><title>抖音</title></head><body><p>{CAPTION}</p></body></html>"


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate({"request_id": "short-video-retry", "source": {"url": url}})


def _prepare(monkeypatch, pages):
    calls = []

    def fake_browser(*_args, **_kwargs):
        calls.append(1)
        return pages.pop(0) if pages else (None, None)

    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    monkeypatch.setattr(reader, "_should_read_media_first", lambda *_a, **_k: False)
    monkeypatch.setattr(reader, "fetch_feed", lambda *_a, **_k: None)
    monkeypatch.setattr(reader, "public_api_fallback", lambda *_a, **_k: None)
    monkeypatch.setattr(reader, "_read_with_isolated_browser", fake_browser)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_a, **_k: ("https://www.douyin.com/video/7698386448961683813", SHELL_HTML),
    )
    return calls


def test_thin_douyin_page_gets_one_more_browser_pass(monkeypatch):
    url = "https://www.douyin.com/video/7698386448961683813"
    pages = [
        (SimpleNamespace(url=url, html=SHELL_HTML, page_video_urls=[], media_urls=()), None),
        (SimpleNamespace(url=url, html=CAPTION_HTML, page_video_urls=[], media_urls=()), None),
    ]
    calls = _prepare(monkeypatch, pages)

    response = reader._read_public_page_once(_request(url))

    assert len(calls) == 2
    details = response.coverage.modalities["text"].details
    assert details["reader"] == "isolated_chromium"
    text = " ".join(block.content.text for block in response.blocks)
    assert "猫在键盘上踩出一段和弦" in text


def test_retry_is_attempted_once_and_only_once(monkeypatch):
    url = "https://www.douyin.com/video/7698386448961683813"
    pages = [
        (SimpleNamespace(url=url, html=SHELL_HTML, page_video_urls=[], media_urls=()), None),
        (SimpleNamespace(url=url, html=SHELL_HTML, page_video_urls=[], media_urls=()), None),
        (SimpleNamespace(url=url, html=CAPTION_HTML, page_video_urls=[], media_urls=()), None),
    ]
    calls = _prepare(monkeypatch, pages)

    reader._read_public_page_once(_request(url))

    assert len(calls) == 2


def test_a_thin_generic_page_is_not_retried(monkeypatch):
    url = "https://example.com/doc"
    pages = [(SimpleNamespace(url=url, html=LONG_PAGE_HTML, page_video_urls=[], media_urls=()), None)]
    calls = _prepare(monkeypatch, pages)
    monkeypatch.setattr(
        reader,
        "_fetch_html",
        lambda *_a, **_k: (url, LONG_PAGE_HTML),
    )

    reader._read_public_page_once(_request(url))

    assert len(calls) == 0
