"""Rendered Bilibili space cards: one video, two links, one real title."""

from __future__ import annotations

from envoy_cli import collections


CARD = """
<a href="//www.bilibili.com/video/BV1ujNV6qEXg" class="bili-cover-card">
  <div class="bili-cover-card__thumbnail">
    <img alt="帮不帮？" src="//i2.hdslb.com/bfs/archive/a.jpg">
  </div>
  <div class="bili-cover-card__stats">167.5万 3992 00:22</div>
</a>
<a href="//www.bilibili.com/video/BV1ujNV6qEXg">帮不帮？</a>
<a href="//www.bilibili.com/video/BV1Sh411N7pH">
  <img alt="桌面改造计划" src="//i2.hdslb.com/bfs/archive/b.jpg">
  <span>14:09</span>
</a>
"""


def test_the_cover_alt_text_wins_over_the_stats_strip():
    rows = collections._bilibili_candidates(CARD, limit=10)
    titles = [row["title"] for row in rows]
    assert titles == ["帮不帮？", "桌面改造计划"]


def test_the_duration_comes_from_the_card_not_the_title():
    rows = collections._bilibili_candidates(CARD, limit=10)
    assert [row["duration_seconds"] for row in rows] == [22, 849]


def test_a_card_without_a_cover_falls_back_to_its_shortest_label():
    html = '<a href="//www.bilibili.com/video/BV1wd4y1H7RR">软 路 由</a>'
    rows = collections._bilibili_candidates(html, limit=10)
    assert rows[0]["title"] == "软 路 由"
    assert rows[0]["duration_seconds"] is None


def test_a_blocked_space_tells_the_user_how_to_sign_in(monkeypatch):
    monkeypatch.setattr(
        "envoy_cli.features.credential_handoff_enabled", lambda: False
    )
    hint = collections._session_required_hint(
        "bilibili", Exception("Request is blocked by server (412)"), ""
    )
    assert "envoy auth handoff on" in hint
    assert "envoy auth import" in hint
    assert "412" not in hint


def test_an_enabled_handoff_points_at_the_sign_in_page(monkeypatch):
    monkeypatch.setattr(
        "envoy_cli.features.credential_handoff_enabled", lambda: True
    )
    hint = collections._session_required_hint("bilibili", "352", "")
    assert "envoy_auth" in hint
    assert "envoy auth handoff on" not in hint


def test_a_plain_empty_space_gets_no_session_hint():
    assert collections._session_required_hint("bilibili", None, "<html></html>") == ""
    assert collections._session_required_hint("youtube", "412", "") == ""
