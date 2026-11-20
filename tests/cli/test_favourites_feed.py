"""A favourites feed is read past its first screen, and says so when it is private."""


from envoy_cli import collections


def _xhs_html(*notes: tuple[str, str]) -> str:
    cards = "".join(
        f'<section class="note-item"><a class="cover" href="/explore/{note_id}'
        f'?xsec_token=abc" title="{title}"><img src="x.jpg"></a></section>'
        for note_id, title in notes
    )
    return f'<div id="userPostedFeeds">{cards}</div>'


def test_a_xiaohongshu_profile_reads_every_note_card_it_shows():
    rows = collections._xhs_candidates(
        _xhs_html(
            ("65f1a2b3c4d5e6f7a8b9c001", "UI 动效的三个层次"),
            ("65f1a2b3c4d5e6f7a8b9c002", "做后端的第一年"),
        )
    )
    assert [row["source_url"] for row in rows] == [
        "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7a8b9c001",
        "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7a8b9c002",
    ]
    assert [row["title"] for row in rows] == ["UI 动效的三个层次", "做后端的第一年"]
    assert {row["platform"] for row in rows} == {"xiaohongshu"}
    assert {row["item_type"] for row in rows} == {"image_note"}
    assert all(row["candidate_id"].startswith("xhs-") for row in rows)


def test_a_note_link_that_repeats_is_counted_once():
    note = ("65f1a2b3c4d5e6f7a8b9c001", "UI 动效的三个层次")
    rows = collections._xhs_candidates(_xhs_html(note, note))
    assert len(rows) == 1


def test_a_feed_that_needs_paging_is_told_to_scroll_and_open_its_tab():
    douyin = collections._feed_scroll_settings("douyin")
    assert douyin["wheel"] is True
    assert douyin["scroll_rounds"] > 4
    assert "收藏" in douyin["click_labels"]

    xhs = collections._feed_scroll_settings("xiaohongshu")
    assert xhs["wheel"] is True
    assert xhs["click_labels"] is None

    # A space or a playlist is a plain list: no extra scrolling cost.
    assert collections._feed_scroll_settings("bilibili") == {}
    assert collections._feed_scroll_settings("youtube") == {}


def test_a_private_collection_says_the_session_is_the_reason(monkeypatch):
    from envoy_cli import features

    monkeypatch.setattr(features, "sign_in_enabled", lambda: True)
    hint = collections._session_required_hint(
        "douyin", None, "<body>请先登录后查看你的收藏</body>"
    )
    assert "signed-in session" in hint
    assert "collection" in hint


def test_a_public_space_keeps_its_own_wording(monkeypatch):
    from envoy_cli import features

    monkeypatch.setattr(features, "sign_in_enabled", lambda: True)
    hint = collections._session_required_hint("bilibili", None, "<body>登录后查看</body>")
    assert "space" in hint


def test_a_page_that_is_not_behind_a_wall_gets_no_hint():
    assert collections._session_required_hint("douyin", None, "<body>正常内容</body>") == ""


def test_discovery_asks_the_browser_to_page_the_feed(monkeypatch, tmp_path):
    from envoy_cli import browser, collections

    captured: dict = {}

    class _Page:
        url = "https://www.douyin.com/user/self?showTab=favorite_collection"
        html = (
            '<div id="usernewui"><a href="/video/7300000000000000001">UI 排版</a></div>'
        )
        text = ""

    def _read_page(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Page()

    monkeypatch.setattr(browser, "read_page", _read_page)
    monkeypatch.setattr(collections, "_collection_root", lambda: tmp_path)
    monkeypatch.setattr(collections, "proxy_plan", lambda: [None])

    class _BrokenYdl:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, *_args, **_kwargs):
            raise RuntimeError("no extractor")

    import envoy_cli.video as video

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (_BrokenYdl, None))

    result = collections.discover_profile(
        "https://www.douyin.com/user/self?showTab=favorite_collection", limit=5
    )
    assert captured["wheel"] is True
    assert captured["scroll_rounds"] > 4
    assert "收藏" in captured["click_labels"]
    assert result["candidates"], "the browser's own links must become candidates"
    assert result["candidates"][0]["title"] == "UI 排版"


def test_a_playlist_never_pays_the_scroll_cost(monkeypatch, tmp_path):
    from envoy_cli import browser, collections

    captured: dict = {}

    class _Page:
        url = "https://space.bilibili.com/1"
        html = '<a href="/video/BV1xx411c7mD">一个视频</a>'
        text = ""

    def _read_page(url, **kwargs):
        captured.update(kwargs)
        return _Page()

    monkeypatch.setattr(browser, "read_page", _read_page)
    monkeypatch.setattr(collections, "_collection_root", lambda: tmp_path)
    monkeypatch.setattr(collections, "proxy_plan", lambda: [None])

    class _BrokenYdl:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def extract_info(self, *_args, **_kwargs):
            raise RuntimeError("no extractor")

    import envoy_cli.video as video

    monkeypatch.setattr(video, "_load_yt_dlp", lambda: (_BrokenYdl, None))

    collections.discover_profile("https://space.bilibili.com/1", limit=5)
    assert "wheel" not in captured
    assert "click_labels" not in captured
