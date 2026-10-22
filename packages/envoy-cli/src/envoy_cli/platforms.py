from __future__ import annotations

import json
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse


_XHS_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}
_XHS_NOTE_RE = re.compile(r"^/(?:explore|discovery/item)/[^/?#]+", re.IGNORECASE)


def classify_public_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host in _XHS_HOSTS:
        if host.endswith("xhslink.com"):
            return "xiaohongshu_share"
        if _XHS_NOTE_RE.match(parsed.path):
            return "xiaohongshu_note"
    if host == "youtu.be" and path.strip("/"):
        return "video_page"
    if host in {"youtube.com", "www.youtube.com"}:
        if path in {"/watch", "/live"} or re.match(r"^/(?:shorts|embed)/[^/]+", path):
            return "video_page"
        return "platform_page"
    if host in {"bilibili.com", "www.bilibili.com"}:
        if re.match(r"^/(?:video|bangumi/play)/", path):
            return "video_page"
        return "platform_page"
    if host == "vimeo.com" and re.fullmatch(r"/\d+/?", path):
        return "video_page"
    if host in {"dailymotion.com", "www.dailymotion.com"}:
        if re.match(r"^/video/", path):
            return "video_page"
        return "platform_page"
    if host in {"instagram.com", "www.instagram.com"}:
        if re.match(r"^/(?:reel|reels|tv)/", path):
            return "video_page"
        return "platform_page"
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return "platform_page"
    if host in {"kuaishou.com", "www.kuaishou.com"}:
        if re.match(r"^/(?:short-video|video|photo)/", path):
            return "video_page"
        return "platform_page"
    return "generic"


def is_platform_page(value: str) -> bool:
    return classify_public_url(value) in {
        "video_page",
        "platform_page",
        "xiaohongshu_note",
        "xiaohongshu_share",
    }


class _XHSImageParser(HTMLParser):
    _excluded = ("avatar", "recommend", "related", "comment", "profile", "footer")
    _note = {"note-container", "note-detail", "note-detail-container", "note-images", "note-slider"}
    _gallery = {"note-images", "note-slider", "note-image", "carousel", "swiper", "album"}

    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.images: list[dict[str, str]] = []
        self._excluded_depth = 0
        self._note_depth = 0
        self._gallery_depth = 0
        self._stack: list[tuple[str, bool, bool, bool]] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_map = dict(attrs)
        identity = " ".join(
            str(attrs_map.get(key) or "")
            for key in ("class", "id", "data-testid", "data-e2e")
        ).lower()
        excluded = any(marker in identity for marker in self._excluded)
        note = bool(set(identity.split()) & self._note)
        gallery = bool(set(identity.split()) & self._gallery)
        is_void = tag.lower() in {
            "area", "base", "br", "col", "embed", "hr", "img",
            "input", "link", "meta", "param", "source", "track", "wbr",
        }
        if not is_void:
            self._stack.append((tag.lower(), excluded, note, gallery))
            self._excluded_depth += int(excluded)
            self._note_depth += int(note)
            self._gallery_depth += int(gallery)
        if (
            tag.lower() != "img" or excluded or self._excluded_depth
            or self._note_depth <= 0 or self._gallery_depth <= 0
        ):
            return
        url = next((
            str(attrs_map.get(key) or "").strip()
            for key in ("src", "data-src", "data-lazy-src", "data-origin-src")
            if str(attrs_map.get(key) or "").strip()
        ), "")
        if not url:
            return
        absolute = urljoin(self.source_url, url)
        if absolute not in {item["url"] for item in self.images}:
            self.images.append({
                "url": absolute,
                "alt": str(attrs_map.get("alt") or "").strip(),
            })

    def handle_endtag(self, tag: str):
        index = next(
            (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i][0] == tag.lower()),
            None,
        )
        if index is None:
            return
        for _, excluded, note, gallery in self._stack[index:]:
            self._excluded_depth -= int(excluded)
            self._note_depth -= int(note)
            self._gallery_depth -= int(gallery)
        del self._stack[index:]


def _bound_album(payload: dict, note_id: str) -> list | None:
    note_state = payload.get("note")
    if not isinstance(note_state, dict):
        return None
    detail_map = note_state.get("noteDetailMap")
    if isinstance(detail_map, dict):
        entry = detail_map.get(note_id)
        note = entry.get("note") if isinstance(entry, dict) else None
    else:
        note = note_state if note_state.get("noteId") == note_id else None
    if not isinstance(note, dict) or note.get("noteId", note_id) != note_id:
        return None
    album = note.get("imageList")
    return album if isinstance(album, list) else None


def extract_xhs_note_images(
    html: str,
    source_url: str,
) -> tuple[list[dict[str, str]], int | None]:
    images: list[dict[str, str]] = []
    total: int | None = None
    match = _XHS_NOTE_RE.match(urlparse(source_url).path)
    if classify_public_url(source_url) != "xiaohongshu_note" or match is None:
        return images, total
    note_id = match.group(0).rstrip("/").rsplit("/", 1)[-1]
    for script in re.findall(
        r"<script[^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        script = script.strip()
        assignment = re.match(r"^(?:window\.)?__INITIAL_STATE__\s*=\s*", script)
        if assignment:
            script = script[assignment.end():]
        try:
            payload, _ = json.JSONDecoder().raw_decode(script)
        except (TypeError, ValueError):
            continue
        album = _bound_album(payload, note_id) if isinstance(payload, dict) else None
        if album is None:
            continue
        total = len(album)
        for item in album:
            if not isinstance(item, dict):
                continue
            value = next((
                item[key] for key in ("urlDefault", "originUrl", "url", "imageUrl")
                if isinstance(item.get(key), str) and item[key].strip()
            ), "")
            absolute = urljoin(source_url, value.strip()) if value else ""
            if urlparse(absolute).scheme in {"http", "https"}:
                if absolute not in {image["url"] for image in images}:
                    images.append({"url": absolute, "alt": ""})
        # A source-bound album is authoritative; DOM carousels can contain clones.
        return images, total
    parser = _XHSImageParser(source_url)
    parser.feed(html)
    parser.close()
    for image in parser.images:
        if image["url"] not in {item["url"] for item in images}:
            images.append(image)
    return images, total
