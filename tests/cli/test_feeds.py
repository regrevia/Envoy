"""Offline tests for the RSS/Atom reader."""

from __future__ import annotations

from envoy_cli.feeds import FEED_MIN_CHARS, looks_like_feed_url, parse_feed

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Feed</title>
  <description>Notes about examples</description>
  <item>
    <title>First post</title>
    <link>https://example.com/first</link>
    <pubDate>Tue, 21 Oct 2026 08:00:00 +0000</pubDate>
    <description>&lt;p&gt;Body of the &lt;b&gt;first&lt;/b&gt; post.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Second post</title>
    <link>https://example.com/second</link>
    <description>Body of the second post.</description>
  </item>
</channel></rss>
"""

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Examples</title>
  <subtitle>Short notes</subtitle>
  <entry>
    <title>Atom one</title>
    <link rel="alternate" href="https://example.org/atom-one"/>
    <published>2026-10-20T10:00:00Z</published>
    <summary>Summary of atom one.</summary>
  </entry>
</feed>
"""


def test_looks_like_feed_url_matches_common_shapes():
    assert looks_like_feed_url("https://www.36kr.com/feed")
    assert looks_like_feed_url("https://example.com/index.xml")
    assert looks_like_feed_url("https://example.com/blog/rss/")
    assert looks_like_feed_url("https://example.com/atom")
    assert not looks_like_feed_url("https://example.com/blog/post-1")
    assert not looks_like_feed_url("https://example.com/?feed=rss")


def test_parse_rss_collects_items_and_strips_html():
    parsed = parse_feed(RSS_FIXTURE, source_url="https://example.com/feed")

    assert parsed is not None
    assert parsed["title"] == "Example Feed"
    assert parsed["items"] == 2
    assert "First post" in parsed["text"]
    assert "https://example.com/first" in parsed["text"]
    assert "Body of the first post." in parsed["text"]
    assert "<p>" not in parsed["text"]
    assert "2026-10-21" in parsed["text"]


def test_parse_atom_reads_subtitle_and_summary():
    parsed = parse_feed(ATOM_FIXTURE, source_url="https://example.org/feed")

    assert parsed is not None
    assert parsed["title"] == "Atom Examples"
    assert "Short notes" in parsed["text"]
    assert "https://example.org/atom-one" in parsed["text"]
    assert "Summary of atom one." in parsed["text"]


def test_parse_feed_rejects_html_and_broken_xml():
    assert parse_feed("<html><body>nope</body></html>", source_url="https://example.com/feed") is None
    assert parse_feed("<rss><channel>", source_url="https://example.com/feed") is None


def test_parse_feed_reports_no_items_for_empty_channel():
    empty = "<rss version='2.0'><channel><title>Empty</title></channel></rss>"

    parsed = parse_feed(empty, source_url="https://example.com/feed")

    assert parsed is not None
    assert parsed["items"] == 0
    assert len(parsed["text"]) < FEED_MIN_CHARS
