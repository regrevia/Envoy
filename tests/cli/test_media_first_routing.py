"""A page that merely embeds a player must still be readable as text.

Media extractors (yt-dlp) match many ordinary hosts: a tweet, a subreddit
thread, a product page, a record page, an archive record. Sending those
through subtitle extraction and then local ASR turns a readable link into
``missing_capability``. Only a genuine single-video page - or a caller that
asked for visuals - should take the media-first route.
"""

from __future__ import annotations

import pytest

from envoy_cli.reader import _should_read_media_first, _url_is_video_page


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=aircAruvnKk",
        "https://www.bilibili.com/video/BV1GJ411x7h7",
        "https://vimeo.com/76979871",
        "https://www.tiktok.com/@scout2015/video/6718335390845095173",
    ],
)
def test_single_video_pages_stay_media_first(url: str) -> None:
    assert _url_is_video_page(url)
    assert _should_read_media_first(url, include_visual=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/OpenAI/status/1796823709356314851",
        "https://www.reddit.com/r/Python/comments/1c0v7q/",
        "https://www.amazon.com/dp/B08N5WRWNW",
        "https://archive.org/details/nasa",
        "https://soundcloud.com/forss/flickermood",
        "https://www.pinterest.com/pin/1139481143253437748/",
    ],
)
def test_embedded_player_pages_read_as_text(url: str) -> None:
    assert not _url_is_video_page(url)
    assert not _should_read_media_first(url, include_visual=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.reddit.com/r/Python/comments/1c0v7q/",
        "https://www.pinterest.com/pin/1139481143253437748/",
    ],
)
def test_visual_request_keeps_the_media_route(url: str) -> None:
    assert _should_read_media_first(url, include_visual=True)
