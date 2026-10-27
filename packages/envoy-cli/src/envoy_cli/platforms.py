from __future__ import annotations

import json
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse


_XHS_HOSTS = {
    "xiaohongshu.com",
    "www.xiaohongshu.com",
    "xhslink.com",
    "www.xhslink.com",
    "xhslink.cn",
    "www.xhslink.cn",
}
_XHS_NOTE_RE = re.compile(r"^/(?:explore|discovery/item)/[^/?#]+", re.IGNORECASE)
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
_BILIBILI_SHARE_HOSTS = {"b23.tv", "www.b23.tv", "bili2233.cn", "www.bili2233.cn"}
_ZHIHU_HOSTS = {
    "zhihu.com",
    "www.zhihu.com",
    "zhuanlan.zhihu.com",
    "www.zhuanlan.zhihu.com",
}
_ZHIHU_ARTICLE_RE = re.compile(
    r"^/(?:question/\d+/answer/\d+|p/\d+)", re.IGNORECASE
)
_WECHAT_ARTICLE_HOSTS = {"mp.weixin.qq.com"}
_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "m.douyin.com", "iesdouyin.com"}
_DOUYIN_SHORT_HOSTS = {"v.douyin.com"}
_DOUYIN_VIDEO_RE = re.compile(r"^/(?:video|share/video)/\d+", re.IGNORECASE)
_WEIBO_HOSTS = {"weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn", "www.weibo.cn"}
_WEIBO_SHORT_HOSTS = {"t.cn"}
_WEIBO_TV_RE = re.compile(r"^/tv/show/", re.IGNORECASE)
_TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
_TIKTOK_VIDEO_RE = re.compile(r"^/@[^/]+/video/\d+", re.IGNORECASE)
_KUAISHOU_HOSTS = {
    "kuaishou.com",
    "www.kuaishou.com",
    "m.kuaishou.com",
    "k.kuaishou.com",
}
_KUAISHOU_VIDEO_RE = re.compile(r"^/(?:short-video|video|photo|fw/photo)/", re.IGNORECASE)
_WECHAT_CHANNELS_HOSTS = {"channels.weixin.qq.com"}
_XIAOYUZHOU_HOSTS = {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"}
_FEED_SUFFIXES = (".xml", ".rss", ".atom")


def classify_public_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host in _XHS_HOSTS:
        if host.endswith(("xhslink.com", "xhslink.cn")):
            return "xiaohongshu_share"
        if _XHS_NOTE_RE.match(parsed.path):
            return "xiaohongshu_note"
    if host == "youtu.be" and path.strip("/"):
        return "video_page"
    if host in {"youtube.com", "www.youtube.com"}:
        if path in {"/watch", "/live"} or re.match(r"^/(?:shorts|embed)/[^/]+", path):
            return "video_page"
        return "platform_page"
    if host in _BILIBILI_SHARE_HOSTS:
        return "bilibili_share"
    if host in _BILIBILI_HOSTS:
        if re.match(r"^/(?:video|bangumi/play)/", path):
            return "video_page"
        return "platform_page"
    if host in _ZHIHU_HOSTS:
        if _ZHIHU_ARTICLE_RE.match(parsed.path):
            return "article_page"
        return "platform_page"
    if host in _WECHAT_ARTICLE_HOSTS:
        return "article_page"
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
    if host in _KUAISHOU_HOSTS:
        if host == "k.kuaishou.com":
            return "platform_page"
        if _KUAISHOU_VIDEO_RE.match(path):
            return "video_page"
        return "platform_page"
    if host in _DOUYIN_SHORT_HOSTS:
        return "platform_page"
    if host in _DOUYIN_HOSTS:
        if _DOUYIN_VIDEO_RE.match(path):
            return "video_page"
        return "platform_page"
    if host in _WEIBO_SHORT_HOSTS:
        return "platform_page"
    if host in _WEIBO_HOSTS:
        if _WEIBO_TV_RE.match(path):
            return "video_page"
        return "platform_page"
    if host in _TIKTOK_SHORT_HOSTS:
        return "platform_page"
    if host in _TIKTOK_HOSTS:
        if _TIKTOK_VIDEO_RE.match(parsed.path):
            return "video_page"
        return "platform_page"
    if host in _WECHAT_CHANNELS_HOSTS:
        return "platform_page"
    if host in _XIAOYUZHOU_HOSTS:
        return "platform_page"
    if path.endswith(_FEED_SUFFIXES):
        return "platform_page"
    return "generic"


def is_platform_page(value: str) -> bool:
    return classify_public_url(value) in {
        "video_page",
        "platform_page",
        "article_page",
        "bilibili_share",
        "xiaohongshu_note",
        "xiaohongshu_share",
    }


# ---------------------------------------------------------------------------
# Read-support verdict
#
# ``classify_public_url`` answers "which reader path fits this URL". The verdict
# answers a different question: "will calling Envoy on this URL return readable
# material, or is it a known dead end / anonymous login wall?" Only
# evidence-backed dead ends are unsupported; everything else stays unknown so
# the model still tries Envoy first.
# ---------------------------------------------------------------------------

_DOUYIN_LIVE_HOSTS = {"live.douyin.com"}
_DOUYIN_LIVE_RE = re.compile(r"^/live(?:/|$)", re.IGNORECASE)
_DOUYIN_NOTE_RE = re.compile(r"^/note/", re.IGNORECASE)
_TME_PRIVATE_RE = re.compile(r"^/(?:\+|joinchat/|c/)", re.IGNORECASE)
_INSTAGRAM_ITEM_RE = re.compile(r"^/(?:p|reel|reels|tv)/[^/]+", re.IGNORECASE)
_WECHAT_HOME_RE = re.compile(r"^/?$")
_VIMEO_VIDEO_RE = re.compile(r"/\d+/?")


_UNSUPPORTED_HOSTS = {"linkedin.com", "apnews.com", "x.com", "twitter.com"}
_DOUYIN_READ_HOSTS = {"douyin.com", "m.douyin.com", "iesdouyin.com"}

# Hosts whose public pages Envoy has read on this machine, or that have an
# accepted row in docs/acceptance/platform-coverage-matrix-2026-10-21.md.
_SUPPORTED_HOSTS = {
    "bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "bili2233.cn",
    "v.douyin.com",
    "weibo.com",
    "m.weibo.cn",
    "weibo.cn",
    "t.cn",
    "tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "kuaishou.com",
    "m.kuaishou.com",
    "k.kuaishou.com",
    "channels.weixin.qq.com",
    "mp.weixin.qq.com",
    "xiaoyuzhoufm.com",
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "36kr.com",
    "m.36kr.com",
    "github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
    "developer.mozilla.org",
    "arxiv.org",
    "export.arxiv.org",
    # Promoted from the link-coverage corpus: each of these was read end to end
    # (>= 200 chars of body text) at least once by this build, so a reader that
    # has it can say "supported" instead of "unknown". See
    # docs/experiments/link-compatibility.md for the run that produced it.
    "sspai.com",
    "ifanr.com",
    "geekpark.net",
    "youtu.be",
    "docs.google.com",
    "docs.python.org",
    "ximalaya.com",
    "kubernetes.io",
    "doc.rust-lang.org",
    "stackoverflow.com",
    "pypi.org",
    "npmjs.com",
    "lobste.rs",
    "meta.discourse.org",
    "movie.douban.com",
    "douban.com",
    "gitlab.com",
    "oschina.net",
    "notion.so",
    "airtable.com",
    "w3.org",
    "rfc-editor.org",
    "gutenberg.org",
    "simonwillison.net",
    "danluu.com",
    # Endpoint-only platforms: the page itself is JS or a login wall, but a
    # documented public endpoint answers it (see fallbacks.SITE_BACKENDS).
    "news.ycombinator.com",
    "juejin.cn",
    "vimeo.com",
    # Hosts that answered with readable text in the round-3 link corpus while
    # the router still said "unknown"; accepted so it stops hedging on them.
    "pinterest.com",
    "figma.com",
    "api.github.com",
    # Round-4/5: both answered with full article text over static HTTP on the
    # first try (cnblogs 13k chars, douban book 6.7k), so stop hedging.
    "cnblogs.com",
    "book.douban.com",
    "ithome.com",
    "ifanr.com",
    "pingwest.com",
    # Round-13: these stayed `unknown` while their corpus rows returned article
    # text, so the router was hedging on links it can actually read.
    "theguardian.com",
    "youtube.com",
    "platformer.news",
    "feeds.arstechnica.com",
    "feeds.simplecast.com",
    "dev.to",
    "discourse.mozilla.org",
    "forum.arduino.cc",
    "youku.com",
    "v.youku.com",
    "framatube.org",
    "npr.org",
    "cnbc.com",
    "qbitai.com",
    "dev.to",
    "zenn.dev",
    "qiita.com",
    "lwn.net",
    "brendangregg.com",
    "techcrunch.com",
    "ted.com",
    "solidot.org",
    "thepaper.cn",
    "guancha.cn",
    "developer.mozilla.org",
    "docs.python.org",
    "jiemian.com",
    "cls.cn",
    "yicai.com",
    "caixin.com",
    "wallstreetcn.com",
    "ruanyifeng.com",
    "commons.wikimedia.org",
}
_SUPPORTED_HOST_SUFFIXES = (".wikipedia.org",)
_SUPPORTED_FILE_SUFFIXES = (
    ".pdf",
    ".mp3",
    ".m4a",
    ".m4b",
    ".aac",
    ".wav",
    ".ogg",
    ".oga",
    ".flac",
    ".opus",
    ".mp4",
    ".webm",
    ".ogv",
    ".mov",
    ".m4v",
    ".xml",
    ".rss",
    ".atom",
)


def read_support_verdict(value: str) -> str:
    """Whether Envoy is expected to read ``value``.

    Returns ``"supported"``, ``"unsupported"``, or ``"unknown"``. This is a
    static, offline judgement: it never resolves redirects or probes the
    network, so a short link keeps the verdict of its share family.
    """

    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    lowered_path = path.lower()
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        # mailto:, tel:, file:, ftp:, data: and app intents are not pages: no
        # amount of fetching turns them into text, so the host should never
        # spend a tool call on them.
        return "unsupported"
    if not host:
        return "unknown"
    if host.startswith("www."):
        host = host[4:]
    if host in _UNSUPPORTED_HOSTS:
        return "unsupported"
    if host in _DOUYIN_LIVE_HOSTS or (
        host in _DOUYIN_READ_HOSTS and _DOUYIN_LIVE_RE.match(path)
    ):
        return "unsupported"
    if host in {"t.me", "telegram.me"} and _TME_PRIVATE_RE.match(path):
        return "unsupported"
    if host == "instagram.com" and _INSTAGRAM_ITEM_RE.match(path):
        return "unsupported"
    if host in _DOUYIN_READ_HOSTS:
        if _DOUYIN_VIDEO_RE.match(path) or _DOUYIN_NOTE_RE.match(path):
            return "supported"
        return "unknown"
    if host == "mp.weixin.qq.com" and _WECHAT_HOME_RE.match(path):
        # The platform home page is a login shell for authors; article URLs
        # (/s/<id>) remain supported below.
        return "unsupported"
    if host == "vimeo.com":
        # Only /<video id> is answerable (oEmbed metadata); channels, albums
        # and showcases need the logged-in app shell.
        return "supported" if _VIMEO_VIDEO_RE.fullmatch(path) else "unknown"
    if host in _SUPPORTED_HOSTS or host.endswith(_SUPPORTED_HOST_SUFFIXES):
        return "supported"
    if lowered_path.endswith(_SUPPORTED_FILE_SUFFIXES):
        return "supported"
    return "unknown"


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
