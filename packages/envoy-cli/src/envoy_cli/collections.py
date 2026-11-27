from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import storage
from .media import interprocess_file_lock
from .netpolicy import proxy_plan


class CollectionError(ValueError):
    pass


class CollectionRevisionConflict(CollectionError):
    pass


_PROFILE_WORKS_ERROR_MARKERS = ("服务异常，重新刷新拉取数据",)
# A marketplace answers a script-throttled or signed-out request with a block
# page whose wording is stable, and those words never appear in an item card:
# reading one as an empty favourites list would report "nothing saved" for a
# list that was never shown.
_MARKETPLACE_BLOCKED_MARKERS = (
    "非法访问",
    "访问受限",
    "访问异常",
    "操作频繁",
    "访问过于频繁",
    "安全验证",
    "人机验证",
    "滑块",
    "请稍后重试",
    "暂时无法展示",
    "页面加载失败",
    "亲，小二正忙",
    # A marketplace that renders its shell but never hydrates its list leaves
    # these strings where the cards should be, so an empty read is a failed
    # read rather than a profile with nothing in it.
    "网络不见了",
    "暂时没有推荐",
    "没有这个用户",
)


def _operation_fingerprint(**payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _DouyinCandidateParser(HTMLParser):
    _profile_markers = ("usernewui", "user-profile-main", "user-detail-main")
    _void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.rows: list[dict] = []
        self._anchor_depth = 0
        self._current: dict | None = None
        self._profile_depth = 0
        self._excluded_depth = 0
        self._stack: list[tuple[bool, bool]] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_map = dict(attrs)
        markers = " ".join(
            str(attrs_map.get(name) or "")
            for name in ("class", "id", "data-e2e", "data-testid")
        ).lower()
        excluded = tag.lower() not in self._void_tags and any(
            marker in markers for marker in (
                "recommend", "related", "avatar", "comment", "search", "hot-list",
            )
        )
        profile = any(marker in markers for marker in self._profile_markers)
        if tag.lower() not in self._void_tags:
            self._stack.append((profile, excluded))
        if profile:
            self._profile_depth += 1
        if excluded:
            self._excluded_depth += 1
        if tag.lower() == "a" and self._excluded_depth == 0 and self._profile_depth > 0:
            href = str(attrs_map.get("href") or "").strip()
            if re.search(r"/(?:video|note)/[A-Za-z0-9_-]+", href):
                self._anchor_depth += 1
                self._current = {"url": urljoin(self.source_url, href), "title": ""}

    def handle_data(self, data: str):
        if self._current is not None and self._anchor_depth:
            text = " ".join(str(data).split())
            if text:
                self._current["title"] = " ".join(
                    item for item in (self._current["title"], text) if item
                )[:1000]

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._current is not None and self._anchor_depth:
            self.rows.append(self._current)
            self._current = None
            self._anchor_depth -= 1
        if self._stack and tag.lower() not in self._void_tags:
            was_profile, was_excluded = self._stack.pop()
            if was_profile:
                self._profile_depth = max(0, self._profile_depth - 1)
            if was_excluded:
                self._excluded_depth = max(0, self._excluded_depth - 1)


@dataclass(frozen=True)
class CollectionBudget:
    max_pages: int = 20
    max_scanned_candidates: int = 100
    max_items: int = 100
    max_download_bytes: int = 1_073_741_824
    max_response_chars: int = 1_000_000
    max_visual_inputs: int = 100
    max_visual_pixels: int = 100_000_000
    max_retries: int = 100
    max_work_seconds: int = 86_400

    def can_add(self, current: dict, **usage: int) -> bool:
        limits = {
            "scanned_pages": self.max_pages,
            "scanned_candidates": self.max_scanned_candidates,
            "selected_items": self.max_items,
            "downloaded_bytes": self.max_download_bytes,
            "returned_chars": self.max_response_chars,
            "visual_inputs": self.max_visual_inputs,
            "visual_pixels": self.max_visual_pixels,
            "retries": self.max_retries,
            "work_seconds": self.max_work_seconds,
        }
        return all(
            int(current.get(field, 0)) + int(amount) <= limits[field]
            for field, amount in usage.items()
            if field in limits
        )


def _collection_root() -> Path:
    return storage.resolve_material_root()


def _safe_identity(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise CollectionError(f"Invalid {name}")
    return value


def _manifest_path(collection_id: str, run_id: str) -> Path:
    identity = f"{_safe_identity(collection_id, 'collection_id')}\0{_safe_identity(run_id, 'run_id')}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return _collection_root() / f"collection-{digest}.json"


def _locked(path: Path):
    return interprocess_file_lock(
        path.with_suffix(".lock"),
        max_wait_seconds=30,
        busy_message="Collection checkpoint is busy; retry the operation",
    )


def _read(path: Path) -> dict | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".collection-", suffix=".tmp", dir=path.parent)
    try:
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _bilibili_part(query: str) -> str:
    """The `?p=N` suffix for a multi-part Bilibili video, or "" for part one."""

    for key, value in parse_qsl(query, keep_blank_values=True):
        if key != "p" or not value.isdigit():
            continue
        number = int(value)
        if number > 1:
            return f"?p={number}"
    return ""


_GOOFISH_ITEM_ID = re.compile(r"^\d{6,24}$")
_TAOBAO_ITEM_ID = re.compile(r"^\d{6,24}$")
_JD_SKU = re.compile(r"^/(\d{5,20})(?:\.html)?$")
# A share token (m.tb.cn, u.jd.com, 3.cn) hides the landing page until it is
# opened, so these hosts name no item on their own.
_GOOFISH_SHORT_HOSTS = {"m.tb.cn", "e.tb.cn", "tb.cn", "www.tb.cn", "u.jd.com", "3.cn", "www.3.cn"}


def _query_id(query: str, key: str, pattern: re.Pattern[str]) -> str:
    for name, value in parse_qsl(query, keep_blank_values=True):
        if name == key and pattern.fullmatch(value or ""):
            return value
    return ""


def _shopping_listing(host: str, path: str, query: str) -> str:
    """Canonicalize one marketplace item URL, or return "" when it is not one.

    Item identity is the numeric id and nothing else: the share parameters
    (`spm`, `scm`, `ut_sk`, `share*`) change per share and must not split one
    item into several candidates.
    """
    if host in _GOOFISH_SHORT_HOSTS:
        raise CollectionError("A shared shopping link must be opened before it names an item")
    if host.endswith(".goofish.com") or host == "goofish.com":
        item_id = _query_id(query, "id", _GOOFISH_ITEM_ID)
        if item_id and path in {"/item", "/item.htm"}:
            return f"https://www.goofish.com/item?id={item_id}"
        return ""
    if host.endswith(".taobao.com") or host == "taobao.com":
        item_id = _query_id(query, "id", _TAOBAO_ITEM_ID)
        if item_id and path in {"/item.htm", "/item"}:
            return f"https://item.taobao.com/item.htm?id={item_id}"
        return ""
    if host.endswith(".tmall.com") or host == "tmall.com":
        item_id = _query_id(query, "id", _TAOBAO_ITEM_ID)
        if item_id and path in {"/item.htm", "/item"}:
            return f"https://detail.tmall.com/item.htm?id={item_id}"
        return ""
    if host.endswith(".jd.com") or host == "jd.com" or host.endswith(".jd.hk"):
        sku = _JD_SKU.fullmatch(path)
        if not sku and path.startswith("/product/"):
            sku = _JD_SKU.fullmatch(path[len("/product"):])
        if sku:
            return f"https://item.jd.com/{sku.group(1)}.html"
        return ""
    return ""


def canonical_candidate_url(value: str) -> str:
    parts = urlsplit(str(value or "").strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"}:
        raise CollectionError("Only public post URLs can be canonicalized")
    path = parts.path.rstrip("/")
    if host in {"douyin.com", "www.douyin.com", "v.douyin.com"}:
        match = re.fullmatch(r"/(video|note)/([A-Za-z0-9_-]+)", path)
        if not match:
            raise CollectionError("URL does not identify a canonical Douyin video or note")
        kind, post_id = match.groups()
        return f"https://www.douyin.com/{kind}/{post_id}"
    if host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]{10}|av\d+)", path)
        if not match:
            raise CollectionError("URL does not identify a canonical Bilibili video")
        # A multi-part video (?p=N) is a separate episode of the same BV id:
        # dropping the part would collapse every part into one candidate.
        part = _bilibili_part(parts.query)
        return f"https://www.bilibili.com/video/{match.group(1)}{part}"
    if host in {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "xhslink.cn"}:
        if host.startswith("xhslink"):
            # A share link hides the note id behind a redirect, so it has to be
            # opened before it can name a note.
            raise CollectionError("A shared Xiaohongshu link must be opened before it names a note")
        match = re.fullmatch(r"/(?:explore|discovery/item)/([0-9a-zA-Z]{16,32})", path)
        if not match:
            raise CollectionError("URL does not identify a canonical Xiaohongshu note")
        return f"https://www.xiaohongshu.com/explore/{match.group(1)}"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path != "/watch":
            raise CollectionError("URL does not identify a canonical YouTube video")
        video_id = next(
            (
                value
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key == "v" and re.fullmatch(r"[A-Za-z0-9_-]{11}", value)
            ),
            "",
        )
        if not video_id:
            raise CollectionError("URL does not identify a canonical YouTube video")
        # A playlist position (index=) and a share token (si=) are not part of
        # the video's identity, so they are dropped here.
        return f"https://www.youtube.com/watch?v={video_id}"
    if host == "youtu.be":
        video_id = path.lstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise CollectionError("URL does not identify a canonical YouTube video")
        return f"https://www.youtube.com/watch?v={video_id}"
    listing = _shopping_listing(host, path, parts.query)
    if listing:
        return listing
    raise CollectionError(
        "Only public Douyin, Xiaohongshu, Bilibili, YouTube, Xianyu, Taobao or JD "
        "post URLs can be canonicalized"
    )


def candidate_id(source_url: str) -> str:
    canonical = canonical_candidate_url(source_url)
    if "bilibili.com" in canonical:
        prefix = "bili-"
    elif "xiaohongshu.com" in canonical:
        prefix = "xhs-"
    elif "youtube.com" in canonical:
        prefix = "yt-"
    elif "goofish.com" in canonical:
        prefix = "goofish-"
    elif "taobao.com" in canonical or "tmall.com" in canonical:
        prefix = "taobao-"
    elif "jd.com" in canonical or "jd.hk" in canonical:
        prefix = "jd-"
    else:
        prefix = "douyin-"
    return prefix + hashlib.sha256(canonical.encode()).hexdigest()[:24]


_SESSION_BLOCKED_MARKERS = (
    "412",
    "352",
    "blocked by server",
    "rejected by server",
    "sign in",
    "登录",
    "captcha",
    "risk control",
)


def _session_required_hint(platform: str, extractor_error: object, html: str) -> str:
    """Say what the user can do when a profile only answers a signed-in session.

    Only the server's own wording is inspected; no cookie value is read here, so
    the hint cannot echo credentials back into a message.
    """

    if platform not in {"bilibili", "douyin", "xiaohongshu", "goofish", "taobao", "jd"}:
        return ""
    text = f"{extractor_error or ''} {(html or '')[:4000]}".lower()
    if not any(marker in text for marker in _SESSION_BLOCKED_MARKERS):
        return ""
    from . import features

    # A favourites list is private by design, so an empty one is normal until
    # the session is there; a public space may simply be empty.
    if platform in {"douyin", "xiaohongshu", "goofish", "taobao", "jd"}:
        target = "collection"
    else:
        target = "space"
    if features.sign_in_enabled():
        return (
            f". This {target} only answers a signed-in session: open its sign-in page with "
            "envoy_auth, then retry after the user finishes it."
        )
    if features.credential_handoff_enabled():
        return (
            f". This {target} only answers a signed-in session: import the browser that holds "
            "one with envoy_auth import, then retry."
        )
    return (
        f". This {target} only answers a signed-in session: the user can turn the in-chat "
        "sign-in back on with `envoy auth handoff on`, then retry."
    )


def _shopping_profile(host: str, path: str, query: str) -> tuple[str, str] | None:
    """Canonicalize one marketplace favourites or seller page, or return None.

    Every marketplace keeps its own tab vocabulary, so the tab is kept as a
    query the marketplace itself understands rather than translated between
    them: a favourites tab means a favourites list on Xianyu and on Taobao
    alike, and dropping it would silently read the seller's listings instead.
    """
    if host in _GOOFISH_SHORT_HOSTS:
        raise CollectionError("A shared shopping link must be opened before it names a profile")
    # The caller strips the trailing slash, so a bare host arrives as an empty
    # path; both spellings mean the same tab and must not split the canonical URL.
    if not path:
        path = "/"
    if host.endswith(".goofish.com") or host == "goofish.com":
        user = _query_id(query, "userId", re.compile(r"^\d{4,24}$"))
        if user and path in {"/personal", "/user"}:
            return f"https://www.goofish.com/personal?userId={user}", "goofish"
        return None
    if host.endswith(".taobao.com") or host == "taobao.com" or host.endswith(".tmall.com") or host == "tmall.com":
        if path in {"/", "/favorite", "/favorites", "/favor"}:
            # The favourites list is the whole point of this link, so it stays
            # the canonical URL even without a session: the read then answers
            # with the sign-in handoff instead of a portal page.
            return "https://favorites.taobao.com/favorite", "taobao"
        return None
    if host.endswith(".jd.com") or host == "jd.com" or host.endswith(".jd.hk"):
        if path in {"/", "/favorite", "/favorites", "/myfavorite"}:
            return "https://favorites.jd.com/", "jd"
        return None
    return None


def canonical_profile_url(value: str) -> tuple[str, str]:
    """Return the canonical profile URL and the platform that serves it."""
    parts = urlsplit(str(value or "").strip())
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    if (
        parts.scheme not in {"http", "https"}
        or parts.username
        or parts.password
    ):
        raise CollectionError("A public profile URL is required")
    if host in {"douyin.com", "www.douyin.com"}:
        if not re.fullmatch(r"/user/[^/]+", path):
            raise CollectionError("A public Douyin user profile URL is required")
        # A profile tab is chosen by query, not by path: `/user/self` alone is
        # the works list, and the favourites live behind `showTab=`. Keeping the
        # tab is what makes the link mean "my collection" and not "my posts".
        tab = next(
            (
                value
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key == "showTab" and re.fullmatch(r"[A-Za-z_]{1,40}", value or "")
            ),
            "",
        )
        canonical = f"https://www.douyin.com{path}"
        return (f"{canonical}?showTab={tab}" if tab else canonical), "douyin"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and re.fullmatch(
        r"/playlist", path
    ):
        playlist_id = next(
            (
                value
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key == "list"
            ),
            "",
        )
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", playlist_id):
            raise CollectionError(
                "A YouTube playlist link needs its list id, for example "
                "/playlist?list=PL..."
            )
        return f"https://www.youtube.com/playlist?list={playlist_id}", "youtube"
    if host in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        profile = re.fullmatch(r"/user/profile/([0-9a-zA-Z]{5,40})", path)
        if not profile:
            raise CollectionError("A public Xiaohongshu profile URL is required")
        return f"https://www.xiaohongshu.com/user/profile/{profile.group(1)}", "xiaohongshu"
    if host == "space.bilibili.com":
        collection = re.fullmatch(
            r"/(\d{1,20})/channel/(collectiondetail|seriesdetail)", path
        )
        if collection:
            # A collection (合集) or series (系列) holds an explicit set of videos;
            # yt-dlp extracts both, so keep the id that selects it.
            sid = next(
                (
                    value
                    for key, value in parse_qsl(parts.query, keep_blank_values=True)
                    if key == "sid" and value.isdigit()
                ),
                "",
            )
            if not sid:
                raise CollectionError(
                    "A Bilibili collection link needs its sid, for example "
                    "/channel/collectiondetail?sid=57445"
                )
            mid, kind = collection.groups()
            return f"https://space.bilibili.com/{mid}/channel/{kind}?sid={sid}", "bilibili"
        match = re.fullmatch(r"/(\d{1,20})(?:/video)?", path)
        if not match:
            raise CollectionError("A public Bilibili space URL is required")
        return f"https://space.bilibili.com/{match.group(1)}/video", "bilibili"
    shopping = _shopping_profile(host, path, parts.query)
    if shopping:
        return shopping
    raise CollectionError(
        "A public Douyin, Xiaohongshu, Bilibili, YouTube, Xianyu, Taobao or JD "
        "favourites or profile URL is required"
    )


_BILIBILI_LINK = re.compile(
    r'<a[^>]+href="[^"]*?/video/(BV[A-Za-z0-9]{10})[^"]*"[^>]*>(.*?)</a>',
    re.S,
)
_HTML_TAG = re.compile(r"<[^>]+>")


_BILIBILI_ALT = re.compile(r"""alt\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_BILIBILI_DURATION = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")


def _bilibili_card_text(inner: str) -> str:
    return " ".join(_HTML_TAG.sub(" ", inner).split())


def _bilibili_card_title(inners: list[str]) -> str:
    """Prefer the cover alt text, then the shortest non-empty label.

    A space card links the same video twice: once through the cover (whose alt
    text is the title) and once through the title link. Keeping the first match
    used to concatenate the stats strip and the duration into the title.
    """

    for inner in inners:
        for alt in _BILIBILI_ALT.findall(inner):
            text = " ".join(alt.split())
            if text:
                return text[:200]
    labels = [t for t in (_bilibili_card_text(inner) for inner in inners) if t]
    if not labels:
        return ""
    for label in labels:
        compact = label.replace(" ", "")
        if compact and sum(ch.isdigit() for ch in compact) / len(compact) < 0.4:
            return label[:200]
    return labels[0][:200]


def _bilibili_card_duration(inners: list[str]) -> int | None:
    for inner in inners:
        for match in _BILIBILI_DURATION.finditer(_bilibili_card_text(inner)):
            hours, minutes, seconds = match.groups()
            if seconds is None:
                return int(hours) * 60 + int(minutes)
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return None


# A feed keeps its cards out of reach until the page advances, and the two
# supported feeds need different triggers: Douyin asks for a real wheel event
# and a click on the `收藏` tab, Xiaohongshu paginates on the wheel as well. The
# budget is rounds, not seconds, so a slow network lengthens the run instead of
# silently returning the first screen as the whole collection.
_FEED_SCROLL_ROUNDS = 12


def _feed_scroll_settings(platform: str) -> dict:
    if platform in {"douyin", "xiaohongshu"}:
        return {
            "scroll_rounds": _FEED_SCROLL_ROUNDS,
            "wheel": True,
            "click_labels": ("收藏", "收藏夹", "Collect") if platform == "douyin" else None,
        }
    if platform in {"goofish", "taobao", "jd"}:
        # A favourites list is a tab on the same profile, so the run starts by
        # clicking into it; the cards below it load on the wheel like any feed.
        return {
            "scroll_rounds": _FEED_SCROLL_ROUNDS,
            "wheel": True,
            "click_labels": ("收藏", "收藏夹", "我的收藏", "Favorites"),
        }
    return {}


_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.IGNORECASE)
_ANCHOR_HREF = re.compile(r"""href\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_SHOPPING_ITEM_LINKS = (
    ("goofish", re.compile(r"goofish\.com/item.*?\bid=(\d{6,24})")),
    ("taobao", re.compile(r"(?:item\.taobao\.com/item|detail\.tmall\.com/item).*?\bid=(\d{6,24})")),
    ("jd", re.compile(r"item\.jd\.(?:com|hk)/(\d{5,20})\.html")),
)
_SHOPPING_TITLE_ATTR = re.compile(
    r"""(?:title|aria-label|alt)\s*=\s*["']([^"']{1,200})["']""", re.IGNORECASE
)


def _shopping_candidates(html: str, *, limit: int = 100) -> list[dict]:
    """Read the item links a rendered marketplace page actually shows.

    The three marketplaces embed their cards differently but agree on one thing:
    each card is an anchor whose href names the item id. The id alone is enough
    to make a candidate, and the title comes from the anchor's own
    `title`/`aria-label`/`alt` or its inner text, so nothing is guessed from the
    surrounding markup.
    """
    document = str(html or "")
    cards: dict[tuple[str, str], list[str]] = {}
    for attributes, inner in _ANCHOR.findall(document):
        href = _ANCHOR_HREF.search(attributes)
        if not href:
            continue
        for platform, pattern in _SHOPPING_ITEM_LINKS:
            found = pattern.search(href.group(1))
            if found:
                cards.setdefault((platform, found.group(1)), []).append(
                    f"{attributes}>{inner}"
                )
                break
    rows: list[dict] = []
    for (platform, item_id), inners in cards.items():
        if platform == "goofish":
            url = f"https://www.goofish.com/item?id={item_id}"
        elif platform == "taobao":
            url = f"https://item.taobao.com/item.htm?id={item_id}"
        else:
            url = f"https://item.jd.com/{item_id}.html"
        title = ""
        for inner in inners:
            found = _SHOPPING_TITLE_ATTR.search(inner)
            if found:
                title = " ".join(_HTML_TAG.sub(" ", found.group(1)).split())[:200]
            if not title:
                title = " ".join(_HTML_TAG.sub(" ", inner.split(">", 1)[-1]).split())[:200]
            if title:
                break
        rows.append({"url": url, "title": title})
    return _candidate_records(rows, limit=limit)


_XHS_NOTE_LINK = re.compile(
    r"<a([^>]*?href=\"[^\"]*?/(?:explore|discovery/item)/([0-9a-zA-Z]{16,32})[^\"]*\"[^>]*)>"
    r"(.*?)</a>",
    re.S,
)
_XHS_TITLE_ATTR = re.compile(r"""(?:title|aria-label)\s*=\s*["\']([^"\']{1,200})["\']""", re.IGNORECASE)


def _xhs_candidates(html: str, *, limit: int = 100) -> list[dict]:
    """Read the note links a rendered Xiaohongshu profile actually shows."""

    cards: dict[str, list[str]] = {}
    # The title can live on the anchor itself or in the card it wraps, so both
    # halves of the match are searched rather than only the inner markup.
    for attributes, note_id, inner in _XHS_NOTE_LINK.findall(str(html or "")):
        cards.setdefault(note_id, []).append(f"{attributes}>{inner}")
    rows: list[dict] = []
    for note_id, inners in cards.items():
        title = ""
        for inner in inners:
            found = _XHS_TITLE_ATTR.search(inner)
            if found:
                title = " ".join(_HTML_TAG.sub(" ", found.group(1)).split())
                break
            text = " ".join(_HTML_TAG.sub(" ", inner).split())
            if text:
                title = text[:200]
                break
        rows.append({
            "url": f"https://www.xiaohongshu.com/explore/{note_id}",
            "title": title,
        })
    return _candidate_records(rows, limit=limit)


def _bilibili_candidates(html: str, *, limit: int = 100) -> list[dict]:
    """Read the video links a rendered Bilibili space actually shows."""
    cards: dict[str, list[str]] = {}
    for video_id, inner in _BILIBILI_LINK.findall(str(html or "")):
        cards.setdefault(video_id, []).append(inner)
    rows: list[dict] = []
    for video_id, inners in cards.items():
        rows.append({
            "url": f"https://www.bilibili.com/video/{video_id}",
            "title": _bilibili_card_title(inners),
            "duration": _bilibili_card_duration(inners),
        })
    return _candidate_records(rows, limit=limit)


def _candidate_records(entries: list[dict], *, offset: int = 0, limit: int = 100) -> list[dict]:
    candidates = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url") or entry.get("webpage_url") or "")
        item_id = str(entry.get("id") or "")
        if raw_url.startswith(("http://", "https://")):
            candidate_url = raw_url
        elif item_id and re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
            entry_type = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
            if "youtube" in entry_type and re.fullmatch(r"[A-Za-z0-9_-]{11}", item_id):
                candidate_url = f"https://www.youtube.com/watch?v={item_id}"
            elif "bilibili" in entry_type:
                candidate_url = f"https://www.bilibili.com/video/{item_id}"
            else:
                kind = "note" if "note" in entry_type else "video"
                candidate_url = f"https://www.douyin.com/{kind}/{item_id}"
        else:
            continue
        try:
            canonical = canonical_candidate_url(candidate_url)
        except CollectionError:
            continue
        stable_id = candidate_id(canonical)
        if stable_id in seen:
            continue
        seen.add(stable_id)
        path_kind = urlsplit(canonical).path.split("/")[1]
        candidates.append({
            "candidate_id": stable_id,
            "source_url": canonical,
            "platform": (
                "bilibili"
                if "bilibili.com" in canonical
                else "youtube"
                if "youtube.com" in canonical
                else "xiaohongshu"
                if "xiaohongshu.com" in canonical
                else "goofish"
                if "goofish.com" in canonical
                else "taobao"
                if "taobao.com" in canonical or "tmall.com" in canonical
                else "jd"
                if "jd.com" in canonical or "jd.hk" in canonical
                else "douyin"
            ),
            "item_type": (
                "image_note"
                if path_kind == "note" or "xiaohongshu.com" in canonical
                else "listing"
                if "goofish.com" in canonical
                or "taobao.com" in canonical
                or "tmall.com" in canonical
                or "jd.com" in canonical
                or "jd.hk" in canonical
                else "video"
            ),
            "title": str(entry.get("title") or ""),
            "published_at": entry.get("upload_date"),
            "duration_seconds": entry.get("duration"),
            "creator": str(entry.get("uploader") or entry.get("channel") or ""),
            "position": offset + len(candidates),
        })
        if len(candidates) >= limit:
            break
    return candidates


def discover_profile(
    profile_url: str,
    *,
    limit: int = 20,
    cursor: str | None = None,
    max_work_seconds: int = 60,
) -> dict:
    from .media import MediaUnsafeAddress, guard_yt_dlp_client, public_network_guard
    from .video import _load_yt_dlp

    if not 1 <= int(limit) <= 100:
        raise CollectionError("limit must be between 1 and 100")
    canonical_profile, platform = canonical_profile_url(profile_url)
    revision = ""
    offset = 0
    if cursor:
        match = re.fullmatch(r"([a-f0-9]{20}):(\d{1,3})", cursor)
        if not match:
            raise CollectionError("Invalid discovery cursor")
        revision = match.group(1)
        offset = int(match.group(2))
        snapshot_path = _collection_root() / f"discovery-{revision}.json"
        snapshot = _read(snapshot_path)
        if (
            snapshot is None
            or snapshot.get("profile_url") != canonical_profile
            or float(snapshot.get("expires_at") or 0) <= time.time()
        ):
            raise CollectionError("Discovery snapshot expired; start discovery again")
        all_candidates = list(snapshot.get("candidates") or [])
        page = all_candidates[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(all_candidates)
        return {
            "profile_url": canonical_profile,
            "platform": snapshot.get("platform", "douyin"),
            "discovery_revision": revision,
            "cursor_mode": "persisted_snapshot",
            "ordering": snapshot.get("ordering", "source"),
            "candidates": page,
            "has_more": True if has_more else snapshot.get("has_more"),
            "next_cursor": f"{revision}:{next_offset}" if has_more else None,
            "stop_reason": "page_limit" if has_more else snapshot.get("stop_reason", "source_end_unknown"),
            "discovered_at": snapshot.get("discovered_at"),
        }
    if platform in {"goofish", "taobao", "jd"}:
        from . import browser
        from .reader import _is_public_http_url

        # No yt-dlp extractor serves a marketplace, so the gate below would fail
        # on an installed binary and blame the user for a missing dependency.
        # The isolated browser is the only path here: the cards are rendered from
        # an authenticated XHR, which is also why an empty list is reported as a
        # possible missing session rather than an empty favourites list.
        try:
            page = browser.read_page(
                canonical_profile,
                max_work_seconds=max(int(max_work_seconds), 1),
                **_feed_scroll_settings(platform),
            )
        except Exception as exc:
            raise CollectionError(
                f"isolated-browser discovery failed ({type(exc).__name__}): {str(exc)[:240]}"
            ) from exc
        if not _is_public_http_url(page.url, {}):
            raise CollectionError(
                "The isolated browser resolved the marketplace page to a non-public address"
            )
        page_html = str(getattr(page, "html", "") or "")
        page_text = str(getattr(page, "text", "") or "")
        if any(marker in page_text for marker in _MARKETPLACE_BLOCKED_MARKERS):
            raise CollectionError(
                "The marketplace served a block page instead of the requested list; "
                "no candidate list is trusted"
                + _session_required_hint(platform, None, page_text)
            )
        candidates = _shopping_candidates(page_html, limit=100)
        cursor_mode = "isolated_browser_bounded_snapshot"
        if not candidates:
            raise CollectionError(
                "No public candidates were discovered: the marketplace exposed no item "
                "links, which is what a signed-out favourites tab renders"
                + _session_required_hint(platform, None, page_html + page_text)
            )
        revision = uuid.uuid4().hex[:20]
        discovered_at = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "profile_url": canonical_profile,
            "platform": platform,
            "discovery_revision": revision,
            "cursor_mode": cursor_mode,
            "ordering": "source",
            "candidates": candidates,
            "has_more": None,
            "stop_reason": (
                "hard_candidate_limit" if len(candidates) >= 100 else "source_end_unknown"
            ),
            "discovered_at": discovered_at,
            "expires_at": time.time() + 7 * 24 * 60 * 60,
        }
        snapshot_path = _collection_root() / f"discovery-{revision}.json"
        with _locked(snapshot_path):
            _write(snapshot_path, snapshot)
        page_candidates = candidates[:limit]
        next_offset = offset + len(page_candidates)
        has_more = len(candidates) > limit
        return {
            "profile_url": canonical_profile,
            "platform": platform,
            "discovery_revision": revision,
            "cursor_mode": cursor_mode,
            "ordering": "source",
            "candidates": page_candidates,
            "has_more": has_more,
            "next_cursor": f"{revision}:{next_offset}" if has_more else None,
            "stop_reason": "page_limit" if has_more else snapshot["stop_reason"],
            "discovered_at": discovered_at,
        }
    youtube_dl, _extractors = _load_yt_dlp()
    if youtube_dl is None:
        raise CollectionError("yt-dlp is not installed")
    extractor_error: Exception | None = None
    options = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 100,
        "noplaylist": False,
        "socket_timeout": min(max(int(max_work_seconds), 1), 10),
        "retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
        "playlist_items": "1-100",
    }
    info = None
    for proxy in proxy_plan():
        options["proxy"] = proxy
        try:
            with public_network_guard():
                with youtube_dl(options) as ydl:
                    guard_yt_dlp_client(ydl)
                    info = ydl.extract_info(canonical_profile, download=False)
        except MediaUnsafeAddress as exc:
            raise CollectionError(
                "Profile discovery resolved a non-public network address"
            ) from exc
        except Exception as exc:
            extractor_error = exc
            info = None
            continue
        if isinstance(info, dict):
            break
    entries = info.get("entries") if isinstance(info, dict) else None
    cursor_mode = "yt_dlp_flat_metadata"
    if isinstance(entries, (list, tuple)):
        candidates = _candidate_records(list(entries), limit=100)
    elif platform == "bilibili":
        from . import browser
        from .reader import _is_public_http_url

        try:
            page = browser.read_page(
                canonical_profile,
                max_work_seconds=max(int(max_work_seconds), 1),
                **_feed_scroll_settings(platform),
            )
        except Exception as exc:
            detail = (
                f"yt-dlp space enumeration unavailable ({type(extractor_error).__name__}: "
                f"{str(extractor_error)[:160]})"
                if extractor_error
                else "yt-dlp did not expose a Bilibili space list"
            )
            raise CollectionError(
                f"{detail}; isolated-browser discovery failed "
                f"({type(exc).__name__}): {str(exc)[:240]}"
            ) from exc
        if not _is_public_http_url(page.url, {}):
            raise CollectionError("The isolated browser resolved the space to a non-public address")
        candidates = _bilibili_candidates(str(getattr(page, "html", "") or ""), limit=100)
        cursor_mode = "isolated_browser_bounded_snapshot"
        if not candidates:
            raise CollectionError(
                "No public profile candidates were discovered in the Bilibili space"
                + _session_required_hint(
                    platform, extractor_error, str(getattr(page, "html", "") or "")
                )
            )
    else:
        from . import browser
        from .reader import _is_public_http_url

        try:
            page = browser.read_page(
                canonical_profile,
                max_work_seconds=max(int(max_work_seconds), 1),
                **_feed_scroll_settings(platform),
            )
        except Exception as exc:
            detail = (
                f"yt-dlp profile enumeration unavailable ({type(extractor_error).__name__})"
                if extractor_error
                else "yt-dlp did not expose a profile candidate list"
            )
            raise CollectionError(
                f"{detail}; isolated-browser discovery failed ({type(exc).__name__}): {str(exc)[:240]}"
            ) from exc
        if not _is_public_http_url(page.url, {}):
            raise CollectionError("The isolated browser resolved the profile to a non-public address")
        page_text = str(getattr(page, "text", "") or "")
        if any(marker in page_text for marker in _PROFILE_WORKS_ERROR_MARKERS):
            raise CollectionError(
                "Douyin profile works list reported a service error; no candidate list is trusted"
            )
        if platform == "xiaohongshu":
            candidates = _xhs_candidates(str(getattr(page, "html", "") or ""), limit=100)
        else:
            parser = _DouyinCandidateParser(page.url)
            parser.feed(page.html)
            parser.close()
            candidates = _candidate_records(parser.rows, limit=100)
        cursor_mode = "isolated_browser_bounded_snapshot"
        if not candidates:
            detail = (
                f"yt-dlp could not enumerate this profile ({type(extractor_error).__name__})"
                if extractor_error
                else "the isolated browser exposed no canonical video/note links"
            )
            raise CollectionError(
                f"No public profile candidates were discovered: {detail}"
                + _session_required_hint(
                    platform, extractor_error, str(getattr(page, "html", "") or "")
                )
            )
    revision = uuid.uuid4().hex[:20]
    discovered_at = datetime.now(timezone.utc).isoformat()
    snapshot_has_more = None
    snapshot = {
        "profile_url": canonical_profile,
        "platform": platform,
        "discovery_revision": revision,
        "cursor_mode": cursor_mode,
        "ordering": "source",
        "candidates": candidates,
        "has_more": snapshot_has_more,
        "stop_reason": "hard_candidate_limit" if len(candidates) >= 100 else "source_end_unknown",
        "discovered_at": discovered_at,
        "expires_at": time.time() + 7 * 24 * 60 * 60,
    }
    snapshot_path = _collection_root() / f"discovery-{revision}.json"
    with _locked(snapshot_path):
        _write(snapshot_path, snapshot)
    page = candidates[:limit]
    next_offset = offset + len(page)
    has_more = len(candidates) > limit
    return {
        "profile_url": canonical_profile,
        "platform": platform,
        "discovery_revision": revision,
        "cursor_mode": cursor_mode,
        "ordering": "source",
        "candidates": page,
        "has_more": True if has_more else snapshot_has_more,
        "next_cursor": f"{revision}:{next_offset}" if has_more else None,
        "stop_reason": (
            "page_limit" if has_more
            else snapshot["stop_reason"]
        ),
        "discovered_at": discovered_at,
    }


def create_collection(
    collection_id: str,
    run_id: str,
    profile_url: str,
    *,
    operation_id: str,
    budget: CollectionBudget | None = None,
) -> dict:
    collection_id = _safe_identity(collection_id, "collection_id")
    run_id = _safe_identity(run_id, "run_id")
    operation_id = _safe_identity(operation_id, "operation_id")
    profile, platform = canonical_profile_url(profile_url)
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        existing = _read(path)
        if existing is not None:
            if operation_id in existing["applied_operations"]:
                return existing
            raise CollectionError("Collection run already exists; choose a new run_id")
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "collection_id": collection_id,
            "run_id": run_id,
            "profile_url": profile,
            "platform": platform,
            "discovery_revision": None,
            "selection_plan_revision": 0,
            "manifest_revision": 1,
            "budget": (budget or CollectionBudget()).__dict__,
            "cursor": None,
            "candidates": [],
            "selected_candidate_ids": [],
            "items": {},
            "usage": {
                "scanned_pages": 0,
                "scanned_candidates": 0,
                "selected_items": 0,
                "work_seconds": 0,
                "downloaded_bytes": 0,
                "returned_chars": 0,
                "visual_inputs": 0,
                "visual_pixels": 0,
                "retries": 0,
            },
            "evidence": {},
            "applied_operations": {operation_id: {"revision": 1}},
            "created_at": now,
            "updated_at": now,
        }
        _write(path, manifest)
        return manifest


def list_collections() -> list[dict]:
    """Every resumable run on this machine, newest first."""

    found: list[dict] = []
    try:
        paths = sorted(_collection_root().glob("collection-*.json"))
    except OSError:
        return found
    for path in paths:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        items = manifest.get("items") if isinstance(manifest.get("items"), dict) else {}
        selected = [str(value) for value in manifest.get("selected_candidate_ids") or []]
        states = [str((items.get(cid) or {}).get("state") or "selected") for cid in selected]
        found.append({
            "collection_id": manifest.get("collection_id"),
            "run_id": manifest.get("run_id"),
            "profile_url": manifest.get("profile_url"),
            "platform": manifest.get("platform"),
            "selected": len(selected),
            "completed": states.count("completed"),
            "blocked": states.count("blocked"),
            "failed": states.count("failed"),
            "partial": states.count("partial"),
            "in_flight": states.count("processing"),
            "manifest_revision": manifest.get("manifest_revision"),
            "updated_at": max(
                (str((items.get(cid) or {}).get("updated_at") or "") for cid in selected),
                default=str(manifest.get("updated_at") or ""),
            ),
        })
    found.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return found


def renew_lease(collection_id: str, run_id: str, candidate_id: str) -> dict:
    """Extend the lease of one in-flight item without spending a retry."""

    manifest = get_collection(collection_id, run_id)
    return update_collection(
        collection_id,
        run_id=run_id,
        expected_revision=int(manifest["manifest_revision"]),
        operation_id=f"lease-{candidate_id}-{time.time_ns()}",
        action="touch_lease",
        candidate_id_value=candidate_id,
    )


def get_collection(collection_id: str, run_id: str) -> dict:
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        manifest = _read(path)
        if manifest is None:
            raise CollectionError("Collection run was not found")
        now = time.time()
        changed = False
        for candidate, item in manifest.get("items", {}).items():
            lease_expiry = item.get("lease_expires_at")
            if (
                item.get("state") == "processing"
                and lease_expiry is not None
                and float(lease_expiry) <= now
            ):
                item["state"] = "selected"
                item["retryable"] = True
                item["lease_expires_at"] = None
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                operation_key = f"lease-reap:{candidate}:{item.get('attempts', 0)}"
                manifest.setdefault("applied_operations", {})[operation_key] = {
                    "action": "lease_reap",
                    "candidate_id": candidate,
                }
                changed = True
        for evidence in manifest.get("evidence", {}).values():
            if (
                evidence.get("availability") == "available"
                and evidence.get("expires_at") is not None
                and float(evidence["expires_at"]) <= now
            ):
                evidence["availability"] = "expired"
                changed = True
        if changed:
            manifest["manifest_revision"] += 1
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write(path, manifest)
        return manifest


def update_collection(
    collection_id: str,
    *,
    run_id: str,
    expected_revision: int,
    operation_id: str,
    action: str,
    candidate_ids: list[str] | None = None,
    candidate_id_value: str | None = None,
    item_state: str | None = None,
    material_id: str | None = None,
    source_revision: str | None = None,
    expires_at: float | None = None,
    evidence_available: bool | None = None,
    pending_resource_ids: list[str] | None = None,
    observed_resource_ids: list[str] | None = None,
    usage: dict[str, int] | None = None,
    budget: CollectionBudget | None = None,
    topic: dict | None = None,
) -> dict:
    allowed_actions = {
        "select", "item_state", "record_evidence", "record_usage", "set_cursor",
        "discovery_snapshot", "record_handoff", "record_observation", "touch_lease",
    }
    if action not in allowed_actions:
        raise CollectionError("Unsupported collection checkpoint action")
    operation_id = _safe_identity(operation_id, "operation_id")
    path = _manifest_path(collection_id, run_id)
    with _locked(path):
        manifest = _read(path)
        if manifest is None:
            raise CollectionError("Collection run was not found")
        applied = manifest["applied_operations"].get(operation_id)
        fingerprint = _operation_fingerprint(
            action=action,
            candidate_ids=candidate_ids,
            candidate_id_value=candidate_id_value,
            item_state=item_state,
            material_id=material_id,
            source_revision=source_revision,
            expires_at=expires_at,
            evidence_available=evidence_available,
            pending_resource_ids=pending_resource_ids,
            observed_resource_ids=observed_resource_ids,
            usage=usage,
            budget=(budget.__dict__ if budget is not None else None),
            topic=topic,
        )
        if applied is not None:
            if applied.get("fingerprint") != fingerprint:
                raise CollectionError("operation_id was already used for different checkpoint input")
            return manifest
        if int(manifest["manifest_revision"]) != int(expected_revision):
            raise CollectionRevisionConflict("manifest_revision changed; reload before updating")
        if action == "select":
            ids = list(dict.fromkeys(str(item) for item in (candidate_ids or [])))
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            if len(ids) > min(stored_budget.max_items, 100):
                raise CollectionError(
                    f"Selection exceeds the collection max_items budget ({stored_budget.max_items})"
                )
            known = {str(item.get("candidate_id")) for item in manifest["candidates"]}
            if any(item not in known for item in ids):
                raise CollectionError("Selection contains an unknown candidate_id")
            previous_candidates = set(manifest.get("selected_candidate_ids", []))
            manifest["selected_candidate_ids"] = ids
            # The goal that produced this selection is part of the record: a
            # later run must be able to tell why a post was left out.
            if topic is not None:
                manifest["topic"] = dict(topic)
            manifest["selection_plan_revision"] += 1
            newly_selected = len(set(ids) - previous_candidates)
            if not stored_budget.can_add(
                manifest["usage"],
                selected_items=newly_selected,
            ):
                raise CollectionError("Collection selected-item budget exhausted")
            manifest["usage"]["selected_items"] += newly_selected
        elif action == "discovery_snapshot":
            snapshot = usage or {}
            discovered = snapshot.get("snapshot")
            if not isinstance(discovered, dict):
                raise CollectionError("A discovery snapshot payload is required")
            revision = str(discovered.get("discovery_revision") or "")
            if not revision:
                raise CollectionError("A discovery_revision is required")
            if (
                manifest.get("discovery_revision")
                and manifest["discovery_revision"] != revision
            ):
                manifest["candidates"] = []
                manifest["selected_candidate_ids"] = []
                manifest["items"] = {}
                manifest["evidence"] = {}
                manifest["selection_plan_revision"] += 1
            known = {
                str(item.get("candidate_id"))
                for item in manifest["candidates"]
                if isinstance(item, dict)
            }
            new_candidates = [
                item for item in (discovered.get("candidates") or [])
                if isinstance(item, dict)
                and str(item.get("candidate_id")) not in known
            ]
            if len(manifest["candidates"]) + len(new_candidates) > 100:
                raise CollectionError("Collection snapshot cannot exceed 100 candidates")
            manifest["discovery_revision"] = revision
            manifest["cursor"] = discovered.get("next_cursor")
            manifest["candidates"].extend(new_candidates)
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            increments = {
                "scanned_pages": 1,
                "scanned_candidates": len(new_candidates),
            }
            if not stored_budget.can_add(manifest["usage"], **increments):
                raise CollectionError("Collection discovery budget exhausted")
            manifest["usage"]["scanned_pages"] += increments["scanned_pages"]
            manifest["usage"]["scanned_candidates"] += increments["scanned_candidates"]
        elif action == "item_state":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("Only selected candidates can change item state")
            state = str(item_state or "")
            if state not in {"selected", "processing", "completed", "partial", "blocked", "skipped", "failed"}:
                raise CollectionError("Invalid collection item state")
            previous = manifest["items"].get(candidate, {})
            attempts = int(previous.get("attempts", 0)) + (1 if state == "processing" else 0)
            if state == "processing" and attempts > 1:
                stored_budget = CollectionBudget(**manifest.get("budget", {}))
                if not stored_budget.can_add(manifest["usage"], retries=1):
                    raise CollectionError("Collection retry budget exhausted")
                manifest["usage"]["retries"] += 1
            manifest["items"][candidate] = {
                **previous,
                "state": state,
                "attempts": attempts,
                "retryable": False,
                "lease_expires_at": time.time() + 300 if state == "processing" else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "touch_lease":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            previous = manifest["items"].get(candidate) or {}
            if previous.get("state") != "processing":
                raise CollectionError("Only an in-flight candidate holds a lease")
            manifest["items"][candidate] = {
                **previous,
                "lease_expires_at": time.time() + 300,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_handoff":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("A handoff must belong to a selected candidate")
            previous = manifest["items"].get(candidate, {})
            resources = list(dict.fromkeys(
                str(item) for item in (pending_resource_ids or [])
            ))
            already_observed = list(dict.fromkeys(
                str(item) for item in (previous.get("observed_resource_ids") or [])
            ))
            manifest["items"][candidate] = {
                **previous,
                "state": "partial",
                "pending_resource_ids": [
                    item for item in resources if item not in already_observed
                ],
                "observed_resource_ids": already_observed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_observation":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("An observation must belong to a selected candidate")
            item = manifest["items"].get(candidate)
            if not item:
                raise CollectionError("The candidate has no checkpoint state")
            pending = set(item.get("pending_resource_ids") or [])
            previously_observed = set(item.get("observed_resource_ids") or [])
            incoming = set(str(value) for value in (observed_resource_ids or []))
            if not incoming or not incoming <= pending:
                raise CollectionError("Observations must reference pending resource IDs")
            item["observed_resource_ids"] = sorted(previously_observed | incoming)
            item["pending_resource_ids"] = sorted(pending - incoming)
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
        elif action == "record_evidence":
            candidate = _safe_identity(candidate_id_value or "", "candidate_id")
            if candidate not in manifest["selected_candidate_ids"]:
                raise CollectionError("Evidence must belong to a selected candidate")
            manifest["evidence"][candidate] = {
                "material_id": material_id,
                "source_revision": source_revision,
                "expires_at": expires_at,
                "availability": (
                    "available" if evidence_available is not False
                    and (expires_at is None or expires_at > time.time())
                    else "expired"
                ),
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }
        elif action == "record_usage":
            increments = usage or {}
            stored_budget = CollectionBudget(**manifest.get("budget", {}))
            limits = budget or stored_budget
            if not limits.can_add(manifest["usage"], **increments):
                raise CollectionError("Collection budget exhausted; resume from the saved cursor")
            for field, amount in increments.items():
                if field in manifest["usage"]:
                    manifest["usage"][field] += int(amount)
        elif action == "set_cursor":
            manifest["cursor"] = {
                "discovery_revision": manifest.get("discovery_revision"),
                "value": str(candidate_id_value) if candidate_id_value is not None else None,
            }
        manifest["manifest_revision"] += 1
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["applied_operations"][operation_id] = {
            "revision": manifest["manifest_revision"],
            "action": action,
            "fingerprint": fingerprint,
        }
        _write(path, manifest)
        return manifest
