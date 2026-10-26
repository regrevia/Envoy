from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for source in (
    ROOT / "packages" / "envoy-core" / "src",
    ROOT / "packages" / "envoy-cli" / "src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from envoy_cli.platforms import classify_public_url, is_platform_page


NEW_CASES = [
    # Douyin: short links stay page reads, real player URLs become media pages.
    ("https://v.douyin.com/iRNBho6u/", "platform_page"),
    ("https://www.douyin.com/video/7300000000000000000", "video_page"),
    ("https://www.douyin.com/share/video/7300000000000000000", "video_page"),
    # Douyin image notes must not take the media-first route.
    ("https://www.douyin.com/note/7300000000000000000", "platform_page"),
    ("https://www.douyin.com/user/MS4wLjABAAAA", "platform_page"),
    # Weibo: t.cn resolves through the browser; tv/show is a video page.
    ("https://t.cn/A6abcdef", "platform_page"),
    ("https://weibo.com/tv/show/1034:4800000000000000", "video_page"),
    ("https://weibo.com/1234567890/NabcdefGH", "platform_page"),
    ("https://m.weibo.cn/detail/4800000000000000", "platform_page"),
    # TikTok: short links are page reads, /@user/video/<id> is a video.
    ("https://vm.tiktok.com/ZMabcdef/", "platform_page"),
    ("https://www.tiktok.com/@creator/video/7300000000000000000", "video_page"),
    ("https://www.tiktok.com/@creator", "platform_page"),
    # Kuaishou: the k.kuaishou.com short host is a page read.
    ("https://k.kuaishou.com/short-video/3xabcdef", "platform_page"),
    ("https://www.kuaishou.com/short-video/3xabcdef", "video_page"),
    # WeChat Channels and podcast pages are page reads.
    ("https://channels.weixin.qq.com/web/pages/feed?feedId=abc", "platform_page"),
    ("https://www.xiaoyuzhoufm.com/episode/650000000000000000000000", "platform_page"),
    # Podcast feeds: the feed itself is a page read, not a media download.
    ("https://feeds.example.org/podcast/feed.xml", "platform_page"),
    ("https://feeds.example.org/show.rss", "platform_page"),
]

UNCHANGED_CASES = [
    ("https://www.xiaohongshu.com/explore/65abc123", "xiaohongshu_note"),
    ("https://xhslink.com/a/abc123", "xiaohongshu_share"),
    ("https://www.xiaohongshu.com/user/profile/abc", "generic"),
    ("https://b23.tv/av170001", "bilibili_share"),
    ("https://www.bilibili.com/video/BV1GJ411x7h7", "video_page"),
    ("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn", "article_page"),
    ("https://www.youtube.com/watch?v=abc123", "video_page"),
    ("https://x.com/user/status/123", "platform_page"),
    ("https://example.org/article", "generic"),
]


@pytest.mark.parametrize(("url", "expected"), NEW_CASES)
def test_new_hosts_classify_as_reader_known_types(url: str, expected: str) -> None:
    assert classify_public_url(url) == expected
    assert is_platform_page(url) is (expected != "generic")


@pytest.mark.parametrize(("url", "expected"), UNCHANGED_CASES)
def test_existing_classification_is_unchanged(url: str, expected: str) -> None:
    assert classify_public_url(url) == expected


def test_short_links_are_marked_without_network_resolution() -> None:
    # No HTTP client is involved: the same short token always maps to a page
    # read, and the isolated browser follows the redirect later.
    for short in (
        "https://v.douyin.com/abc/",
        "https://t.cn/A6abcdef",
        "https://vm.tiktok.com/ZMabcdef/",
        "https://k.kuaishou.com/short-video/3xabcdef",
    ):
        assert classify_public_url(short) == "platform_page"
        assert classify_public_url(short) == classify_public_url(short)


def test_platform_page_predicate_rejects_generic_and_feed_is_page_read() -> None:
    assert is_platform_page("https://example.org/post") is False
    assert is_platform_page("https://feeds.example.org/podcast/feed.xml") is True
    assert is_platform_page("https://www.douyin.com/note/7300000000000000000") is True
