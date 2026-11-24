"""Public-API fallbacks for pages whose HTML renders to an empty shell.

Each backend answers from a documented, unauthenticated public endpoint, so a
page that JavaScript renders client-side still yields real text. Only the exact
URL shapes in ``SITE_BACKENDS`` are handled; anything else returns ``None``.

The routes are one ordered table, not a chain of ``if`` checks: a URL is walked
down ``SITE_BACKENDS`` in order and the first pattern that matches decides the
backend. The pattern list is shared with the read-support verdict a host sees
before it reads, so the row that advertises a site and the row that answers it
cannot drift apart; adding a site is one row, no control flow.

A route that matches but fails does not collapse into the same ``None`` as a
route that never matched. :func:`public_api_fallback_outcome` reports it as a
:class:`FallbackFailure` carrying one code from the reader's closed
``ErrorCode`` set, whether retrying the same endpoint can help, and the
behavioural next step that already describes that code in
:mod:`envoy_cli.diagnosis`. :func:`public_api_fallback` stays the ``dict |
None`` seam the reader calls, and still never raises: the distinction exists so
the reason can be recorded, not so callers must handle an exception.
"""

from __future__ import annotations

import contextvars
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from envoy_core.schemas.read_v1 import ErrorCode

from . import diagnosis

SCHEMA = "envoy.public-api-fallback/1"

_MAX_TEXT = 60_000
_HTTP_TIMEOUT = 8.0
_MAX_REDIRECTS = 3
_USER_AGENT = "envoy-cli-public-api-fallback/0.1"
_BLOCK_TAGS = frozenset(
    {
        "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "hr", "li", "ol", "p", "pre", "table", "tr", "ul",
    }
)
_SKIP_TAGS = frozenset({"script", "style"})

_STACKOVERFLOW = re.compile(
    r"^https?://(?:www\.)?stackoverflow\.com/questions/(?P<id>\d+)(?:/[\w%.-]*)?/?(?:[?#].*)?$"
)
_NPM = re.compile(
    r"^(?:https?://(?:www\.)?npmjs\.com/package/|https?://registry\.npmjs\.org/)"
    r"(?P<name>(?:@[\w.-]+/)?[\w.-]+)(?:/v/[\w.-]+)?/?(?:[?#].*)?$"
)
_HACKER_NEWS = re.compile(
    r"^https?://news\.ycombinator\.com/item\?(?:[^#]*&)?id=(?P<id>\d+)(?:[&#].*)?$"
)
_BILIBILI = re.compile(
    r"^(?:https?://(?:www\.|m\.)?bilibili\.com/video/(?P<bvid>BV[0-9A-Za-z]{10})"
    r"(?:[/?#].*)?|https?://b23\.tv/(?P<short>[\w-]+)/?(?:[?#].*)?)$"
)
_DOUBAN = re.compile(
    r"^https?://movie\.douban\.com/subject/(?P<id>\d+)/?(?:[?#].*)?$"
)
_DOUBAN_REFERER = {"Referer": "https://m.douban.com/"}
_JUEJIN = re.compile(r"^https?://juejin\.cn/post/(?P<id>\d{19})(?:[/?#].*)?$")
_SSPAI = re.compile(r"^https?://sspai\.com/post/(?P<id>\d+)/?(?:[?#].*)?$")
_VIMEO = re.compile(r"^https?://(?:www\.)?vimeo\.com/(?P<id>\d+)(?:[/?#].*)?$")

# Every endpoint-only backend and one URL it must answer. The tuple is also the
# place to check parity: a backend here whose host is not marked supported in
# ``platforms.read_support_verdict`` is unreachable code, because the router
# never sends such a URL down the public-API path.
SITE_BACKENDS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("stackexchange", _STACKOVERFLOW, "https://stackoverflow.com/questions/111102"),
    ("npm", _NPM, "https://www.npmjs.com/package/httpx"),
    ("hackernews", _HACKER_NEWS, "https://news.ycombinator.com/item?id=1"),
    ("bilibili", _BILIBILI, "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("douban", _DOUBAN, "https://movie.douban.com/subject/1292052/"),
    ("juejin", _JUEJIN, "https://juejin.cn/post/7000000000000000000"),
    ("sspai", _SSPAI, "https://sspai.com/post/117529"),
    ("vimeo", _VIMEO, "https://vimeo.com/76979871"),
)

# A retry re-runs the same endpoint. Only a transport failure is worth that: an
# HTTP status or a body the endpoint did not fill is an answer, and asking again
# spends the read's remaining budget to hear the same answer.
_RETRYABLE_CODES = frozenset({ErrorCode.NETWORK_ERROR})


def _display_url(url: str) -> str:
    """The URL as it may be shown: credentials never leave this module."""

    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.username and not parts.password:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class FallbackFailure:
    """Why a route that matched produced no text.

    ``code`` is one of the reader's closed ``ErrorCode`` members, so a failure
    here is expressible anywhere else in the read path and needs no vocabulary
    of its own. The next step is not written here either: :func:`as_dict` asks
    :mod:`envoy_cli.diagnosis` what that code means, which is the same sentence
    the host agent already receives when the read itself fails that way.

    ``final`` means re-asking this endpoint cannot help. It is the whole point
    of not returning a bare ``None``: an agent that cannot tell "no route here"
    from "the route answered nothing" retries the wrong thing.
    """

    backend: str
    code: ErrorCode
    url: str
    detail: str = ""

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_CODES

    @property
    def final(self) -> bool:
        return not self.retryable

    def as_dict(self) -> dict[str, object]:
        """One envelope: the closed code, the diagnosis kind, the next step."""

        found = diagnosis.diagnose(self.code, message=self.detail)
        return {
            "schema_version": SCHEMA,
            "backend": self.backend,
            "url": _display_url(self.url),
            "code": self.code.value,
            "failure_kind": found.kind,
            "retryable": self.retryable,
            "final": self.final,
            "summary": found.summary,
            "next_step": found.next_step,
        }


def _classify(exc: BaseException) -> tuple[ErrorCode, str]:
    """The reader's own code for a way an endpoint can fail."""

    if isinstance(exc, httpx.TimeoutException):
        return ErrorCode.NETWORK_ERROR, "the endpoint did not answer in time"
    if isinstance(exc, _CONNECTION_ERRORS):
        return ErrorCode.NETWORK_ERROR, "the connection to the endpoint did not carry the request"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return ErrorCode.SOURCE_UNAVAILABLE, f"the endpoint answered {status}"
    if isinstance(exc, httpx.HTTPError):
        return ErrorCode.SOURCE_UNAVAILABLE, "the endpoint broke the exchange"
    if isinstance(exc, ValueError):
        return ErrorCode.SOURCE_UNAVAILABLE, "the endpoint did not answer with readable JSON"
    return ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"[:200]


class _PlainText(HTMLParser):
    """Turn a fragment of HTML into plain text, dropping scripts and styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def _plain(fragment: Any) -> str:
    if not isinstance(fragment, str) or not fragment.strip():
        return ""
    parser = _PlainText()
    parser.feed(fragment)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _client() -> httpx.Client:
    """One request-scoped client: no proxy env, no cookies, bounded redirects."""

    return httpx.Client(
        timeout=httpx.Timeout(_HTTP_TIMEOUT),
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        trust_env=False,
        headers={"User-Agent": _USER_AGENT},
    )


#: Only failures that prove the direct route did not carry the request are
#: retried through a proxy; an HTTP status is an answer from the source itself.
_CONNECTION_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    OSError,
)


def _proxy_url() -> str | None:
    """The proxy to retry through, or None when outbound policy says direct."""

    try:
        from .netpolicy import detected_proxy, explicit_proxy

        return explicit_proxy() or detected_proxy()
    except Exception:  # noqa: BLE001 — a proxy is optional, never a hard failure
        return None


def _send(
    client: httpx.Client, method: str, url: str, budget: float, **kwargs: Any
) -> httpx.Response:
    """Send one request directly, retrying once through a proxy on connect failure."""

    request = dict(kwargs)
    request["timeout"] = httpx.Timeout(min(_HTTP_TIMEOUT, budget))
    try:
        return client.request(method, url, **request)
    except _CONNECTION_ERRORS:
        proxy = _proxy_url()
        if not proxy:
            raise
        # The proxy URL may carry credentials, so it is never logged or returned.
        with _proxy_client(proxy) as proxied:
            return proxied.request(method, url, **request)


def _proxy_client(proxy: str) -> httpx.Client:
    """A throwaway client bound to one proxy, for the retry attempt only."""

    return httpx.Client(
        proxy=proxy,
        timeout=httpx.Timeout(_HTTP_TIMEOUT),
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        trust_env=False,
        headers={"User-Agent": _USER_AGENT},
    )


def _get_json(
    client: httpx.Client,
    url: str,
    budget: float,
    headers: dict[str, str] | None = None,
) -> Any:
    if budget <= 0:
        return None
    response = _send(client, "GET", url, budget, headers=headers)
    response.raise_for_status()
    return response.json()


def _seconds(seconds: Any) -> str:
    if not isinstance(seconds, int) or seconds < 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _resolve_b23(client: httpx.Client, code: str, budget: float) -> str | None:
    """Follow one b23.tv hop and return the final public video URL."""

    if budget <= 0:
        return None
    response = _send(
        client, "HEAD", f"https://b23.tv/{code}", budget, follow_redirects=False
    )
    location = response.headers.get("location")
    if not location:
        return None
    location = httpx.URL(location)
    if str(location).startswith("/"):
        return None
    return str(location)


def _result(
    backend: str, title: str, text: str, url: str, *, min_chars: int | None = None
) -> dict:
    result = {
        "backend": backend,
        "title": title.strip() or url,
        "text": text.strip()[:_MAX_TEXT],
        "url": url,
    }
    if min_chars is not None:
        result["min_chars"] = min_chars
    return result


def _stackexchange(client: httpx.Client, question_id: str, budget: float) -> dict | None:
    api = "https://api.stackexchange.com/2.3"
    site = "https://stackoverflow.com"
    question_payload = _get_json(
        client, f"{api}/questions/{question_id}?site=stackoverflow&filter=withbody", budget
    )
    items = (question_payload or {}).get("items") or []
    question = items[0] if items else None
    if not isinstance(question, dict):
        return None
    title = _plain(question.get("title")) or f"Stack Overflow question {question_id}"
    parts = [title, f"{site}/questions/{question_id}", "", _plain(question.get("body"))]
    answers_payload = _get_json(
        client,
        f"{api}/questions/{question_id}/answers?site=stackoverflow"
        "&sort=votes&order=desc&filter=withbody",
        budget,
    )
    answers = (answers_payload or {}).get("items") or []
    for index, answer in enumerate(answers[:3], start=1):
        if not isinstance(answer, dict):
            continue
        body = _plain(answer.get("body"))
        if not body:
            continue
        score = answer.get("score")
        label = f"Answer {index}" + (f" (score {score})" if isinstance(score, int) else "")
        if answer.get("is_accepted"):
            label += " — accepted"
        parts.extend(["", f"--- {label} ---", body])
    return _result(
        "stackexchange", title, "\n".join(parts), f"{site}/questions/{question_id}"
    )


def _npm(client: httpx.Client, name: str, budget: float) -> dict | None:
    payload = _get_json(client, f"https://registry.npmjs.org/{quote(name, safe='@/')}", budget)
    if not isinstance(payload, dict) or not payload.get("name"):
        return None
    name = str(payload["name"])
    title = f"{name} — npm"
    description = str(payload.get("description") or "").strip()
    versions = payload.get("dist-tags") or {}
    latest = versions.get("latest") if isinstance(versions, dict) else None
    tags = payload.get("time") or {}
    listing = [str(version) for version in (payload.get("versions") or {})][:10]
    parts = [title, f"https://www.npmjs.com/package/{name}"]
    if description:
        parts.extend(["", description])
    if latest:
        parts.append(f"latest: {latest}")
    if isinstance(tags, dict) and tags.get("modified"):
        parts.append(f"last published: {tags['modified']}")
    if listing:
        parts.extend(["", "versions: " + ", ".join(listing)])
    readme = _plain(payload.get("readme"))
    if readme:
        parts.extend(["", "--- README ---", readme])
    return _result("npm", title, "\n".join(parts), f"https://www.npmjs.com/package/{name}")


def _hacker_news(client: httpx.Client, item_id: str, budget: float) -> dict | None:
    api = "https://hacker-news.firebaseio.com/v0"
    item = _get_json(client, f"{api}/item/{item_id}.json", budget)
    if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
        return None
    title = _plain(item.get("title")) or f"Hacker News item {item_id}"
    parts = [title, f"https://news.ycombinator.com/item?id={item_id}"]
    if item.get("url"):
        parts.append(str(item["url"]))
    if isinstance(item.get("score"), int):
        parts.append(f"score: {item['score']}")
    if isinstance(item.get("by"), str):
        parts.append(f"by: {item['by']}")
    body = _plain(item.get("text"))
    if body:
        parts.extend(["", body])
    kids = item.get("kids") or []
    comments = 0
    for kid in kids:
        if comments >= 5:
            break
        if not isinstance(kid, int) or budget <= 0:
            continue
        try:
            comment = _get_json(client, f"{api}/item/{kid}.json", budget)
        except httpx.HTTPError:
            continue
        if not isinstance(comment, dict) or comment.get("deleted") or comment.get("dead"):
            continue
        text = _plain(comment.get("text"))
        if not text:
            continue
        comments += 1
        author = comment.get("by") if isinstance(comment.get("by"), str) else "unknown"
        parts.extend(["", f"--- comment {comments} by {author} ---", text])
    return _result("hackernews", title, "\n".join(parts), f"https://news.ycombinator.com/item?id={item_id}")


def _bilibili(client: httpx.Client, bvid: str, budget: float) -> dict | None:
    payload = _get_json(
        client, f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", budget
    )
    data = (payload or {}).get("data")
    if not isinstance(data, dict) or payload.get("code") != 0:
        return None
    title = str(data.get("title") or "").strip() or f"Bilibili video {bvid}"
    page_url = f"https://www.bilibili.com/video/{bvid}"
    parts = [title, page_url]
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    uploader = str(owner.get("name") or "").strip()
    if uploader:
        parts.append(f"UP: {uploader}")
    published = data.get("pubdate")
    if isinstance(published, int) and published > 0:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(published))
        parts.append(f"published: {stamp}")
    stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
    numbers = []
    if isinstance(stat.get("view"), int):
        numbers.append(f"{stat['view']} views")
    if isinstance(stat.get("danmaku"), int):
        numbers.append(f"{stat['danmaku']} danmaku")
    if numbers:
        parts.append("stats: " + ", ".join(numbers))
    description = str(data.get("desc") or "").strip()
    if description and description != "-":
        parts.extend(["", description])
    pages = data.get("pages") if isinstance(data.get("pages"), list) else []
    listing = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        part = str(page.get("part") or "").strip()
        length = _seconds(page.get("duration"))
        if part:
            listing.append(f"P{page.get('page', '?')} {part}" + (f" ({length})" if length else ""))
    if len(listing) > 1:
        parts.extend(["", "--- pages ---", *listing])
    return _result("bilibili", title, "\n".join(parts), page_url)


def _douban(client: httpx.Client, subject_id: str, budget: float) -> dict | None:
    payload = _get_json(
        client,
        f"https://m.douban.com/rexxar/api/v2/movie/{subject_id}",
        budget,
        headers=_DOUBAN_REFERER,
    )
    if not isinstance(payload, dict) or not payload.get("title"):
        return None
    title = str(payload["title"]).strip()
    year = str(payload.get("year") or "").strip()
    original = str(payload.get("original_title") or "").strip()
    label = title + (f" ({year})" if year else "")
    page_url = f"https://movie.douban.com/subject/{subject_id}/"
    parts = [label, page_url]
    if original and original != title:
        parts.append(original)
    rating = payload.get("rating") if isinstance(payload.get("rating"), dict) else {}
    if rating.get("value") is not None:
        count = rating.get("count")
        star = f"{rating['value']}/10" + (f" from {count} ratings" if isinstance(count, int) else "")
        parts.append(f"rating: {star}")
    for key, header in (("directors", "directors"), ("actors", "cast")):
        people = payload.get(key) if isinstance(payload.get(key), list) else []
        names = [str(person.get("name")).strip() for person in people if isinstance(person, dict)]
        names = [name for name in names if name][:5]
        if names:
            parts.append(f"{header}: " + ", ".join(names))
    genres = [str(genre).strip() for genre in payload.get("genres") or [] if str(genre).strip()]
    if genres:
        parts.append("genres: " + ", ".join(genres))
    durations = [str(item).strip() for item in payload.get("durations") or [] if str(item).strip()]
    if durations:
        parts.append("duration: " + ", ".join(durations))
    intro = str(payload.get("intro") or "").strip()
    if intro:
        parts.extend(["", intro])
    return _result("douban", label, "\n".join(parts), page_url)


def _juejin(client: httpx.Client, article_id: str, budget: float) -> dict | None:
    if budget <= 0:
        return None
    response = _send(
        client,
        "POST",
        "https://api.juejin.cn/content_api/v1/article/detail",
        budget,
        json={"article_id": article_id, "client_type": 2608},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("err_no") != 0:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    article = data.get("article_info") if isinstance(data.get("article_info"), dict) else {}
    if not article.get("title"):
        return None
    title = str(article["title"]).strip()
    page_url = f"https://juejin.cn/post/{article_id}"
    parts = [title, page_url]
    author = data.get("author_user_info") if isinstance(data.get("author_user_info"), dict) else {}
    if author.get("user_name"):
        parts.append(f"author: {author['user_name']}")
    if isinstance(article.get("ctime"), str) and article["ctime"].isdigit():
        parts.append("published: " + time.strftime("%Y-%m-%d", time.localtime(int(article["ctime"]))))
    tags = [str(tag.get("tag_name")).strip() for tag in data.get("tags") or [] if isinstance(tag, dict)]
    tags = [tag for tag in tags if tag]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    body = _plain(article.get("content")) or str(article.get("mark_content") or "")
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if body:
        parts.extend(["", body])
    return _result("juejin", title, "\n".join(parts), page_url)


def _sspai(client: httpx.Client, article_id: str, budget: float) -> dict | None:
    payload = _get_json(
        client, f"https://sspai.com/api/v1/article/info/get?id={article_id}", budget
    )
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("title"):
        return None
    title = str(data["title"]).strip()
    page_url = f"https://sspai.com/post/{article_id}"
    parts = [title, page_url]
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    if author.get("nickname"):
        parts.append(f"author: {author['nickname']}")
    released = data.get("released_time")
    if isinstance(released, int) and released > 0:
        parts.append("published: " + time.strftime("%Y-%m-%d", time.localtime(released)))
    tags = [str(tag.get("title")).strip() for tag in data.get("tags") or [] if isinstance(tag, dict)]
    tags = [tag for tag in tags if tag]
    if tags:
        parts.append("tags: " + ", ".join(tags))
    body = _plain(data.get("body"))
    if body:
        parts.extend(["", body])
    # A paid or client-only post is short by nature; the endpoint still owns it,
    # so a short body beats rendering the shell that hides it.
    return _result("sspai", title, "\n".join(parts), page_url, min_chars=80)


def _vimeo(client: httpx.Client, video_id: str, budget: float) -> dict | None:
    """Answer a Vimeo page from its public oEmbed endpoint (metadata only)."""

    endpoint = (
        "https://vimeo.com/api/oembed.json?url="
        + quote(f"https://vimeo.com/{video_id}", safe="")
    )
    payload = _get_json(client, endpoint, budget=min(budget, _HTTP_TIMEOUT + 2))
    if not isinstance(payload, dict):
        return None
    title = _plain(payload.get("title"))
    lines = [line for line in (
        title,
        _plain(payload.get("description")),
        f"作者：{_plain(payload.get('author_name'))}" if payload.get("author_name") else "",
        f"时长：{_seconds(payload.get('duration'))}" if payload.get("duration") else "",
    ) if line]
    if not title or not lines:
        return None
    return _result(
        "vimeo",
        title=title,
        text="\n".join(lines),
        url=f"https://vimeo.com/{video_id}",
        min_chars=40,
    )


# One row per backend: the order is the order the walk tries them, and the
# pattern is the only thing that decides which row a URL belongs to. Handlers
# take the match, extract the id they need, and answer with a result dict or
# ``None`` when the endpoint answered without the fields a read needs.
_Backend = Callable[[httpx.Client, "re.Match[str]", float], "dict | None"]


def _bilibili_route(
    client: httpx.Client, match: "re.Match[str]", budget: float
) -> dict | None:
    """A video URL answers directly; a b23.tv short link is resolved first."""

    bvid = match.group("bvid")
    if bvid:
        return _bilibili(client, bvid, budget)
    resolved = _resolve_b23(client, match.group("short"), budget)
    if not resolved:
        return None
    redirected = _BILIBILI.match(resolved)
    if not redirected or not redirected.group("bvid"):
        return None
    return _bilibili(client, redirected.group("bvid"), budget)


_BACKENDS: dict[str, _Backend] = {
    "stackexchange": lambda c, m, b: _stackexchange(c, m.group("id"), b),
    "npm": lambda c, m, b: _npm(c, m.group("name"), b),
    "hackernews": lambda c, m, b: _hacker_news(c, m.group("id"), b),
    "bilibili": _bilibili_route,
    "douban": lambda c, m, b: _douban(c, m.group("id"), b),
    "juejin": lambda c, m, b: _juejin(c, m.group("id"), b),
    "sspai": lambda c, m, b: _sspai(c, m.group("id"), b),
    "vimeo": lambda c, m, b: _vimeo(c, m.group("id"), b),
}

# The last typed failure this task produced, so a caller that went through the
# plain ``dict | None`` seam can still say why nothing came back. Scoped to the
# task: two reads in different tasks never see each other's failure.
_LAST_FAILURE: contextvars.ContextVar[FallbackFailure | None] = contextvars.ContextVar(
    "envoy_public_api_failure", default=None
)


def route_backend(url: str) -> str | None:
    """The backend a URL belongs to, without touching the network."""

    if not isinstance(url, str) or not url.strip():
        return None
    probe = url.strip()
    for backend, pattern, _sample in SITE_BACKENDS:
        if pattern.match(probe):
            return backend
    return None


def failure_for(url: str) -> FallbackFailure | None:
    """The failure recorded for this URL, if this task called the seam for it.

    A short link is resolved before the endpoint is asked, so the recorded URL
    can be the expanded one while the caller holds the original. Both route to
    the same backend, and that is the weaker match allowed here.
    """

    failure = _LAST_FAILURE.get()
    if failure is None:
        return None
    asked = str(url or "").strip()
    if _display_url(failure.url) == _display_url(asked):
        return failure
    if route_backend(asked) == failure.backend:
        return failure
    return None


def public_api_fallback_outcome(
    url: str, *, goal: str = "", max_work_seconds: int = 20
) -> dict | FallbackFailure | None:
    """Answer a supported page from its public API.

    Three outcomes, and they are not interchangeable: a result dict, a
    :class:`FallbackFailure` naming why the endpoint produced nothing, and
    ``None`` for a URL no backend claims. A caller that only wants text takes
    :func:`public_api_fallback` instead.
    """

    if not isinstance(url, str) or not url.strip():
        _LAST_FAILURE.set(None)
        return None
    url = url.strip()
    route = None
    for backend, pattern, _sample in SITE_BACKENDS:
        match = pattern.match(url)
        if match is not None:
            route = (backend, match)
            break
    if route is None:
        _LAST_FAILURE.set(None)
        return None
    backend, match = route
    deadline = time.monotonic() + max(1, int(max_work_seconds))
    try:
        with _client() as client:
            result = _BACKENDS[backend](client, match, deadline - time.monotonic())
    except Exception as exc:  # noqa: BLE001 — a fallback must never raise into the reader
        code, detail = _classify(exc)
        failure = FallbackFailure(backend=backend, code=code, url=url, detail=detail)
        _LAST_FAILURE.set(failure)
        return failure
    if isinstance(result, dict):
        _LAST_FAILURE.set(None)
        return result
    failure = FallbackFailure(
        backend=backend,
        code=ErrorCode.EMPTY_CONTENT,
        url=url,
        detail=f"the {backend} endpoint answered without the fields a read needs",
    )
    _LAST_FAILURE.set(failure)
    return failure


def public_api_fallback(
    url: str, *, goal: str = "", max_work_seconds: int = 20
) -> dict | None:
    """Answer a supported page from its public API, or return None.

    The reader's seam: text or nothing, never an exception. Why nothing came
    back is kept in :func:`failure_for`, so recording the reason does not ask
    every caller to branch on it.
    """

    outcome = public_api_fallback_outcome(
        url, goal=goal, max_work_seconds=max_work_seconds
    )
    return outcome if isinstance(outcome, dict) else None
