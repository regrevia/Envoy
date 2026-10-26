"""Read RSS and Atom feeds as material instead of as rendered pages.

A feed URL returns XML, not HTML. The browser route turns that XML into a
handful of unrelated strings, while the feed itself carries titles, links,
dates and summaries in a fixed shape. Parsing the XML keeps the item list
intact and is far cheaper than launching a browser.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any

import httpx

FEED_MIN_CHARS = 200
MAX_FEED_ITEMS = 40
_MAX_FIELD_CHARS = 600
_MAX_BYTES = 4_000_000
_USER_AGENT = "envoy-cli-feed/0.1 (+https://github.com/omniasola/envoy)"


def _client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=True,
        max_redirects=3,
        trust_env=False,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"},
    )


def looks_like_feed_url(url: str) -> bool:
    path = url.split("://", 1)[-1].split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith((".rss", ".atom", ".xml")):
        return True
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return tail in {"feed", "rss", "atom", "feed.xml", "index.xml"}


def _local_name(tag: Any) -> str:
    text = str(tag or "")
    return text.rsplit("}", 1)[-1].lower()


def _clean(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_FIELD_CHARS]


def _child_text(node: ElementTree.Element, names: set[str]) -> str:
    for child in list(node):
        if _local_name(child.tag) in names:
            return _clean(child.text)
    return ""


def _entry_summary(node: ElementTree.Element) -> str:
    for child in list(node):
        name = _local_name(child.tag)
        if name in {"description", "summary", "content", "encoded"}:
            direct = _clean(child.text)
            if direct:
                return direct
            # Atom content may nest XHTML under a div.
            parts = [_clean(grand.text) for grand in child.iter() if _local_name(grand.tag) in {"p", "div", "li"}]
            joined = " ".join(part for part in parts if part)
            if joined:
                return joined[: _MAX_FIELD_CHARS]
    return ""


def _entry_link(node: ElementTree.Element) -> str:
    for child in list(node):
        if _local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        if href and str(child.attrib.get("rel") or "alternate") in {"alternate", ""}:
            return href
        if child.text and child.text.strip().startswith(("http://", "https://")):
            return child.text.strip()
    return _child_text(node, {"guid", "id"})


def _entry_date(node: ElementTree.Element) -> str:
    for name in ("pubdate", "published", "updated", "date"):
        value = _child_text(node, {name})
        if not value:
            continue
        try:
            return parsedate_to_datetime(value).isoformat()
        except (TypeError, ValueError):
            return value
    return ""


def parse_feed(xml_text: str, *, source_url: str) -> dict | None:
    """Turn feed XML into ``{"title", "text", "items"}`` or ``None``."""

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    root_name = _local_name(root.tag)
    if root_name not in {"rss", "feed", "rdf"}:
        return None
    channel = root
    for child in list(root):
        if _local_name(child.tag) in {"channel", "feed"}:
            channel = child
            break
    entries = [
        child
        for child in list(channel)
        if _local_name(child.tag) in {"item", "entry"}
    ]
    if not entries:
        entries = [child for child in root.iter() if _local_name(child.tag) in {"item", "entry"}]
    feed_title = _child_text(channel, {"title"}) or _child_text(root, {"title"}) or source_url
    feed_note = _child_text(channel, {"description", "subtitle", "tagline"})
    lines = [feed_title]
    if feed_note:
        lines.append(feed_note)
    for index, entry in enumerate(entries[:MAX_FEED_ITEMS], start=1):
        title = _child_text(entry, {"title"}) or "(untitled item)"
        link = _entry_link(entry)
        published = _entry_date(entry)
        summary = _entry_summary(entry)
        head = f"{index}. {title}"
        if published:
            head += f" ({published[:10]})"
        if link:
            head += f" {link}"
        lines.append(head)
        if summary:
            lines.append(f"   {summary}")
    text = "\n".join(lines).strip()
    return {
        "backend": "feed",
        "title": feed_title,
        "text": text,
        "url": source_url,
        "items": len(entries[:MAX_FEED_ITEMS]),
    }


def fetch_feed(url: str, *, max_work_seconds: int = 12) -> dict | None:
    """Read a feed URL; ``None`` when it is not a usable feed."""

    if not looks_like_feed_url(url):
        return None
    timeout = max(3.0, min(float(max_work_seconds), 20.0))
    try:
        with _client(timeout) as client:
            response = client.get(url)
    except (httpx.HTTPError, OSError, ValueError):
        return None
    if response.status_code != 200:
        return None
    body = response.content[:_MAX_BYTES]
    try:
        xml_text = body.decode(response.encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeError):
        xml_text = body.decode("utf-8", errors="replace")
    parsed = parse_feed(xml_text, source_url=url)
    if parsed is None:
        return None
    if len(parsed["text"]) < FEED_MIN_CHARS:
        return None
    return parsed
