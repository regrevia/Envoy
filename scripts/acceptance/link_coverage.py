#!/usr/bin/env python3
"""Measure how many ordinary links Envoy can actually read.

The corpus is grouped by page family so a regression can be attributed to a
family instead of to a single host. Every URL is read through the public
``envoy read --request-json`` entrypoint, exactly the way a host would.

The measurement separates three things that are easy to conflate:

- **dead link** -- the site answers ``404``/``410`` (or the host does not
  resolve). ``HTTP precheck`` finds these before any read, and they are taken
  out of the denominator: a removed page is not a product defect.
- **environment unreachable** -- neither a direct connection nor Envoy's proxy
  fallback gets an answer. This is the machine, not the product, and it is
  reported on its own line.
- **product outcome** -- readable / partial / empty / truncated / blocked /
  failed, judged from the read response itself.

Because the social platforms (知乎 / 微博 / 抖音 / 小红书) treat repeated
automated hits as risk control, they are left out of an ordinary run. A single
deliberate run may include them with ``--include-risk-hosts``; this is the only
way to fill in the "with risk-control" denominator, so reports that need both
numbers do one run with the flag and the summary breaks the result down both
ways.

Usage:
    # precheck only: which links are dead / unreachable right now
    python scripts/acceptance/link_coverage.py --precheck-only

    # a measured run (risk-control hosts left alone)
    python scripts/acceptance/link_coverage.py --jobs 2 --timeout 180 --out /tmp/accept/run.json

    # the deliberate both-denominators run (hits each risk host once or twice)
    python scripts/acceptance/link_coverage.py --include-risk-hosts --repeats 2 --out /tmp/accept/run.json

    # compare two saved runs
    python scripts/acceptance/link_coverage.py --compare /tmp/accept/baseline.json --out /tmp/accept/round2.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies

#: Hosts that answer repeated automated hits with risk control. A run leaves
#: them alone unless ``--include-risk-hosts`` is passed on purpose.
RISK_HOSTS: tuple[str, ...] = ("zhihu.com", "weibo.com", "douyin.com", "xiaohongshu.com")

#: Same browser-ish UA the precheck uses for every request, so the precheck
#: and Envoy see the same site behaviour.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CORPUS: list[tuple[str, str, str]] = [
    # (family, label, url)
    # -- reference -------------------------------------------------------
    ("reference", "example.com", "https://example.com/"),
    ("reference", "wikipedia", "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)"),
    ("reference", "mdn", "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API"),
    ("reference", "python-zh-asyncio", "https://docs.python.org/zh-cn/3/library/asyncio.html"),
    ("reference", "runoob-python3", "https://www.runoob.com/python3/python3-tutorial.html"),
    ("reference", "php-manual", "https://www.php.net/manual/en/language.types.string.php"),
    ("reference", "python-docs", "https://docs.python.org/3/library/asyncio.html"),
    ("reference", "mdn-http", "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview"),
    ("reference", "rust-book", "https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html"),
    ("reference", "kubernetes", "https://kubernetes.io/docs/concepts/workloads/pods/"),
    ("reference", "gutenberg", "https://www.gutenberg.org/files/1342/1342-h/1342-h.htm"),
    # -- news ------------------------------------------------------------
    ("news", "bbc", "https://www.bbc.com/news"),
    ("news", "reuters-tech", "https://www.reuters.com/technology/"),
    ("news", "nytimes", "https://www.nytimes.com/section/technology"),
    ("news", "npr", "https://www.npr.org/sections/technology/"),
    ("news", "engadget-home", "https://www.engadget.com/"),
    ("news", "theverge-home", "https://www.theverge.com/"),
    ("news", "cnbc", "https://www.cnbc.com/technology/"),
    ("news", "guardian", "https://www.theguardian.com/international"),
    ("news", "pingwest", "https://www.pingwest.com/a/300000"),
    ("news", "oschina", "https://www.oschina.net/news"),
    ("news", "thepaper", "https://www.thepaper.cn/"),
    # -- blog ------------------------------------------------------------
    ("blog", "medium", "https://medium.com/@karpathy/a-recipe-for-training-neural-networks-9c4c9f7d4b6c"),
    ("blog", "substack", "https://platformer.news/"),
    ("blog", "simonwillison", "https://simonwillison.net/"),
    ("blog", "danluu", "https://danluu.com/"),
    # -- code ------------------------------------------------------------
    ("code", "github-repo", "https://github.com/psf/requests"),
    ("code", "github-issue", "https://github.com/psf/requests/issues/1"),
    ("code", "gitlab-readme", "https://gitlab.com/gitlab-org/gitlab/-/blob/master/README.md"),
    ("code", "github-blob", "https://github.com/psf/requests/blob/main/README.md"),
    ("code", "pypi", "https://pypi.org/project/requests/"),
    ("code", "npm", "https://www.npmjs.com/package/express"),
    ("code", "gist", "https://gist.github.com/kennethreitz/973705"),
    # -- forum -----------------------------------------------------------
    ("forum", "stackoverflow", "https://stackoverflow.com/questions/11227809/why-is-processing-a-sorted-array-faster-than-processing-an-unsorted-array"),
    ("forum", "hackernews", "https://news.ycombinator.com/item?id=1"),
    ("forum", "reddit", "https://www.reddit.com/r/Python/comments/1c0v7q/"),
    ("forum", "discourse", "https://meta.discourse.org/t/what-is-discourse/1"),
    ("forum", "devto", "https://dev.to/"),
    ("forum", "discourse-mozilla", "https://discourse.mozilla.org/"),
    ("forum", "arduino-forum", "https://forum.arduino.cc/"),
    ("forum", "lobsters", "https://lobste.rs/"),
    # -- social ----------------------------------------------------------
    ("social", "x-profile", "https://x.com/OpenAI"),
    ("social", "mastodon", "https://mastodon.social/@Gargron"),
    ("social", "bluesky", "https://bsky.app/profile/bsky.app"),
    ("social", "zhihu", "https://www.zhihu.com/question/19550224"),
    ("social", "weibo", "https://weibo.com/u/1642904381"),
    # -- video -----------------------------------------------------------
    ("video", "youtube", "https://www.youtube.com/watch?v=aircAruvnKk"),
    ("video", "bilibili", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("video", "youku", "https://v.youku.com/v_show/id_XNTk1ODQ0MjAw.html"),
    ("video", "peertube", "https://framatube.org/w/9c9de5e8-0a1e-484a-b099-e80766180a6d"),
    ("video", "vimeo", "https://vimeo.com/76979871"),
    ("video", "tiktok", "https://www.tiktok.com/@scout2015/video/6718335390845095173"),
    ("video", "ted-talk", "https://www.ted.com/talks/chimamanda_ngozi_adichie_the_danger_of_a_single_story"),
    # -- audio -----------------------------------------------------------
    ("audio", "simplecast-podcast", "https://feeds.simplecast.com/54nAGcIl"),
    ("audio", "soundcloud", "https://soundcloud.com/forss/flickermood"),
    ("social", "douban-group", "https://www.douban.com/group/topic/30678422/"),
    ("audio", "ximalaya-album", "https://www.ximalaya.com/album/316137"),
    ("audio", "apple-podcast", "https://podcasts.apple.com/us/podcast/the-daily/id1200361736"),
    ("audio", "spotify-episode", "https://open.spotify.com/episode/7makk4oTQel546B0PZlDM5"),
    # -- image -----------------------------------------------------------
    ("image", "pinterest", "https://www.pinterest.com/pin/1139481143253437748/"),
    ("image", "unsplash", "https://unsplash.com/s/photos/ocean"),
    # -- document --------------------------------------------------------
    ("document", "arxiv-abs", "https://arxiv.org/abs/1706.03762"),
    ("document", "arxiv-pdf", "https://arxiv.org/pdf/1706.03762"),
    ("document", "rfc9110-pdf", "https://www.rfc-editor.org/rfc/rfc9110.pdf"),
    ("document", "github-raw-md", "https://raw.githubusercontent.com/psf/requests/main/README.md"),
    ("document", "rfc", "https://www.rfc-editor.org/rfc/rfc9110.html"),
    # -- spa -------------------------------------------------------------
    ("spa", "notion-public", "https://www.notion.so/product/ai"),
    ("spa", "figma-community", "https://www.figma.com/community"),
    ("spa", "airtable-share", "https://airtable.com/"),
    ("spa", "google-sheets", "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"),
    # -- feed ------------------------------------------------------------
    ("feed", "arstechnica-rss", "https://feeds.arstechnica.com/arstechnica/index"),
    ("feed", "verge-rss", "https://www.theverge.com/rss/index.xml"),
    ("feed", "hn-rss", "https://news.ycombinator.com/rss"),
    ("feed", "github-atom", "https://github.com/psf/requests/releases.atom"),
    ("feed", "ruanyf-atom", "https://www.ruanyifeng.com/blog/atom.xml"),
    ("feed", "ithome-rss", "https://www.ithome.com/rss/"),
    # -- api -------------------------------------------------------------
    ("api", "json-api", "https://api.github.com/repos/psf/requests"),
    # -- web -------------------------------------------------------------
    ("web", "archive-org", "https://archive.org/details/nasa"),
    ("web", "amazon", "https://www.amazon.com/dp/B08N5WRWNW"),
    ("web", "wikipedia-zh", "https://zh.wikipedia.org/wiki/%E5%A4%A7%E6%A8%A1%E5%9E%8B"),
    ("web", "36kr", "https://36kr.com/"),
    ("web", "github-trending", "https://github.com/trending"),
    ("web", "w3.org", "https://www.w3.org/TR/html52/"),
    ("web", "agent-docs", "https://docs.anthropic.com/en/docs/overview"),
    # -- platforms -------------------------------------------------------
    # One or two entries per platform that ordinary users paste into a chat.
    # Shared links (b23.tv / v.douyin.com) and platform public pages are kept
    # apart from the canonical content page so a resolver fix and a reader fix
    # stay distinguishable. 微信公众号具体文章 / 视频号具体内容 only exist as
    # links copied inside the WeChat client, so the platform public page is the
    # honest address of that capability.
    ("platforms", "youtube-watch", "https://www.youtube.com/watch?v=aircAruvnKk"),
    ("platforms", "youtube-youtu.be", "https://youtu.be/dQw4w9WgXcQ"),
    ("platforms", "bilibili-video", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("platforms", "bilibili-av", "https://www.bilibili.com/video/BV1xx411c7mD"),
    ("platforms", "b23-bv", "https://b23.tv/BV1GJ411x7h7"),
    ("platforms", "b23-av", "https://b23.tv/av170001"),
    ("platforms", "weibo-profile", "https://weibo.com/u/1642904381"),
    ("platforms", "tiktok-video", "https://www.tiktok.com/@scout2015/video/6718335390845095173"),
    ("platforms", "wechat-mp-platform", "https://mp.weixin.qq.com/"),
    ("platforms", "wechat-channels-platform", "https://channels.weixin.qq.com/"),
    ("platforms", "zhihu-question", "https://www.zhihu.com/question/19550224"),
    ("platforms", "xiaoyuzhou-podcast", "https://www.xiaoyuzhoufm.com/podcast/672444113f485d82405e768e"),
    ("platforms", "x-profile", "https://x.com/OpenAI"),
    ("platforms", "instagram-reel", "https://www.instagram.com/reel/C3x0vQ0J6gF/"),
    ("platforms", "reddit-thread", "https://www.reddit.com/r/Python/comments/1c0v7q/"),
    ("platforms", "github-readme", "https://github.com/psf/requests/blob/main/README.md"),
    ("platforms", "medium-post", "https://medium.com/@karpathy/a-recipe-for-training-neural-networks-9c4c9f7d4b6c"),
    # -- cn-platform -----------------------------------------------------
    # The pages this round of adaptation work was aimed at. Kept in their own
    # family so their numbers are not averaged into the generic corpus.
    ("cn-platform", "douyin-video", "https://www.douyin.com/video/7698386448961683813"),
    ("cn-platform", "douyin-short", "https://v.douyin.com/iRNBho6u/"),
    ("cn-platform", "kuaishou-share", "https://www.kuaishou.com/f/X-1WVMwQuRjthjtv"),
    ("cn-platform", "kuaishou-video", "https://www.kuaishou.com/short-video/3x7xgbznn8g2mvy"),
    ("cn-platform", "xiaoyuzhou-episode", "https://www.xiaoyuzhoufm.com/episode/6a5f6a9fa3fec224d5a1200f"),
    ("cn-platform", "podcast-rss-ifanr", "https://www.ifanr.com/feed"),
    ("cn-platform", "podcast-rss-sspai", "https://sspai.com/feed"),
    ("cn-platform", "sspai-post", "https://sspai.com/post/73595"),
    ("cn-platform", "podcast-rss-geekpark", "https://www.geekpark.net/rss"),
    ("cn-platform", "podcast-rss-36kr", "https://www.36kr.com/feed"),
    ("cn-platform", "douban-movie", "https://movie.douban.com/subject/1292052/"),
    ("cn-platform", "douban-book", "https://book.douban.com/subject/1084336/"),
    ("cn-platform", "cnblogs-article", "https://www.cnblogs.com/rossiXYZ/p/15553994.html"),
    ("cn-platform", "juejin-post", "https://juejin.cn/post/6844903967407734797"),
    ("cn-media", "ithome-home", "https://www.ithome.com/"),
    ("cn-media", "ifanr-home", "https://www.ifanr.com/"),
    ("cn-media", "pingwest-home", "https://www.pingwest.com/"),
    ("cn-media", "qbitai-home", "https://www.qbitai.com/"),
    ("cn-media", "solidot", "https://www.solidot.org/"),
    ("cn-media", "guancha", "https://www.guancha.cn/"),
    ("cn-media", "jiemian-home", "https://www.jiemian.com/"),
    ("cn-media", "infoq-home", "https://www.infoq.cn/"),
    ("cn-media", "huxiu-home", "https://www.huxiu.com/"),
    ("cn-media", "leiphone-home", "https://www.leiphone.com/"),
    ("cn-media", "geekpark-home", "https://www.geekpark.net/"),
    ("cn-media", "cls-home", "https://www.cls.cn/"),
    ("cn-media", "yicai-home", "https://www.yicai.com/"),
    ("cn-media", "caixin-home", "https://www.caixin.com/"),
    ("cn-media", "wallstreetcn-home", "https://wallstreetcn.com/"),
    ("blog", "ruanyf-blog", "https://www.ruanyifeng.com/blog/"),
    ("image", "wikimedia-commons", "https://commons.wikimedia.org/wiki/Main_Page"),
    ("blog", "devto-home", "https://dev.to/"),
    ("blog", "zenn-home", "https://zenn.dev/"),
    ("blog", "qiita-home", "https://qiita.com/"),
    ("blog", "brendangregg-blog", "https://www.brendangregg.com/blog/index.html"),
    ("news", "lwn-home", "https://lwn.net/"),
    ("news", "techcrunch-home", "https://techcrunch.com/"),
]

#: Families a report table shows, in a stable order.
FAMILY_ORDER = [
    "reference", "news", "blog", "code", "forum", "social", "video", "audio",
    "image", "document", "spa", "feed", "api", "web", "cn-platform", "cn-media",
    "platforms",
]

#: Error strings that mean "no HTTP answer at all" (browser/proxy/DNS layer).
#: A read that ends here is an environment observation, so it is reported as
#: unreachable and kept out of the readability denominator - the same three-way
#: split the precheck makes.
EGRESS_ERROR_MARKERS = (
    "net::err_",
    "econnrefused",
    "econnreset",
    "etimedout",
    "timed out",
    "timeout",
    "network is unreachable",
    "temporary failure",
    "name or service not known",
    "nodename nor servname",
    "getaddrinfo",
    "dns",
    "ssl",
    "handshake",
    "tunnel",
    "proxy",
    "connection refused",
    "connection reset",
)

VERDICT_KEYS = ("readable", "partial", "empty", "truncated", "blocked", "unreachable", "failed")


@dataclass
class Entries:
    """A corpus row plus everything measured about it."""

    family: str
    label: str
    url: str
    risk_host: bool = False
    dead: bool = False
    precheck: dict = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.family, self.label)


# ---------------------------------------------------------------------------
# host / proxy / precheck
# ---------------------------------------------------------------------------


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_risk_host(url: str) -> bool:
    host = host_of(url)
    return any(host == item or host.endswith("." + item) for item in RISK_HOSTS)


def envoy_python() -> str:
    """An interpreter that can import ``envoy_cli`` (the product's own policy).

    The precheck must not invent its own egress rule: dead / unreachable /
    blocked only line up with the product when the proxy decision comes from
    ``envoy_cli.netpolicy``, the same module Envoy consults.
    """
    candidates = [
        os.getenv("ENVOY_PYTHON", ""),
        str(Path(__file__).resolve().parents[2] / "packages/envoy-cli/.venv/bin/python"),
        str(Path.home() / ".local/share/envoy-dsh/runtime/bin/python3"),
        sys.executable,
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            proc = subprocess.run(
                [candidate, "-c", "import envoy_cli"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            return candidate
    return sys.executable


@lru_cache(maxsize=1)
def detected_proxy() -> str | None:
    """The proxy Envoy itself would use, asked through its own policy module.

    ``python -c "from envoy_cli.netpolicy import detected_proxy; ..."`` is the
    authority, because ``netpolicy`` is what the product executes. Only when
    that module cannot be imported at all do we fall back to ``getproxies()``
    (which is what ``netpolicy`` reads internally anyway).
    """
    interpreter = envoy_python()
    probe = (
        "from envoy_cli.netpolicy import detected_proxy; "
        "print(detected_proxy() or '')"
    )
    try:
        proc = subprocess.run(
            [interpreter, "-c", probe], capture_output=True, text=True, timeout=30,
        )
        value = (proc.stdout or "").strip()
        if proc.returncode == 0:
            return value or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proxies = getproxies()
    except OSError:
        return None
    for key in ("https", "http", "all", "socks"):
        value = proxies.get(key)
        if value:
            return str(value) if "://" in str(value) else f"http://{value}"
    return None


#: httpx is the probe client: it is the same HTTP stack territory as the
#: product, and it takes the proxy Envoy's policy returned. The fallback to
#: urllib exists only for interpreters without httpx.
try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - depends on the interpreter
    httpx = None


def _http_probe_once(url: str, timeout: float, proxy: str | None) -> dict:
    """One probe, through the proxy Envoy's policy picked (when it picked one)."""
    if httpx is not None:
        try:
            with httpx.Client(
                proxy=proxy,
                follow_redirects=True,
                timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
                headers={"User-Agent": USER_AGENT},
                trust_env=False,
            ) as client:
                response = client.get(url)
            return {
                "code": str(response.status_code),
                "final_url": str(response.url),
                "note": "",
                "dead": response.status_code in {404, 410},
            }
        except Exception as exc:  # noqa: BLE001 - probe failures are data
            message = str(exc)
            lower = message.lower()
            dns_failure = any(
                marker in lower
                for marker in ("name or service not known", "nodename nor servname", "no address associated")
            )
            return {
                "code": "000",
                "final_url": "",
                "note": message[:200],
                "dead": dns_failure,
            }
    # urllib fallback keeps the same proxy decision; it never bypasses it.
    from urllib.request import ProxyHandler, Request, build_opener
    handlers = []
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            code = str(response.status)
            final_url = response.geturl()
            return {"code": code, "final_url": final_url, "note": "", "dead": code in {"404", "410"}}
    except Exception as exc:  # noqa: BLE001 - also catches HTTPError with a status
        import urllib.error

        if isinstance(exc, urllib.error.HTTPError):
            return {"code": str(exc.code), "final_url": getattr(exc, "url", ""), "note": "", "dead": exc.code in {404, 410}}
        message = str(exc)
        lower = message.lower()
        dns_failure = any(
            marker in lower
            for marker in ("name or service not known", "nodename nor servname", "no address associated")
        )
        return {"code": "000", "final_url": "", "note": message[:200], "dead": dns_failure}


def precheck_http(url: str, timeout: float = 20.0) -> dict:
    """Probe through Envoy's egress, then say ok / dead / unreachable.

    The plan mirrors ``envoy_cli.netpolicy.proxy_plan()``: direct first, then
    the policy proxy when one exists. Only 404/410 and a host that does not
    resolve count as dead; 403/429 and challenge pages are answers, so those
    links stay in the denominator and any read failure there is a product
    finding (``blocked``), not a dead link. A "curl cannot reach it" verdict is
    never the whole story on a machine where the product has a proxy fallback.
    """
    proxy = detected_proxy()
    attempts: list[dict] = []
    for via, selected in (("direct", None), ("proxy", proxy)):
        if via == "proxy" and not selected:
            continue
        result = _http_probe_once(url, timeout, selected)
        if result["dead"] and result["code"] == "000":
            # A local DNS failure is not proof the host is gone: a proxy
            # resolves the name at its own end, and a resolver can fail for a
            # moment under load. Retry once, then let the next hop decide.
            result = _http_probe_once(url, timeout, selected)
        attempts.append({"via": via, "proxy": selected, **result})
        if result["dead"] and result["code"] in {"404", "410"}:
            return {
                "status": "dead",
                "code": result["code"],
                "final_url": result["final_url"],
                "via": via,
                "attempts": attempts,
            }
        code = result["code"]
        if code and code != "000":
            return {
                "status": "ok",
                "code": code,
                "final_url": result["final_url"],
                "via": via,
                "attempts": attempts,
            }
    return {
        "status": "unreachable",
        "code": (attempts[-1]["code"] if attempts else None) or "000",
        "final_url": "",
        "via": "",
        "attempts": attempts,
    }


# ---------------------------------------------------------------------------
# read + classify
# ---------------------------------------------------------------------------


def build_request(label: str, url: str, repeat: int) -> dict:
    return {
        "protocol_version": "envoy.read/1",
        "request_id": f"coverage-{label}-r{repeat}-{uuid.uuid4().hex[:8]}",
        "source": {"kind": "auto", "url": url},
        "goal": "Read this page so the content can be answered from later",
        "budget": {"max_response_chars": 8000, "max_work_seconds": 120},
        "retention": {"derived_ttl_seconds": 600},
    }


def classify(response: dict) -> dict:
    """Reduce one read response to a verdict.

    ``result`` is a status string (ready/partial/blocked/...); the delivered
    body lives in ``blocks[].content.text``. Counting ``result`` as text is the
    one mistake this function exists to prevent.
    """
    result = str(response.get("result") or "")
    state = str(response.get("execution_state") or "")
    blocks = response.get("blocks") or []
    chars = 0
    for block in blocks:
        content = block.get("content") or {}
        text = content.get("text")
        if isinstance(text, str):
            chars += len(text)
    errors = response.get("errors") or []
    code = str((errors[0] or {}).get("code") or "") if errors else ""
    message = str((errors[0] or {}).get("message") or "") if errors else ""
    coverage = response.get("coverage") or {}
    modalities = coverage.get("modalities") or {}
    text_meta = modalities.get("text") or {}
    total_chars = int((text_meta.get("details") or {}).get("total_chars") or 0)
    reader_pages_all = chars >= 40 and total_chars <= 2 * chars
    if result == "ready" and chars >= 200:
        verdict = "readable"
    elif result == "partial" and chars >= 200:
        verdict = "partial"
    elif result in {"ready", "partial"} and reader_pages_all:
        # A short page is not an empty one: `example.com` is 170 characters and
        # the reader delivered all of it. Using a flat 200-character floor called
        # that "empty", which was a measurement defect, not a page defect.
        verdict = "readable" if result == "ready" else "partial"
    elif chars < 200 and total_chars >= 1000:
        # the page was extracted but almost nothing was delivered: a delivery
        # defect, not a reachability one.
        verdict = "truncated"
    elif result in {"ready", "partial"}:
        verdict = "empty"
    elif result in {"blocked", "needs_host", "needs_action"} or state in {"waiting_for_host", "blocked"}:
        verdict = "blocked"
    else:
        verdict = "failed"
    if verdict in {"blocked", "failed"}:
        detail = f"{code} {message}".lower()
        if any(marker in detail for marker in EGRESS_ERROR_MARKERS):
            # the read never got an HTTP answer: that is the environment or the
            # proxy of the day, not the page failing to be readable.
            verdict = "unreachable"
    return {
        "verdict": verdict,
        "status": result,
        "execution_state": state,
        "chars": chars,
        "blocks": len(blocks),
        "code": code,
        "message": message[:400],
        "total_chars": total_chars,
        "material_id": str(response.get("material_id") or ""),
        "needs": len(response.get("needs") or []),
    }


def run_read(url: str, label: str, repeat: int, timeout: float, envoy: str) -> dict:
    """One ``envoy read`` call, reduced to a small dict."""
    request = build_request(label, url, repeat)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [envoy, "read", "--request-json", "-"],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "verdict": "failed",
            "status": "",
            "code": "timeout",
            "message": f"no answer within {timeout:.0f}s",
            "seconds": round(time.monotonic() - started, 3),
        }
    seconds = time.monotonic() - started
    if proc.returncode != 0 and not proc.stdout.strip():
        return {
            "verdict": "failed",
            "status": "",
            "code": "crash",
            "message": (proc.stderr or "").strip()[-400:],
            "seconds": round(seconds, 3),
        }
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "verdict": "failed",
            "status": "",
            "code": "bad-json",
            "message": (proc.stdout or proc.stderr or "")[-400:],
            "seconds": round(seconds, 3),
        }
    outcome = classify(response)
    outcome["seconds"] = round(seconds, 3)
    return outcome


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def latency_stats(values: list[float]) -> dict:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p90": 0.0, "max": 0.0}
    return {
        "n": len(cleaned),
        "mean": round(statistics.fmean(cleaned), 2),
        "p50": round(percentile(cleaned, 0.5), 2),
        "p90": round(percentile(cleaned, 0.9), 2),
        "max": round(max(cleaned), 2),
    }


def scope_summary(entries: list[Entries], scope: str) -> dict:
    """Counts for one denominator.

    ``scope`` is ``all`` (everything a dead-link check left alive) or
    ``no_risk`` (the same, minus the risk-control hosts). Both are reported
    side by side so the two numbers cannot be quietly swapped for each other.
    """
    considered = [
        item for item in entries
        if not item.dead and (scope == "all" or not item.risk_host)
    ]
    counts = {key: 0 for key in VERDICT_KEYS}
    by_family: dict[str, dict[str, int]] = {}
    latencies: list[float] = []
    for item in considered:
        first = item.runs[0] if item.runs else None
        verdict = first["verdict"] if first else "failed"
        counts[verdict] = counts.get(verdict, 0) + 1
        family = by_family.setdefault(item.family, {key: 0 for key in VERDICT_KEYS})
        family[verdict] = family.get(verdict, 0) + 1
        if first and first.get("seconds"):
            latencies.append(float(first["seconds"]))
    denominator = len(considered)
    readable = counts.get("readable", 0)
    usable = readable + counts.get("partial", 0)
    reachable = denominator - counts.get("unreachable", 0)
    return {
        "scope": scope,
        "denominator": denominator,
        "readable": readable,
        "partial": counts.get("partial", 0),
        "empty": counts.get("empty", 0),
        "truncated": counts.get("truncated", 0),
        "blocked": counts.get("blocked", 0),
        "unreachable": counts.get("unreachable", 0),
        "failed": counts.get("failed", 0),
        "usable": usable,
        "usable_rate": round(usable / denominator, 3) if denominator else 0.0,
        "reachable_denominator": reachable,
        "reachable_readable_rate": round(readable / reachable, 3) if reachable else 0.0,
        "reachable_usable_rate": round(usable / reachable, 3) if reachable else 0.0,
        "by_family": by_family,
        "latency": latency_stats(latencies),
    }


def jitter_report(entries: list[Entries], repeats: int) -> dict:
    """How stable the verdict is across repeats, and how much latency moves."""
    measured = [item for item in entries if not item.dead and item.runs]
    verdict_flips: list[dict] = []
    status_flips = 0
    deltas: list[float] = []
    for item in measured:
        verdicts = [run["verdict"] for run in item.runs]
        if len(set(verdicts)) > 1:
            verdict_flips.append({
                "family": item.family,
                "label": item.label,
                "url": item.url,
                "verdicts": verdicts,
            })
        statuses = [run.get("status") for run in item.runs]
        if len(set(statuses)) > 1:
            status_flips += 1
        seconds = [float(run.get("seconds") or 0) for run in item.runs]
        if len(seconds) >= 2 and all(value > 0 for value in seconds[:2]):
            deltas.append(abs(seconds[0] - seconds[1]))
    return {
        "repeats": repeats,
        "entries": len(measured),
        "verdict_flip_count": len(verdict_flips),
        "verdict_flip_rate": round(len(verdict_flips) / len(measured), 3) if measured else 0.0,
        "status_flip_count": status_flips,
        "verdict_flips": verdict_flips,
        "latency_delta": latency_stats(deltas),
    }


def build_report(entries: list[Entries], args, envoy_version: str, proxy: str | None) -> dict:
    total = len(entries)
    dead = [item for item in entries if item.dead]
    risk_entries = [item for item in entries if item.risk_host]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": "scripts/acceptance/link_coverage.py",
        "envoy": args.envoy,
        "envoy_version": envoy_version,
        "jobs": args.jobs,
        "timeout_seconds": args.timeout,
        "repeats": args.repeats,
        "include_risk_hosts": bool(args.include_risk_hosts),
        "precheck_proxy": proxy or "",
        "corpus_total": total,
        "dead_links": [
            {
                "family": item.family,
                "label": item.label,
                "url": item.url,
                "code": item.precheck.get("code"),
                "final_url": item.precheck.get("final_url", ""),
            }
            for item in dead
        ],
        "unreachable_links": [
            {
                "family": item.family,
                "label": item.label,
                "url": item.url,
                "precheck": item.precheck.get("status"),
            }
            for item in entries
            if not item.dead and item.runs and item.runs[0]["verdict"] == "unreachable"
        ],
        "risk_control_links": [
            {"family": item.family, "label": item.label, "url": item.url}
            for item in risk_entries
        ],
        "corpus": [
            {
                "family": item.family,
                "label": item.label,
                "url": item.url,
                "risk_host": item.risk_host,
                "dead": item.dead,
                "precheck": item.precheck,
                "runs": item.runs,
            }
            for item in entries
        ],
        "summary": {
            "total": total,
            "dead": len(dead),
            "denominator_after_dead": total - len(dead),
            "risk_control_excluded": len([item for item in risk_entries if not item.dead]),
            "all": scope_summary(entries, "all"),
            "no_risk": scope_summary(entries, "no_risk"),
        },
        "jitter": jitter_report(entries, args.repeats),
    }


def format_metrics(summary: dict) -> list[str]:
    """Plain-text lining-up of the fixed metrics, both denominators."""
    all_scope = summary["all"]
    no_risk = summary["no_risk"]
    rows: list[str] = []
    rows.append(
        f"corpus {summary['denominator_after_dead']} live of {summary['denominator_after_dead'] + summary['dead']} "
        f"(dead {summary['dead']} excluded) · risk-control {summary['risk_control_excluded']}"
    )
    header = f"{'scope':<10}{'denom':>7}{'usable':>8}{'usable%':>9}{'reach%':>8}{'reach-usable%':>15}"
    rows.append(header)
    for scope in (all_scope, no_risk):
        rows.append(
            f"{scope['scope']:<10}{scope['denominator']:>7}{scope['usable']:>8}"
            f"{scope['usable_rate'] * 100:>8.1f}%{scope['reachable_readable_rate'] * 100:>7.1f}%"
            f"{scope['reachable_usable_rate'] * 100:>14.1f}%"
        )
    rows.append("")
    rows.append("verdicts     " + " ".join(f"{key:<10}" for key in VERDICT_KEYS))
    for scope in (all_scope, no_risk):
        rows.append(
            f"{scope['scope']:<13}" + " ".join(f"{scope[key]:<10}" for key in VERDICT_KEYS)
        )
    rows.append("")
    latency = no_risk["latency"]
    rows.append(
        f"latency (no-risk scope): n {latency['n']} · mean {latency['mean']}s · "
        f"p50 {latency['p50']}s · p90 {latency['p90']}s · max {latency['max']}s"
    )
    return rows


def format_family_table(entries: list[Entries]) -> list[str]:
    considered = [item for item in entries if not item.dead]
    rows = [
        f"{'family':<12}{'n':>4}  " + " ".join(f"{key:<9}" for key in VERDICT_KEYS)
    ]
    for family in FAMILY_ORDER:
        family_entries = [item for item in considered if item.family == family]
        if not family_entries:
            continue
        counts = {key: 0 for key in VERDICT_KEYS}
        for item in family_entries:
            verdict = item.runs[0]["verdict"] if item.runs else "failed"
            counts[verdict] += 1
        rows.append(
            f"{family:<12}{len(family_entries):>4}  "
            + " ".join(f"{counts[key]:<9}" for key in VERDICT_KEYS)
        )
    return rows


def render(entries: list[Entries], report: dict) -> str:
    lines: list[str] = []
    lines.append(f"envoy: {report['envoy']} ({report['envoy_version']})")
    lines.append(f"precheck proxy: {report['precheck_proxy'] or 'none'}")
    lines.extend(format_metrics(report["summary"]))
    lines.append("")
    lines.append("family       " + " ".join(f"{key:<9}" for key in VERDICT_KEYS))
    lines.extend(format_family_table(entries)[1:])
    lines.append("")
    missed = [
        item for item in entries
        if not item.dead and item.runs and item.runs[0]["verdict"] not in {"readable", "partial"}
    ]
    dead = [item for item in entries if item.dead]
    lines.append(f"dead links excluded ({len(dead)}):")
    for item in dead:
        lines.append(f"- dead      {item.family:<9} {item.label:<20} {item.url} [{item.precheck.get('code')}]")
    lines.append("")
    lines.append(f"misses ({len(missed)}):")
    for item in missed:
        first = item.runs[0]
        lines.append(
            f"- {first['verdict']:<9} {item.family:<9} {item.label:<20} "
            f"{first.get('code') or first.get('status'):<18} {str(first.get('message'))[:80]}"
        )
    jitter = report["jitter"]
    if jitter["repeats"] > 1:
        lines.append("")
        lines.append(
            f"jitter: {jitter['verdict_flip_count']} verdict flips / {jitter['entries']} entries "
            f"({jitter['verdict_flip_rate'] * 100:.1f}%), {jitter['status_flip_count']} status flips, "
            f"latency delta mean {jitter['latency_delta']['mean']}s "
            f"p90 {jitter['latency_delta']['p90']}s"
        )
        for flip in jitter["verdict_flips"]:
            lines.append(f"- flip {flip['label']}: {' → '.join(flip['verdicts'])}")
    return "\n".join(lines)


def compare_reports(previous: dict, current: dict) -> str:
    """Per-scope movement plus per-link transitions between two saved runs."""
    lines: list[str] = []
    for scope in ("no_risk", "all"):
        old = previous.get("summary", {}).get(scope) or {}
        new = current.get("summary", {}).get(scope) or {}
        if not old or not new:
            continue
        lines.append(
            f"{scope:<8} usable {old.get('usable')}/{old.get('denominator')} "
            f"({old.get('usable_rate', 0) * 100:.1f}%) → {new.get('usable')}/{new.get('denominator')} "
            f"({new.get('usable_rate', 0) * 100:.1f}%)  "
            f"reachable-readable {old.get('reachable_readable_rate', 0) * 100:.1f}% → "
            f"{new.get('reachable_readable_rate', 0) * 100:.1f}%  "
            f"latency p50 {old.get('latency', {}).get('p50', 0)}s → {new.get('latency', {}).get('p50', 0)}s"
        )
    previous_runs = {
        (item.get("family"), item.get("label")): item
        for item in previous.get("corpus", [])
    }
    current_runs = {
        (item.get("family"), item.get("label")): item
        for item in current.get("corpus", [])
    }
    transitions: list[str] = []
    for key in sorted(set(previous_runs) & set(current_runs)):
        old_item = previous_runs[key]
        new_item = current_runs[key]
        old_verdict = (old_item.get("runs") or [{}])[0].get("verdict")
        new_verdict = (new_item.get("runs") or [{}])[0].get("verdict")
        if old_item.get("dead") or new_item.get("dead"):
            continue
        if old_verdict != new_verdict:
            transitions.append(
                f"- {key[0]:<9} {key[1]:<20} {old_verdict:<10} → {new_verdict}"
            )
    lines.append("")
    lines.append("changed verdicts:")
    lines.extend(transitions or ["- none"])
    old_urls = {item.get("url") for item in previous.get("corpus", [])}
    new_urls = {item.get("url") for item in current.get("corpus", [])}
    if old_urls != new_urls:
        lines.append("")
        lines.append(f"corpus changed: +{len(new_urls - old_urls)} / -{len(old_urls - new_urls)} URLs (numbers are not strictly comparable)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def select_entries(args) -> tuple[list[Entries], list[Entries]]:
    """Corpus rows to measure, plus the ones left alone for risk-control reasons."""
    families = {item.strip() for item in args.only.split(",") if item.strip()}
    entries: list[Entries] = []
    skipped: list[Entries] = []
    seen_urls: set[str] = set()
    skip_hosts = tuple(item.strip() for item in args.skip_hosts.split(",") if item.strip())
    for family, label, url in CORPUS:
        if families and family not in families:
            continue
        host = host_of(url)
        risky = any(host == item or host.endswith("." + item) for item in (skip_hosts or RISK_HOSTS))
        entry = Entries(family=family, label=label, url=url, risk_host=risky)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if entry.risk_host and not args.include_risk_hosts:
            skipped.append(entry)
            continue
        entries.append(entry)
    return entries, skipped


def precheck_entries(entries: list[Entries], jobs: int, timeout: float) -> dict:
    summary = {"ok": 0, "dead": 0, "unreachable": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(precheck_http, item.url, timeout): item for item in entries}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - a probe must never abort a run
                result = {"status": "unreachable", "code": None, "error": str(exc)[:200], "attempts": []}
            item.precheck = result
            item.dead = result.get("status") == "dead"
            summary[result.get("status", "unreachable")] = summary.get(result.get("status", "unreachable"), 0) + 1
            print(
                f"precheck {result.get('status', '?'):<11} {item.family:<9} {item.label:<20} "
                f"{result.get('code')} via {result.get('via') or '-'}",
                flush=True,
            )
    return summary


def envoy_version(envoy: str) -> str:
    try:
        proc = subprocess.run([envoy, "--version"], capture_output=True, text=True, timeout=30)
        return (proc.stdout or proc.stderr or "").strip().splitlines()[0][:120]
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="", help="comma-separated families")
    parser.add_argument("--jobs", type=int, default=2, help="parallel reads, clamped to 3")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-read timeout in seconds")
    parser.add_argument("--repeats", type=int, default=1, help="run each link N times to measure jitter")
    parser.add_argument("--compare", default="", help="previous run JSON to compare against")
    parser.add_argument("--envoy", default="envoy", help="envoy executable")
    parser.add_argument("--precheck-only", action="store_true", help="only probe the corpus for dead/unreachable links")
    parser.add_argument("--precheck-jobs", type=int, default=8)
    parser.add_argument("--precheck-timeout", type=float, default=20.0)
    parser.add_argument(
        "--include-risk-hosts",
        action="store_true",
        help="also read 知乎/微博/抖音/小红书; these sites treat repeated automated hits as risk "
             "control, so use it once, deliberately, never in a loop",
    )
    parser.add_argument(
        "--skip-hosts",
        default=",".join(RISK_HOSTS),
        help="comma-separated host fragments left out of an ordinary run",
    )
    args = parser.parse_args()
    args.jobs = max(1, min(3, args.jobs))
    args.repeats = max(1, args.repeats)

    entries, skipped = select_entries(args)
    proxy = detected_proxy()
    for item in skipped:
        print(f"skipped  {item.family:<9} {item.label:<20} {item.url} (risk-control host, --include-risk-hosts)", flush=True)

    precheck_summary = precheck_entries(entries, args.precheck_jobs, args.precheck_timeout)
    print(f"\nprecheck: {precheck_summary}", flush=True)
    if args.precheck_only:
        report = build_report(entries, args, "not-run", proxy)
        report["skipped_risk"] = [
            {"family": item.family, "label": item.label, "url": item.url} for item in skipped
        ]
        print(render(entries, report))
        if args.out:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\nwrote {args.out}")
        return 0

    version = envoy_version(args.envoy)
    measured = [item for item in entries if not item.dead]
    started_all = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures: dict[concurrent.futures.Future, tuple[Entries, int]] = {}
        for item in measured:
            for repeat in range(args.repeats):
                futures[pool.submit(run_read, item.url, item.label, repeat, args.timeout, args.envoy)] = (item, repeat)
        for future in concurrent.futures.as_completed(futures):
            item, repeat = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - one broken read must not abort the run
                result = {"verdict": "failed", "code": "runner_error", "message": str(exc)[:200], "seconds": 0.0}
            result["repeat"] = repeat
            result["url"] = item.url
            item.runs.append(result)
            print(
                f"run {repeat}  {result['verdict']:<9} {item.family:<9} {item.label:<20} "
                f"{result.get('seconds', 0):5.1f}s chars={result.get('chars', 0)}",
                flush=True,
            )
    for item in entries:
        item.runs.sort(key=lambda run: run.get("repeat", 0))
    print(f"\nmeasured {len(measured)} links × {args.repeats} in {time.monotonic() - started_all:.0f}s", flush=True)

    report = build_report(entries, args, version, proxy)
    report["skipped_risk"] = [
        {"family": item.family, "label": item.label, "url": item.url} for item in skipped
    ]
    print()
    print(render(entries, report))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    if args.compare:
        previous = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        print()
        print(compare_reports(previous, report))
    no_risk = report["summary"]["no_risk"]
    return 0 if no_risk["usable_rate"] >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
