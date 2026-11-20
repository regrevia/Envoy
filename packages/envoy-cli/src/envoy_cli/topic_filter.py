"""Keep only the candidates a stated goal actually asks about.

A goal such as "UI 相关的" names a topic in a few characters; titles carry it in
different words. Nothing here calls a model: the filter is a deterministic
overlap score so the same goal always keeps the same items, and every dropped
item keeps the reason it was dropped.
"""

from __future__ import annotations

import re

# Words that describe the errand rather than the topic. Leaving them in would
# match almost every title ("整理一下这些视频" says nothing about UI).
_STOPWORDS = frozenset(
    """
    帮我 帮忙 麻烦 一下 整理 整理一下 汇总 总结 归纳 提取 洗一遍 过一遍 看看
    视频 短视频 那个 这些 那些 这个 那个 收藏 收藏夹 里面 里的 有关 相关
    关于 帖子 笔记 内容 东西 一份 一个 一些 想要 需要 给我 我要 可以 然后
    所有 全部 每条 每个 以及 还有 就行 就好 看看有 有没有 哪些 什么 怎么
    the a an and or of for to in on with about all every each some any
    please help summarise summarize collection favourite favorite video videos
    """.split()
)

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")

# Function words and characters. A bi-gram that touches one of these is
# grammar, not topic: without this "我要 UI 相关的那些视频" yields "些视" and
# "关的" as if they were topics.
_CJK_STOP_CHARS = frozenset(
    "我你他她它咱您的了着过把被让给和与及或就都也还在是有个这那些么什怎样"
    "要和想去来去到一上下里中大小多少几又很太更最而这那就才能会可请帮麻烦"
    "看说做弄整理汇总统归纳提取遍历刷收关相条每全所些点"
)
_CJK_STOP_PHRASES = (
    "相关", "有关", "关于", "整理", "汇总", "总结", "归纳", "提取", "视频", "短视频",
    "收藏", "笔记", "内容", "东西", "一下", "那些", "这些", "里面", "什么", "怎么",
    "帮我", "麻烦", "看看", "然后", "全部", "所有", "需要", "想要", "给我", "我要",
)
_LATIN = re.compile(r"[a-z][a-z0-9+#._-]{1,}")
_PUNCT = re.compile(r"[\s,，。、;；:：!！?？()（）\[\]【】\"'“”‘’/\\|]+")

# A goal token that appears in a title is worth this much; a token that only
# appears next to another goal token counts less, because it is likely generic.
_MATCH_WEIGHT = 1.0
_PARTIAL_WEIGHT = 0.45
DEFAULT_MIN_SCORE = 0.34


def _latin_tokens(text: str) -> list[str]:
    lowered = (text or "").lower()
    out = []
    for raw in _LATIN.findall(lowered):
        token = raw.strip(".-_")
        if not token or token in _STOPWORDS or len(token) < 2:
            continue
        out.append(token)
    # "ui" and "ux" are the two shortest topic words worth keeping.
    for short in ("ui", "ux", "ai", "ar", "vr", "3d", "qa"):
        if re.search(rf"(?<![a-z0-9]){short}(?![a-z0-9])", lowered):
            out.append(short)
    return out


def _cjk_segments(text: str) -> list[str]:
    """Cut a Chinese sentence into the chunks that could name a topic."""

    cleaned = str(text or "")
    for phrase in _CJK_STOP_PHRASES:
        cleaned = cleaned.replace(phrase, " ")
    segments: list[str] = []
    for run in _CJK.findall(cleaned):
        chunk = ""
        for char in run:
            if char in _CJK_STOP_CHARS:
                if chunk:
                    segments.append(chunk)
                chunk = ""
            else:
                chunk += char
        if chunk:
            segments.append(chunk)
    return segments


def _cjk_tokens(text: str) -> list[str]:
    """Chinese needs no dictionary here: two-character windows find "界面工程"."""

    out: list[str] = []
    for segment in _cjk_segments(text):
        if segment in _STOPWORDS:
            continue
        if len(segment) <= 2:
            out.append(segment)
            continue
        for index in range(len(segment) - 1):
            gram = segment[index : index + 2]
            if (
                gram not in _STOPWORDS
                and not (set(gram) & _CJK_STOP_CHARS)
            ):
                out.append(gram)
        # The whole chunk is the strongest signal when it is short enough to
        # name a topic on its own.
        if len(segment) <= 6:
            out.append(segment)
    return out


def goal_tokens(goal: str) -> list[str]:
    """The topic words in a goal, most specific first, without duplicates."""

    cleaned = _PUNCT.sub(" ", str(goal or ""))
    tokens: list[str] = []
    seen: set[str] = set()
    for token in _cjk_tokens(cleaned) + _latin_tokens(cleaned):
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    # Longer tokens are more specific, so they lead and break ties.
    return sorted(tokens, key=lambda item: (-len(item), item))


def topic_score(text: str, tokens: list[str]) -> float:
    """How much of the goal a piece of text carries, in 0..1."""

    if not tokens:
        return 0.0
    haystack = (text or "").lower()
    if not haystack.strip():
        return 0.0
    matched = 0.0
    total = 0.0
    for token in tokens:
        total += _MATCH_WEIGHT
        if token in haystack:
            matched += _MATCH_WEIGHT
        elif len(token) >= 3 and any(token[:index] in haystack for index in (len(token) - 1, len(token) - 2)):
            matched += _PARTIAL_WEIGHT
    if not total:
        return 0.0
    return round(min(1.0, matched / total), 4)


def _prune_by_document_frequency(tokens: list[str], candidates: list[dict]) -> list[str]:
    """Drop goal words that say nothing about which item is which.

    A word that appears in no title cannot be the topic the user meant, and a
    word in every title separates nothing. Both are noise from the sentence
    around the topic, so scoring only sees the words in between.
    """

    if not tokens or not candidates:
        return tokens
    titles = [
        " ".join(str(item.get(key) or "") for key in ("title", "description")).lower()
        for item in candidates
    ]
    total = len(titles)
    kept: list[str] = []
    for token in tokens:
        hits = sum(1 for title in titles if token in title)
        if 0 < hits < total:
            kept.append(token)
        elif hits == total and total == 1:
            kept.append(token)
    # Never return nothing just because the vocabulary missed: an empty token
    # list would silently mean "no topic given" and keep everything.
    return kept or tokens[:1]


def select_by_topic(
    candidates: list[dict],
    goal: str,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict:
    """Split candidates into the ones the goal asks for and the rest.

    An empty goal keeps everything: "整理一下" with no topic is a request to
    process the whole collection, not to guess one.
    """

    tokens = _prune_by_document_frequency(goal_tokens(goal), candidates)
    if not tokens:
        return {
            "goal": goal,
            "tokens": [],
            "mode": "no_topic",
            "kept": list(candidates),
            "dropped": [],
            "min_score": min_score,
        }
    kept: list[dict] = []
    dropped: list[dict] = []
    for candidate in candidates:
        # The title is what the author wrote; the creator names the person, not
        # the topic, so it never decides a match on its own.
        text = " ".join(
            str(candidate.get(key) or "") for key in ("title", "description")
        )
        score = topic_score(text, tokens)
        item = dict(candidate)
        item["topic_score"] = score
        (kept if score >= min_score else dropped).append(item)
    kept.sort(key=lambda item: (-item.get("topic_score", 0.0), item.get("position", 0)))
    return {
        "goal": goal,
        "tokens": tokens,
        "mode": "topic_filtered",
        "kept": kept,
        "dropped": dropped,
        "min_score": min_score,
    }
