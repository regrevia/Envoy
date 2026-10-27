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

from envoy_cli.platforms import classify_public_url, is_platform_page, read_support_verdict


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


VERDICT_CASES = [
    # Supported: accepted in the coverage matrix or previously read on this machine.
    ("https://www.bilibili.com/video/BV1GJ411x7h7", "supported"),
    ("https://b23.tv/av170001", "supported"),
    ("https://www.douyin.com/video/7300000000000000000", "supported"),
    ("https://www.douyin.com/note/7300000000000000000", "supported"),
    ("https://v.douyin.com/iRNBho6u/", "supported"),
    ("https://weibo.com/tv/show/1034:4800000000000000", "supported"),
    ("https://weibo.com/1234567890/NabcdefGH", "supported"),
    ("https://www.tiktok.com/@creator/video/7300000000000000000", "supported"),
    ("https://vm.tiktok.com/ZMabcdef/", "supported"),
    ("https://k.kuaishou.com/short-video/3xabcdef", "supported"),
    ("https://channels.weixin.qq.com/web/pages/feed?feedId=abc", "supported"),
    ("https://www.xiaoyuzhoufm.com/episode/650000000000000000000000", "supported"),
    ("https://mp.weixin.qq.com/s/AbCdEfGhIjKlMn", "supported"),
    ("https://zhuanlan.zhihu.com/p/123456789", "supported"),
    ("https://36kr.com/p/123456789", "supported"),
    ("https://github.com/org/repo", "supported"),
    ("https://gist.github.com/user/abc123", "supported"),
    ("https://zh.wikipedia.org/wiki/Envoy", "supported"),
    ("https://developer.mozilla.org/en-US/docs/Web", "supported"),
    ("https://arxiv.org/abs/2401.00001", "supported"),
    ("https://example.org/paper.pdf", "supported"),
    ("https://example.org/audio.mp3", "supported"),
    ("https://feeds.example.org/podcast/feed.xml", "supported"),
    # Unsupported: anonymous login walls, no public read.
    ("https://www.instagram.com/p/Cabc123/", "unsupported"),
    ("https://www.instagram.com/reel/Cabc123/", "unsupported"),
    ("https://x.com/user/status/123", "unsupported"),
    ("https://twitter.com/user/status/123", "unsupported"),
    ("https://www.linkedin.com/posts/user_abc", "unsupported"),
    ("https://live.douyin.com/123456", "unsupported"),
    ("https://www.douyin.com/live/123456", "unsupported"),
    ("https://t.me/+AbCdEfGh", "unsupported"),
    ("https://t.me/joinchat/AbCdEfGh", "unsupported"),
    ("https://t.me/c/1234567890/42", "unsupported"),
    # Unknown: readable-looking but unproven, so Envoy is still tried first.
    ("https://www.youtube.com/watch?v=abc123", "unknown"),
    ("https://vimeo.com/123456", "supported"),
    ("https://vimeo.com/channels/staffpicks", "unknown"),
    ("https://vimeo.com/123456/abc", "unknown"),
    ("https://www.dailymotion.com/video/x8abc", "unknown"),
    ("https://www.xiaohongshu.com/explore/65abc123", "unknown"),
    ("https://xhslink.com/a/abc123", "unknown"),
    ("https://www.pinterest.com/pin/123456/", "supported"),
    ("https://www.figma.com/community", "supported"),
    ("https://api.github.com/repos/psf/requests", "supported"),
    ("https://www.cnblogs.com/rossiXYZ/p/15553994.html", "supported"),
    ("https://book.douban.com/subject/1084336/", "supported"),
    ("https://www.ithome.com/", "supported"),
    ("https://www.ifanr.com/", "supported"),
    ("https://www.pingwest.com/", "supported"),
    ("https://www.qbitai.com/", "supported"),
    ("https://dev.to/", "supported"),
    ("https://zenn.dev/", "supported"),
    ("https://qiita.com/", "supported"),
    ("https://lwn.net/", "supported"),
    ("https://www.brendangregg.com/blog/index.html", "supported"),
    ("https://techcrunch.com/", "supported"),
    ("https://www.ted.com/talks/chimamanda_ngozi_adichie_the_danger_of_a_single_story", "supported"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview", "supported"),
    ("https://docs.python.org/3/library/asyncio.html", "supported"),
    ("https://www.solidot.org/", "supported"),
    ("https://www.guancha.cn/", "supported"),
    ("https://www.thepaper.cn/", "supported"),
    ("https://www.ximalaya.com/album/316137", "supported"),
    ("https://www.douban.com/group/topic/30678422/", "supported"),
    ("https://gitlab.com/gitlab-org/gitlab/-/blob/master/README.md", "supported"),
    ("https://www.jiemian.com/", "supported"),
    ("https://www.cls.cn/", "supported"),
    ("https://www.yicai.com/", "supported"),
    ("https://www.caixin.com/", "supported"),
    ("https://wallstreetcn.com/", "supported"),
    ("https://www.ruanyifeng.com/blog/", "supported"),
    ("https://commons.wikimedia.org/wiki/Main_Page", "supported"),
    ("https://www.amazon.com/dp/B000000000", "unknown"),
    ("https://www.douyin.com/user/MS4wLjABAAAA", "unknown"),
    ("https://example.org/article", "unknown"),
    ("", "unknown"),
]


@pytest.mark.parametrize(("url", "expected"), VERDICT_CASES)
def test_read_support_verdict(url: str, expected: str) -> None:
    assert read_support_verdict(url) == expected


def test_verdict_is_offline_and_stable() -> None:
    # No HTTP client: repeated calls return the same verdict for share links.
    for url in ("https://v.douyin.com/abc/", "https://t.cn/A6abcdef"):
        assert read_support_verdict(url) == read_support_verdict(url)
