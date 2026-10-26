"""Public-API fallbacks for pages whose HTML renders to an empty shell.

Each backend answers from a documented, unauthenticated public endpoint, so a
page that JavaScript renders client-side still yields real text. Only the exact
URL shapes listed in ``_ROUTES`` are handled; anything else returns ``None``.
Every failure (transport, status, malformed JSON, missing fields) also returns
``None``: a fallback may never raise into the reader.
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

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


def _get_json(client: httpx.Client, url: str, budget: float) -> Any:
    if budget <= 0:
        return None
    response = client.get(url, timeout=httpx.Timeout(min(_HTTP_TIMEOUT, budget)))
    response.raise_for_status()
    return response.json()


def _result(backend: str, title: str, text: str, url: str) -> dict:
    return {
        "backend": backend,
        "title": title.strip() or url,
        "text": text.strip()[:_MAX_TEXT],
        "url": url,
    }


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


def public_api_fallback(
    url: str, *, goal: str = "", max_work_seconds: int = 20
) -> dict | None:
    """Answer a supported page from its public API, or return None."""

    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    stackoverflow = _STACKOVERFLOW.match(url)
    npm = _NPM.match(url)
    hacker_news = _HACKER_NEWS.match(url)
    if not (stackoverflow or npm or hacker_news):
        return None
    deadline = time.monotonic() + max(1, int(max_work_seconds))
    try:
        with _client() as client:
            budget = deadline - time.monotonic()
            if stackoverflow:
                return _stackexchange(client, stackoverflow.group("id"), budget)
            if npm:
                return _npm(client, npm.group("name"), budget)
            return _hacker_news(client, hacker_news.group("id"), budget)
    except Exception:  # noqa: BLE001 — a fallback must never raise into the reader
        return None
