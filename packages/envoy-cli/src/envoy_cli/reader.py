from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import socket
import shutil
import sys
import tempfile
import time
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from pypdf import PdfReader
from .video import extract_public_subtitles, has_specialized_extractor
from .media import (
    MediaBudgetExceeded,
    MediaCapabilityMissing,
    MediaNoSpeech,
    MediaProcessingError,
    MediaUnsafeAddress,
    asr_model_cache_dir,
    download_audio_for_asr,
    download_video_for_frames,
    extract_video_frames,
    interprocess_file_lock,
    media_runtime_status,
    probe_media_duration,
    transcribe_audio_file,
)
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ErrorCode,
    ExpandRequest,
    ExecutionState,
    HostDelivery,
    ImageContent,
    ImageInputCapability,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadError,
    ReadRequest,
    ReadResponse,
    ReadResult,
    ResourceRef,
    Need,
    Quality,
    MaterialSearchRequest,
    PdfLocator,
    TextContent,
    Usage,
    VideoLocator,
    VideoContent,
    VisualObservationHistoryRequest,
    WebLocator,
)


MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_TEXT_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_TEXT_CHARS = 2_000_000
MAX_SEARCH_CACHE_CHARS = 256_000
MAX_IMAGES = 40
CHALLENGE_MARKERS = (
    "验证码",
    "安全验证",
    "人机验证",
    "登录后继续",
    "verify you are human",
    "checking your browser",
    "captcha",
)
MAX_FETCH_ATTEMPTS = 3
MIN_EXPANSION_CHARS = 128
MATERIAL_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHED_MATERIALS = 100
MAX_LOCATOR_QUOTE_CHARS = 120
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif")
DIRECT_VIDEO_SUFFIXES = (
    ".mp4", ".m4v", ".mov", ".webm", ".m3u8", ".ogv", ".avi", ".mkv",
    ".mpeg", ".mpg", ".3gp", ".ts", ".flv", ".wmv",
)
_READ_WORK_DEADLINE: ContextVar[float | None] = ContextVar(
    "envoy_read_work_deadline",
    default=None,
)
SAFE_VIDEO_QUERY_KEYS = {
    "v", "t", "start", "end", "list", "index", "share", "modal_id",
    "mid", "item_id", "lang", "p", "page", "download", "filename",
    "format", "quality", "source", "utm_source", "utm_medium", "utm_campaign",
}
TEXT_SUFFIXES = (".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".rst")
TEXT_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/yaml",
    "text/yaml",
    "application/x-yaml",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "signature",
    "sig",
    "token",
    "x-amz-signature",
    "x-goog-signature",
    "x-oss-signature",
}


class PublicPageError(RuntimeError):
    def __init__(self, message: str, code: ErrorCode = ErrorCode.SOURCE_UNAVAILABLE):
        super().__init__(message)
        self.code = code


class DetectedPDF(PublicPageError):
    def __init__(self, source: dict):
        super().__init__("The URL resolved to a PDF document", ErrorCode.UNSUPPORTED_SOURCE)
        self.source = source


class DetectedText(PublicPageError):
    def __init__(self, source: dict):
        super().__init__("The URL resolved to a text document", ErrorCode.UNSUPPORTED_SOURCE)
        self.source = source


class DetectedImage(PublicPageError):
    def __init__(self, source: dict):
        super().__init__("The URL resolved to an image document", ErrorCode.UNSUPPORTED_SOURCE)
        self.source = source


class DetectedMedia(PublicPageError):
    def __init__(self, source: dict):
        super().__init__("The URL resolved to a media document", ErrorCode.UNSUPPORTED_SOURCE)
        self.source = source


class _PageParser(HTMLParser):
    _ignored_tags = {"script", "style", "noscript", "template", "svg", "nav", "footer", "aside", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.images: list[dict[str, str]] = []
        self.video_urls: list[str] = []
        self._ignored_depth = 0
        self._in_title = False
        self._block_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attrs_map = dict(attrs)
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}:
            self.text_parts.append("\n")
            self._block_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "img" and self._ignored_depth == 0:
            src = (attrs_map.get("src") or attrs_map.get("data-src") or "").strip()
            if src:
                self.images.append({"url": src, "alt": (attrs_map.get("alt") or "").strip()})
        if tag in {"video", "source"} and self._ignored_depth == 0:
            src = (attrs_map.get("src") or "").strip()
            if src:
                self.video_urls.append(src)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"} and self._block_depth:
            self.text_parts.append("\n")
            self._block_depth -= 1

    def handle_data(self, data: str):
        if self._ignored_depth:
            return
        value = data.strip()
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        else:
            self.text_parts.append(value)


class _DouyinNoteGalleryParser(HTMLParser):
    _excluded_markers = (
        "recommend", "related", "related-list", "video-playing-item",
        "avatar", "profile", "comment", "feed",
    )
    _gallery_markers = ("note-image", "image-list", "swiper", "album")
    _void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.images: list[dict[str, str]] = []
        self.total_count: int | None = None
        self._note_depth = 0
        self._gallery_depth = 0
        self._excluded_depth = 0
        self._stack: list[tuple[bool, bool, bool]] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_map = dict(attrs)
        identity = " ".join(str(attrs_map.get(key) or "") for key in (
            "class", "id", "data-e2e", "data-testid",
        )).lower()
        excluded = any(marker in identity for marker in self._excluded_markers)
        note_region = (
            attrs_map.get("data-e2e") == "note-detail"
            or "note-detail-container" in identity
        )
        gallery = self._note_depth > 0 and any(
            marker in identity for marker in self._gallery_markers
        )
        if tag.lower() not in self._void_tags:
            self._stack.append((note_region, gallery, excluded))
        if note_region:
            self._note_depth += 1
        if gallery:
            self._gallery_depth += 1
            for key in ("data-total", "data-count", "data-image-count"):
                try:
                    count = int(attrs_map.get(key))
                except (TypeError, ValueError):
                    continue
                if count >= len(self.images):
                    self.total_count = count
                    break
        if excluded:
            self._excluded_depth += 1
        if tag.lower() != "img" or self._gallery_depth <= 0 or self._excluded_depth:
            return
        src = next((
            str(attrs_map.get(key) or "").strip()
            for key in ("src", "data-src", "data-lazy-src", "data-origin-src")
            if str(attrs_map.get(key) or "").strip()
        ), "")
        if not src:
            srcset = str(attrs_map.get("srcset") or "").strip()
            if srcset:
                src = srcset.split(",")[0].strip().split(" ")[0]
        if src:
            absolute = urljoin(self.source_url, src)
            if absolute not in {image["url"] for image in self.images}:
                self.images.append({
                    "url": absolute,
                    "alt": str(attrs_map.get("alt") or "").strip(),
                })

    def handle_endtag(self, _tag: str):
        if not self._stack:
            return
        note_region, gallery, excluded = self._stack.pop()
        if note_region:
            self._note_depth = max(0, self._note_depth - 1)
        if gallery:
            self._gallery_depth = max(0, self._gallery_depth - 1)
        if excluded:
            self._excluded_depth = max(0, self._excluded_depth - 1)


def extract_douyin_note_gallery(html: str, source_url: str) -> tuple[list[dict[str, str]], int | None]:
    parser = _DouyinNoteGalleryParser(source_url)
    parser.feed(html)
    parser.close()
    return parser.images, parser.total_count


def _is_public_http_url(value: str, dns_cache: dict[str, bool] | None = None) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return False
        try:
            addresses = {ipaddress.ip_address(host)}
        except ValueError:
            if dns_cache is not None and host in dns_cache:
                return dns_cache[host]
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                )
            }
            result = bool(addresses) and all(address.is_global for address in addresses)
            if dns_cache is not None:
                dns_cache[host] = result
            return result
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError, TypeError):
        return False


def _is_sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_-]", "", key.lower())
    return normalized in SENSITIVE_QUERY_KEYS or normalized.endswith(("_token", "_secret", "_signature"))


def _video_handoff_url_is_safe(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.username or parsed.password or parsed.fragment:
        return False
    for key, _item in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r"[^a-z0-9_-]", "", key.lower())
        if _is_sensitive_query_key(key):
            return False
        if normalized not in SAFE_VIDEO_QUERY_KEYS and not normalized.startswith("utm_"):
            return False
    return True


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    safe_query = [
        (key, "[REDACTED]" if _is_sensitive_query_key(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(safe_query),
        "",
    ))


def _visual_source_identity(value: str) -> str:
    parsed = urlparse(value)
    safe_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_sensitive_query_key(key)
    ]
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(safe_query),
        "",
    ))


def _clean_text(values: list[str]) -> str:
    text = "\n".join(values)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def _goal_terms(goal: str) -> list[str]:
    terms = []
    for item in re.split(r"[\s,，。.!！?？:：;；/、()\[\]{}]+", str(goal or "").lower()):
        value = item.strip()
        if len(value) >= 2 and value not in terms:
            terms.append(value)
    return terms[:24]


def _goal_score(value: str, terms: list[str]) -> int:
    haystack = str(value or "").lower()
    score = 0
    for term in terms:
        if term.isascii() and term.isalnum():
            score += len(re.findall(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                haystack,
            ))
        else:
            score += haystack.count(term)
    return score


def _safe_visual_candidates(
    images: list[dict],
    page_url: str,
    dns_cache: dict[str, bool],
) -> tuple[list[dict], int]:
    selected = []
    seen_urls = set()
    skipped_sensitive = 0
    for rank, image in enumerate(images, start=1):
        image_url = urljoin(page_url, str(image.get("url") or ""))
        if any(
            _is_sensitive_query_key(key)
            for key, _value in parse_qsl(urlparse(image_url).query, keep_blank_values=True)
        ):
            skipped_sensitive += 1
            continue
        if image_url in seen_urls or not _is_public_http_url(image_url, dns_cache):
            continue
        seen_urls.add(image_url)
        source_index = int(image.get("source_index", rank - 1))
        block_id = f"blk-image-{source_index + 1:04d}"
        resource_id = f"resource-{hashlib.sha256(image_url.encode()).hexdigest()[:16]}"
        selected.append({
            "image": image,
            "image_url": image_url,
            "block_id": block_id,
            "resource_id": resource_id,
            "source_index": source_index,
        })
    return selected, skipped_sensitive


def _goal_candidates(text: str, terms: list[str], limit: int = 5) -> list[dict]:
    if not terms:
        return []
    candidates = []
    cursor = 0
    for paragraph in re.split(r"\n{2,}", text):
        paragraph = paragraph.strip()
        if not paragraph:
            cursor += 2
            continue
        offset = text.find(paragraph, cursor)
        if offset < 0:
            offset = cursor
        score = _goal_score(paragraph, terms)
        if score:
            candidates.append({
                "offset": offset,
                "end_offset": offset + len(paragraph),
                "score": score,
                "snippet": paragraph[:MAX_LOCATOR_QUOTE_CHARS],
            })
        cursor = offset + len(paragraph)
    return sorted(candidates, key=lambda item: (-item["score"], item["offset"]))[:limit]


def _estimated_tokens(value: str | int) -> int:
    if isinstance(value, int):
        return max(0, (value + 3) // 4)
    non_ascii_count = sum(1 for character in value if ord(character) > 0x7F)
    ascii_count = len(value) - non_ascii_count
    return non_ascii_count + (ascii_count + 3) // 4


def _token_budget_details(request, returned_tokens: int, remaining_text: str) -> dict:
    response_limit = request.budget.max_response_tokens
    total_limit = request.budget.max_total_response_tokens
    if response_limit is None and total_limit is None:
        return {}
    limits = [int(value) for value in (response_limit, total_limit) if value is not None]
    return {
        "estimated_returned_tokens": returned_tokens,
        "estimated_remaining_tokens": _estimated_tokens(remaining_text),
        "estimated_token_basis": "ascii4_nonascii1_v1",
        "estimated_token_scope": "returned_material_text_only",
        "token_budget": response_limit,
        "total_token_budget": total_limit,
        "remaining_tokens": max(0, min(limits) - returned_tokens),
        "returned_tokens": returned_tokens,
    }


def _take_text_budgeted(
    text: str,
    offset: int,
    max_chars: int,
    max_tokens: int | None,
) -> tuple[str, int]:
    if max_tokens is None:
        return _take_text_range(text, offset, max_chars)
    if max_tokens <= 0 or max_chars <= 0:
        return "", offset
    high = min(len(text), offset + max_chars)
    ascii_count = 0
    non_ascii_count = 0
    budget_end = offset
    for index in range(offset, high):
        if ord(text[index]) > 0x7F:
            non_ascii_count += 1
        else:
            ascii_count += 1
        if non_ascii_count + (ascii_count + 3) // 4 > max_tokens:
            break
        budget_end = index + 1
    content, end = _take_text_range(text, offset, budget_end - offset)
    while content and _estimated_tokens(content) > max_tokens:
        content = content[:-1]
        end -= 1
    return content, end


def _unseen_ranges(start: int, end: int, returned_ranges: list[list[int]]) -> list[tuple[int, int]]:
    segments = [(start, end)]
    for returned_start, returned_end in returned_ranges:
        remaining = []
        for segment_start, segment_end in segments:
            if returned_end <= segment_start or returned_start >= segment_end:
                remaining.append((segment_start, segment_end))
                continue
            if segment_start < returned_start:
                remaining.append((segment_start, returned_start))
            if returned_end < segment_end:
                remaining.append((returned_end, segment_end))
        segments = remaining
        if not segments:
            break
    return segments


def _merge_ranges(ranges: list[list[int]]) -> list[list[int]]:
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _response_char_budget(request) -> int:
    return int(request.budget.max_response_chars)


def _source_text_was_truncated(values: list[str]) -> bool:
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(values)).strip()
    return len(text) > MAX_TEXT_CHARS


def _looks_like_access_challenge(title: str, text: str) -> bool:
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


def _read_with_isolated_browser(
    request: ReadRequest,
    source_url: str,
    dns_cache: dict[str, bool],
):
    from . import browser

    def user_action(url: str) -> None:
        print(
            "Envoy opened its isolated browser for "
            f"{_redact_url(url)}. Complete the normal login or verification "
            "action there; Envoy will resume automatically.",
            file=sys.stderr,
            flush=True,
        )

    try:
        page = browser.read_page(
            source_url,
            max_work_seconds=_remaining_work_seconds(request),
            on_user_action=user_action,
        )
    except MediaBudgetExceeded as exc:
        raise PublicPageError(str(exc), ErrorCode.BUDGET_EXCEEDED) from exc
    except browser.BrowserChallengeRequired as exc:
        return None, exc
    except (browser.BrowserRuntimeUnavailable, browser.BrowserReadError) as exc:
        return None, str(exc)
    except Exception as exc:
        return None, exc
    if not _is_public_http_url(page.url, dns_cache):
        return None, "The browser resolved the source to a non-public address"
    return page, None


def _browser_read_failure_code(error) -> ErrorCode:
    from .browser import BrowserChallengeRequired

    if isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError":
        return ErrorCode.NETWORK_ERROR
    return (
        ErrorCode.CHALLENGE_REQUIRED
        if isinstance(error, BrowserChallengeRequired)
        else ErrorCode.AUTH_REQUIRED
    )


def _browser_page_has_video(page, requested_kind: str) -> bool:
    path = urlparse(page.url).path.lower()
    return (
        requested_kind == "video"
        or bool(page.page_video_urls)
        or bool(re.search(r"/(?:video|watch|shorts?)/", path))
    )


def _url_is_video_page(value: str) -> bool:
    return bool(re.search(r"/(?:video|watch|shorts?)/", urlparse(value).path.lower()))


def _select_browser_video_url(
    candidates: list[str],
    dns_cache: dict[str, bool],
) -> tuple[str | None, int]:
    rejected = 0
    accepted = []
    seen = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not _is_public_http_url(value, dns_cache)
        ):
            rejected += 1
            continue
        accepted.append(value)
    return (accepted[0] if accepted else None), rejected


def _chapter_timestamp(value: str) -> float | None:
    pieces = value.split(":")
    try:
        numbers = [float(item) for item in pieces]
    except ValueError:
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def _page_video_chapter_blocks(
    text: str,
    source_url: str,
    *,
    first_order: int,
    duration: float | None,
) -> list[MaterialBlock]:
    chapters = []
    pattern = re.compile(
        r"(?m)^\s*(?P<time>(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d{1,3})?)\s+"
        r"(?P<label>\S[^\n]*)$"
    )
    for match in pattern.finditer(text):
        timestamp = _chapter_timestamp(match.group("time"))
        label = " ".join(match.group("label").split())
        if timestamp is None or not label:
            continue
        if duration is not None and timestamp >= duration:
            continue
        chapters.append((timestamp, label, match.group(0).strip()))
    chapters.sort(key=lambda item: item[0])
    blocks = []
    for index, (timestamp, label, quote) in enumerate(chapters):
        next_timestamp = chapters[index + 1][0] if index + 1 < len(chapters) else None
        end = next_timestamp if next_timestamp and next_timestamp > timestamp else timestamp + 1
        if duration is not None:
            end = min(end, duration)
        if end <= timestamp:
            end = timestamp + 0.001
        block_id = f"blk-video-chapter-{index + 1:03d}"
        blocks.append(MaterialBlock(
            id=block_id,
            kind=MaterialKind.VIDEO_SEGMENT,
            content=TextContent(type="text", text=label),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=VideoLocator(
                type="video_time",
                source_url=_redact_url(source_url),
                start_seconds=timestamp,
                end_seconds=end,
            ),
            order=first_order + index,
            quality=Quality(
                engine="page_chapter_metadata",
                unresolved=["Chapter text was extracted from the page, not verified against video playback"],
            ),
        ))
    return blocks


def _attach_browser_video_evidence(
    request: ReadRequest,
    source_url: str,
    media_urls: list[str],
    response: ReadResponse,
    *,
    duration_seconds: float | None = None,
    bound_media_urls: list[str] | None = None,
) -> ReadResponse:
    eligible_urls = list(dict.fromkeys(bound_media_urls or []))
    selected_url = None
    rejected_count = 0
    if len(eligible_urls) == 1:
        selected_url, rejected_count = _select_browser_video_url(eligible_urls, {})
    elif len(eligible_urls) > 1:
        rejected_count = len(eligible_urls)
    video_coverage = response.coverage.modalities.get("video") or CoverageItem(
        discovered=1,
        extraction="partial",
    )
    obsolete_video_gaps = {
        "Browser page was read, but video transcript and frames have not been extracted",
        "Embedded video was discovered but not processed",
    }
    obsolete_coverage_gaps = {
        "Embedded video resources were discovered but not processed",
        "The isolated browser read the video page only; no transcript or video-frame inspection was completed",
    }
    unresolved = [
        item for item in video_coverage.unresolved
        if item not in obsolete_video_gaps
    ]
    if selected_url is None:
        reason = (
            "Browser media URLs were private, malformed, or otherwise unsafe for local processing"
            if rejected_count
            else "Browser media could not be associated with the requested post because no unique page video URL was exposed"
        )
        video_coverage = video_coverage.model_copy(update={
            "discovered": max(int(video_coverage.discovered or 0), 1),
            "extraction": "partial",
            "unresolved": list(dict.fromkeys([*unresolved, reason])),
            "details": {
                **video_coverage.details,
                "browser_media_candidates": len(media_urls),
                "rejected_media_candidates": rejected_count,
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
        })
        modalities = dict(response.coverage.modalities)
        modalities["video"] = video_coverage
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": response.coverage.model_copy(update={"modalities": modalities}),
        })

    root = _material_root()
    media_dir = root / f"video-{response.material_id or 'browser'}"
    if media_dir.is_symlink() or (media_dir.exists() and not media_dir.is_dir()):
        video_coverage = video_coverage.model_copy(update={
            "extraction": "failed",
            "unresolved": list(dict.fromkeys([*unresolved, "Video staging path is not a regular directory"])),
        })
        modalities = dict(response.coverage.modalities)
        modalities["video"] = video_coverage
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": response.coverage.model_copy(update={"modalities": modalities}),
        })
    media_dir.mkdir(mode=0o700, exist_ok=True)

    duration = float(duration_seconds or 0)
    downloaded = None
    transcript_source = "unavailable"
    transcript_segments: list[dict] = []
    language = None
    asr_model = None
    asr_status = None
    asr_processed_until = None
    asr_elapsed_ms = None
    downloaded_bytes = None
    frame_response = response
    frame_enabled = (
        request.scope.include_visual
        and
        request.host_capabilities.video_input == "frames"
        and request.host_capabilities.image_input == ImageInputCapability.LOCAL_FILE
    )
    try:
        with tempfile.TemporaryDirectory(prefix="envoy-browser-video-") as temporary_dir:
            downloaded = download_video_for_frames(
                selected_url,
                temporary_dir,
                max_download_bytes=request.budget.max_download_bytes,
                max_work_seconds=_remaining_work_seconds(request),
            )
            video_path = Path(downloaded["path"]).resolve()
            if not video_path.is_file() or video_path.stat().st_size <= 0:
                raise MediaProcessingError("Browser video download did not produce a usable file")
            duration = duration or float(downloaded.get("duration") or 0)
            if duration <= 0:
                duration = probe_media_duration(
                    video_path,
                    max_work_seconds=_remaining_work_seconds(request),
                )
            downloaded_bytes = int(downloaded.get("downloaded_bytes") or video_path.stat().st_size)

            runtime = media_runtime_status()
            if runtime.get("asr_installed") and runtime.get("asr_model_available"):
                try:
                    asr_result = transcribe_audio_file(
                        video_path,
                        model_name=str(runtime["asr_model"]),
                        download_root=asr_model_cache_dir(),
                        max_work_seconds=_remaining_work_seconds(request),
                        start_seconds=request.scope.start_seconds or 0,
                        end_seconds=request.scope.end_seconds or duration,
                    )
                    transcript_segments = list(asr_result.get("segments") or [])
                    transcript_source = "asr" if transcript_segments else "no_speech"
                    language = asr_result.get("language")
                    asr_model = asr_result.get("model")
                    asr_status = asr_result.get("status")
                    asr_processed_until = asr_result.get("next_seconds")
                    asr_elapsed_ms = asr_result.get("elapsed_ms")
                    if not transcript_segments:
                        unresolved.append("ASR completed without speech segments")
                except MediaNoSpeech as exc:
                    transcript_source = "no_speech"
                    unresolved.append(str(exc))
                except (MediaBudgetExceeded, MediaCapabilityMissing, MediaProcessingError) as exc:
                    transcript_source = "failed"
                    unresolved.append(f"Local ASR did not complete ({exc})")
            elif not runtime.get("asr_installed"):
                unresolved.append("Local video ASR is unavailable because Whisper is not installed")
            else:
                unresolved.append("Local video ASR is unavailable because its Whisper model is not prepared")

            blocks = list(response.blocks)
            source_text = "\n".join(
                block.content.text
                for block in response.blocks
                if isinstance(block.content, TextContent)
            )
            chapter_blocks = _page_video_chapter_blocks(
                source_text,
                source_url,
                first_order=len(blocks),
                duration=duration or None,
            )
            blocks.extend(chapter_blocks)
            remaining_chars = max(
                0,
                request.budget.max_response_chars
                - sum(
                    len(block.content.text)
                    for block in blocks
                    if isinstance(block.content, TextContent)
                ),
            )
            asr_char_count = 0
            for index, cue in enumerate(transcript_segments[:200], start=1):
                cue_text = " ".join(str(cue.get("text") or "").split())
                try:
                    start = float(cue["start"])
                    end = float(cue["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not cue_text or start < 0 or end <= start:
                    continue
                if len(cue_text) > remaining_chars - asr_char_count:
                    unresolved.append("ASR transcript was truncated by the response character budget")
                    break
                asr_char_count += len(cue_text)
                blocks.append(MaterialBlock(
                    id=f"blk-video-asr-{index:04d}",
                    kind=MaterialKind.TRANSCRIPT,
                    content=TextContent(type="text", text=cue_text, language=language),
                    origin=MaterialOrigin.ASR,
                    locator=VideoLocator(
                        type="video_time",
                        source_url=_redact_url(source_url),
                        start_seconds=start,
                        end_seconds=end,
                    ),
                    parent_id=blocks[0].id if blocks else None,
                    order=len(blocks),
                    quality=Quality(engine=f"whisper-{asr_model or 'local'}"),
                ))

            persisted_transcript = None
            if transcript_segments:
                persisted_transcript = _video_subtitle_response(
                    request,
                    source_url,
                    {
                        "source": "asr",
                        "title": str(response.blocks[0].content.text)
                        if response.blocks
                        and isinstance(response.blocks[0].content, TextContent)
                        else request.source.label or "Video transcript",
                        "language": language,
                        "model": asr_model,
                        "transcription_status": asr_status,
                        "processed_until_seconds": asr_processed_until,
                        "cues": transcript_segments,
                    },
                )

            video_coverage = video_coverage.model_copy(update={
                "discovered": 1,
                "extracted": 1 if transcript_segments or frame_enabled else 0,
                "returned": 1 if transcript_segments or frame_enabled else 0,
                "processed": 1 if downloaded else 0,
                "extraction": "partial",
                "unresolved": list(dict.fromkeys(unresolved)),
                "details": {
                    **video_coverage.details,
                    "browser_media_url": _redact_url(selected_url),
                    "media_downloaded_bytes": downloaded_bytes,
                    "duration_seconds": duration or None,
                    "transcript_source": transcript_source,
                    "transcript_language": language,
                    "asr_model": asr_model,
                    "asr_duration_ms": asr_elapsed_ms,
                    "page_chapter_count": len(chapter_blocks),
                    "transcript_segment_count": len(transcript_segments),
                    "frame_inspection": "requested" if frame_enabled else "host_capability_missing",
                },
            })
            modalities = dict(response.coverage.modalities)
            modalities["video"] = video_coverage
            evidence_response = response.model_copy(update={
                "blocks": blocks,
                **(
                    {
                        "material_id": persisted_transcript.material_id,
                        "source_revision": persisted_transcript.source_revision,
                    }
                    if persisted_transcript is not None
                    else {}
                ),
                "coverage": response.coverage.model_copy(update={
                    "modalities": modalities,
                    "unresolved": [
                        item for item in response.coverage.unresolved
                        if item not in obsolete_coverage_gaps
                    ],
                }),
                "result": ReadResult.PARTIAL,
            })
            if frame_enabled:
                frame_response = _attach_video_frames(
                    request,
                    source_url,
                    evidence_response,
                    downloaded_media=downloaded,
                )
            else:
                video_coverage = video_coverage.model_copy(update={
                    "unresolved": list(dict.fromkeys([
                        *video_coverage.unresolved,
                        "The host did not declare local image input and frame inspection capability",
                    ])),
                })
                modalities["video"] = video_coverage
                frame_response = evidence_response.model_copy(update={
                    "coverage": evidence_response.coverage.model_copy(update={"modalities": modalities}),
                })
    except MediaBudgetExceeded as exc:
        unresolved.append(str(exc))
    except MediaUnsafeAddress:
        unresolved.append("A browser-captured video redirect resolved to a non-public address")
    except (MediaCapabilityMissing, MediaProcessingError) as exc:
        unresolved.append(f"Browser-captured video processing failed ({exc})")
    except (OSError, ValueError, TypeError) as exc:
        unresolved.append(f"Browser-captured video evidence could not be stored ({type(exc).__name__})")

    if downloaded is None:
        video_coverage = video_coverage.model_copy(update={
            "discovered": 1,
            "extraction": "partial",
            "unresolved": list(dict.fromkeys(unresolved)),
            "details": {
                **video_coverage.details,
                "browser_media_url": _redact_url(selected_url),
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
        })
        modalities = dict(response.coverage.modalities)
        modalities["video"] = video_coverage
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": response.coverage.model_copy(update={"modalities": modalities}),
        })

    if frame_response.result == ReadResult.NEEDS_HOST:
        return frame_response
    return frame_response.model_copy(update={
        "result": ReadResult.PARTIAL,
        "execution_state": ExecutionState.COMPLETED,
    })


def _take_text_range(text: str, offset: int, max_chars: int) -> tuple[str, int]:
    end = min(len(text), offset + max_chars)
    if end < len(text):
        boundary = max(
            text.rfind("\n", offset, end),
            text.rfind("。", offset, end),
            text.rfind("！", offset, end),
            text.rfind("？", offset, end),
            text.rfind(". ", offset, end),
            text.rfind("! ", offset, end),
            text.rfind("? ", offset, end),
        )
        if boundary >= offset + max_chars // 2:
            end = boundary + (2 if text[boundary:boundary + 2] in {". ", "! ", "? "} else 1)
    return text[offset:end], end


def _material_root() -> Path:
    configured = os.getenv("ENVOY_LOCAL_DATA_DIR", "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path.home() / ".envoy" / "materials"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    now = time.time()
    expiry_paths = sorted(
        root.glob("mat-*.expires"),
        key=lambda item: item.lstat().st_mtime,
        reverse=True,
    )
    valid_material_names = set()
    for index, expiry_path in enumerate(expiry_paths):
        try:
            if expiry_path.is_symlink():
                expiry_path.unlink(missing_ok=True)
                continue
            expires_at = float(expiry_path.read_text(encoding="ascii").strip())
            material_path = expiry_path.with_suffix(".json")
            if (
                now >= expires_at
                or index >= MAX_CACHED_MATERIALS
                or material_path.is_symlink()
                or not material_path.is_file()
            ):
                expiry_path.unlink(missing_ok=True)
                material_path.unlink(missing_ok=True)
                continue
            valid_material_names.add(material_path.name)
        except OSError:
            continue
        except ValueError:
            expiry_path.unlink(missing_ok=True)
    for material_path in root.glob("mat-*.json"):
        if material_path.name not in valid_material_names:
            try:
                material_path.unlink(missing_ok=True)
            except OSError:
                continue
    valid_material_ids = {
        name.removesuffix(".json")
        for name in valid_material_names
    }
    for frame_dir in root.glob("frames-mat-*"):
        match = re.fullmatch(r"frames-(mat-[a-f0-9]{24})", frame_dir.name)
        if not match:
            continue
        if frame_dir.is_symlink():
            frame_dir.unlink(missing_ok=True)
            continue
        if (
            match.group(1) not in valid_material_ids
            and frame_dir.is_dir()
            and frame_dir.resolve().parent == root.resolve()
        ):
            shutil.rmtree(frame_dir, ignore_errors=True)
    return root


def _material_id(
    source_url: str,
    source_revision: str,
    session_id: str | None,
    request_id: str,
) -> str:
    scope = f"session:{session_id}" if session_id is not None else f"request:{request_id}"
    identity = f"{source_url}\0{source_revision}\0{scope}"
    return f"mat-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _load_matching_material(material_id: str, source_revision: str, session_id: str | None) -> dict | None:
    root = _material_root()
    material_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if material_path.is_symlink() or expiry_path.is_symlink():
        return None
    try:
        data = json.loads(material_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    if expires_at <= time.time() or not isinstance(data, dict):
        return None
    if data.get("source_revision") != source_revision or data.get("session_id") != session_id:
        return None
    return data


def _source_coverage_unresolved(stored: dict) -> list[str]:
    values = stored.get("source_coverage_unresolved")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item) for item in values if str(item).strip()))


def _remaining_work_seconds(request: ReadRequest) -> int:
    deadline = _READ_WORK_DEADLINE.get()
    if deadline is None:
        return max(int(request.budget.max_work_seconds), 1)
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise MediaBudgetExceeded("The request max_work_seconds budget is exhausted")
    return remaining


@contextmanager
def _material_transaction(material_id: str):
    root = _material_root()
    lock_bucket = hashlib.sha256(material_id.encode()).hexdigest()[:2]
    try:
        with interprocess_file_lock(
            root / f".material-{lock_bucket}.lock",
            max_wait_seconds=30,
            busy_message="Material is busy; timed out waiting to update its evidence ledger",
        ):
            yield
    except TimeoutError:
        raise PublicPageError(
            "Material evidence ledger is busy; retry this operation",
            ErrorCode.BUDGET_ENFORCEMENT_UNAVAILABLE,
        ) from None


def _serialize_material_request(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        with _material_transaction(request.material_id):
            return function(request, *args, **kwargs)

    return wrapped


def _serialize_material_id_first(function):
    @wraps(function)
    def wrapped(material_id, *args, **kwargs):
        if not material_id:
            return function(material_id, *args, **kwargs)
        with _material_transaction(material_id):
            return function(material_id, *args, **kwargs)

    return wrapped


def _serialize_read_request(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        root = _material_root()
        request_scope = (
            f"session:{request.session_id}"
            if request.session_id is not None
            else f"request:{request.request_id}"
        )
        identity = f"{request.source.url}\0{request_scope}"
        lock_id = hashlib.sha256(identity.encode()).hexdigest()

        def execute():
            token = _READ_WORK_DEADLINE.set(
                time.monotonic() + max(int(request.budget.max_work_seconds), 1)
            )
            try:
                return function(request, *args, **kwargs)
            finally:
                _READ_WORK_DEADLINE.reset(token)

        try:
            with interprocess_file_lock(
                root / f".read-{lock_id[:2]}.lock",
                max_wait_seconds=request.budget.max_work_seconds,
                busy_message="This source is already being read in the current session",
            ):
                if not request.idempotency_key:
                    return execute()
                from .operations import OperationConflict, run_idempotent

                fingerprint_payload = request.model_dump(
                    mode="json",
                    exclude={"request_id", "session_id", "idempotency_key"},
                )
                fingerprint = hashlib.sha256(json.dumps(
                    fingerprint_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()).hexdigest()
                try:
                    payload = run_idempotent(
                        request.idempotency_key,
                        fingerprint=fingerprint,
                        execute=lambda: execute().model_dump(mode="json"),
                        ttl_seconds=min(
                            request.retention.derived_ttl_seconds,
                            MATERIAL_TTL_SECONDS,
                        ),
                    )
                    response = ReadResponse.model_validate(payload)
                    if response.material_id and response.source_revision:
                        cached_material = _load_matching_material(
                            response.material_id,
                            response.source_revision,
                            response.session_id,
                        )
                        if cached_material is None:
                            raise OperationConflict(
                                "Completed evidence expired or was evicted; use a new idempotency_key to reacquire it"
                            )
                    for resource in response.resources:
                        target = resource.delivery.path
                        if target and not Path(target).is_file():
                            raise OperationConflict(
                                "Completed evidence expired or was evicted; use a new idempotency_key to reacquire it"
                            )
                    return _rebind_cached_response_session(
                        response,
                        request.session_id,
                        request.request_id,
                    )
                except OperationConflict as exc:
                    code = (
                        ErrorCode.EXPIRED_RESOURCE
                        if "expired" in str(exc).lower() or "evicted" in str(exc).lower()
                        else ErrorCode.INVALID_REQUEST
                    )
                    return _blocked(request, code, str(exc))
        except TimeoutError:
            return _blocked(
                request,
                ErrorCode.BUDGET_EXCEEDED,
                "Timed out waiting for another read of this source in the same session",
            )

    return wrapped


def _cached_material_read_response(
    request: ReadRequest,
    material_id: str,
    source_revision: str,
    stored: dict,
    *,
    modality: str = "text",
) -> ReadResponse:
    title = str(stored.get("title_text") or request.source.label or "Cached material")
    title_block = MaterialBlock(
        id="blk-title",
        kind=MaterialKind.HEADING,
        content=TextContent(type="text", text=title),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(
            type="web",
            source_url=str(stored.get("source_url") or request.source.url),
            block_id="blk-title",
            quote=title[:MAX_LOCATOR_QUOTE_CHARS],
        ),
        order=0,
    )
    state = stored.get("budget_state") if isinstance(stored.get("budget_state"), dict) else {}
    returned_chars = int(state.get("returned_chars") or 0)
    returned_tokens = int(state.get("returned_tokens") or 0)
    source_unresolved = [
        str(item) for item in stored.get("source_coverage_unresolved", [])
        if str(item).strip()
    ]
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
        blocks=[title_block],
                coverage=Coverage(
                    modalities={
                modality: CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=0,
                    extraction="partial",
                    unresolved=["This source revision was already returned in this session; use envoy_search for another goal."],
                    details={
                        "already_returned": True,
                        "next_operation": "envoy_search",
                        "returned_chars": returned_chars,
                        "returned_tokens": returned_tokens,
                        "remaining_chars": max(
                            0,
                            int(state.get("max_total_response_chars") or request.budget.max_total_response_chars)
                            - returned_chars,
                        ),
                        "remaining_tokens": (
                            max(0, int(state["max_total_response_tokens"]) - returned_tokens)
                            if state.get("max_total_response_tokens") is not None
                            else None
                        ),
                    },
                )
            },
            unresolved=source_unresolved,
        ),
    )


def _rebind_cached_response_session(
    response: ReadResponse,
    session_id: str | None,
    request_id: str,
) -> ReadResponse:
    if response.session_id == session_id:
        return response.model_copy(update={"request_id": request_id})
    if not response.material_id or not response.source_revision:
        return response.model_copy(update={
            "request_id": request_id,
            "session_id": session_id,
        })
    original = _load_matching_material(
        response.material_id,
        response.source_revision,
        response.session_id,
    )
    if original is None:
        raise PublicPageError(
            "Completed material expired or was evicted; start a new operation to reacquire it",
            ErrorCode.EXPIRED_RESOURCE,
        )
    remaining_ttl = max(
        0,
        int(float(original.get("expires_at") or time.time()) - time.time()),
    )
    rebound_id = _material_id(
        str(original.get("source_url") or ""),
        response.source_revision,
        session_id,
        request_id,
    )
    rebound_material = {
        **original,
        "session_id": session_id,
        "expires_at": time.time() + min(remaining_ttl, MATERIAL_TTL_SECONDS),
    }
    rebound_resources = []
    stored_resources = []
    root = _material_root().resolve()
    rebound_frame_dir = root / f"frames-{rebound_id}"
    if rebound_frame_dir.is_symlink() or (
        rebound_frame_dir.exists() and not rebound_frame_dir.is_dir()
    ):
        raise PublicPageError(
            "The rebound local frame lease path is not a regular Envoy directory",
            ErrorCode.INVALID_REQUEST,
        )
    staging_frame_dir = None
    if any(resource.delivery.type == "local_file" for resource in response.resources):
        staging_frame_dir = Path(tempfile.mkdtemp(prefix=".frames-rebind-", dir=root))
    for resource in response.resources:
        delivery = resource.delivery
        if delivery.type != "local_file" or not delivery.path:
            rebound_resources.append(resource)
            continue
        source_path = Path(delivery.path).expanduser().resolve()
        try:
            relative = source_path.relative_to(root)
        except ValueError:
            raise PublicPageError(
                "Completed local evidence is outside Envoy's material lease; reacquire it",
                ErrorCode.EXPIRED_RESOURCE,
            ) from None
        if not relative.parts or not relative.parts[0].startswith("frames-mat-"):
            raise PublicPageError(
                "Completed local evidence is not an Envoy-managed frame lease; reacquire it",
                ErrorCode.EXPIRED_RESOURCE,
            )
        if not source_path.is_file() or source_path.is_symlink():
            raise PublicPageError(
                "Completed local frame evidence expired or was evicted; reacquire it",
                ErrorCode.EXPIRED_RESOURCE,
            )
        staged_target = staging_frame_dir / source_path.name
        if not staged_target.exists():
            shutil.copyfile(source_path, staged_target)
            staged_target.chmod(0o600)
        target = rebound_frame_dir / source_path.name
        rebound_resources.append(resource.model_copy(update={
            "delivery": delivery.model_copy(update={"path": str(target)}),
        }))
        stored_resources.append({
            "resource_id": resource.resource_id,
            "source_block_id": next((
                str(item.get("source_block_id") or "")
                for item in original.get("visual_resources", [])
                if isinstance(item, dict)
                and item.get("resource_id") == resource.resource_id
            ), ""),
            "source_url": str(response.blocks[0].locator.source_url)
            if response.blocks and response.blocks[0].locator
            else str(original.get("source_url") or ""),
            "frame_path": str(target),
        })
    if stored_resources:
        rebound_material["visual_resources"] = stored_resources
    _store_material(rebound_id, rebound_material)
    if staging_frame_dir is not None:
        rebound_frame_dir.mkdir(mode=0o700, exist_ok=True)
        for staged in staging_frame_dir.iterdir():
            target = rebound_frame_dir / staged.name
            if not target.exists():
                os.replace(staged, target)
        staging_frame_dir.rmdir()
    return response.model_copy(update={
        "request_id": request_id,
        "session_id": session_id,
        "material_id": rebound_id,
        "resources": rebound_resources,
    })


def _store_material(material_id: str, data: dict) -> None:
    with _material_transaction(material_id):
        _store_material_unlocked(material_id, data)


def _store_material_unlocked(material_id: str, data: dict) -> None:
    root = _material_root()
    target = root / f"{material_id}.json"
    expiry_target = root / f"{material_id}.expires"
    data = {
        **data,
        "expires_at": float(data.get("expires_at") or time.time() + MATERIAL_TTL_SECONDS),
    }
    if target.is_file() and not target.is_symlink():
        try:
            previous = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if isinstance(previous, dict) and previous.get("source_revision") == data.get("source_revision"):
            previous_resources = previous.get("visual_resources")
            new_resources = data.get("visual_resources")
            if isinstance(previous_resources, list) or isinstance(new_resources, list):
                merged_resources = {}
                for item in [*(previous_resources or []), *(new_resources or [])]:
                    if isinstance(item, dict) and item.get("resource_id"):
                        merged_resources[str(item["resource_id"])] = item
                data["visual_resources"] = list(merged_resources.values())

            previous_budget = previous.get("budget_state")
            new_budget = data.get("budget_state")
            if isinstance(previous_budget, dict) and isinstance(new_budget, dict):
                old_ranges = _material_returned_ranges(
                    previous_budget,
                    max(len(str(previous.get("text") or "")), len(str(data.get("text") or ""))),
                )
                new_ranges = _material_returned_ranges(
                    new_budget,
                    max(len(str(previous.get("text") or "")), len(str(data.get("text") or ""))),
                )
                merged_ranges = _merge_ranges(old_ranges + new_ranges)
                new_budget["returned_ranges"] = merged_ranges
                merged_text = str(data.get("text") or previous.get("text") or "")
                range_chars = sum(end - start for start, end in merged_ranges)
                range_tokens = sum(
                    _estimated_tokens(merged_text[start:end])
                    for start, end in merged_ranges
                )
                title_tokens = (
                    _estimated_tokens(str(data.get("title_text") or previous.get("title_text") or ""))
                    if merged_ranges
                    else 0
                )
                new_budget["returned_chars"] = max(
                    int(previous_budget.get("returned_chars") or 0),
                    int(new_budget.get("returned_chars") or 0),
                    range_chars,
                )
                new_budget["returned_tokens"] = max(
                    int(previous_budget.get("returned_tokens") or 0),
                    int(new_budget.get("returned_tokens") or 0),
                    title_tokens + range_tokens,
                )
                new_budget["expansion_rounds"] = max(
                    int(previous_budget.get("expansion_rounds") or 0),
                    int(new_budget.get("expansion_rounds") or 0),
                )
                for limit_name in ("max_total_response_chars", "max_total_response_tokens", "max_expansion_rounds"):
                    old_limit = previous_budget.get(limit_name)
                    new_limit = new_budget.get(limit_name)
                    if old_limit is not None and new_limit is not None:
                        new_budget[limit_name] = min(int(old_limit), int(new_limit))
                    elif old_limit is not None:
                        new_budget[limit_name] = old_limit
            source_gaps = list(previous.get("source_coverage_unresolved") or [])
            source_gaps.extend(data.get("source_coverage_unresolved") or [])
            if source_gaps:
                data["source_coverage_unresolved"] = list(dict.fromkeys(map(str, source_gaps)))

        if (
            isinstance(previous, dict)
            and previous.get("source_revision") == data.get("source_revision")
            and isinstance(previous.get("visual_observations"), list)
        ):
            allowed = {
                (
                    str(item.get("resource_id") or ""),
                    str(item.get("source_block_id") or ""),
                    _visual_source_identity(str(item.get("source_url") or "")),
                )
                for item in data.get("visual_resources", [])
                if isinstance(item, dict)
            }
            candidates = [
                item
                for item in previous["visual_observations"]
                if isinstance(item, dict)
                and (
                    str(item.get("resource_id") or ""),
                    str(item.get("source_block_id") or ""),
                    _visual_source_identity(str(item.get("source_url") or "")),
                ) in allowed
            ]
            candidates.extend(
                item for item in data.get("visual_observations", [])
                if isinstance(item, dict)
            )
            merged = {}
            for item in candidates:
                merged[(item.get("resource_id"), item.get("source_block_id"))] = item
            if merged:
                data["visual_observations"] = list(merged.values())[-32:]
    descriptor, temporary_path = tempfile.mkstemp(prefix=".material-", dir=str(root))
    expiry_descriptor, expiry_temporary_path = tempfile.mkstemp(prefix=".expiry-", dir=str(root))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        target.chmod(0o600)
        with os.fdopen(expiry_descriptor, "w", encoding="ascii") as handle:
            handle.write(str(data["expires_at"]))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(expiry_temporary_path, 0o600)
        os.replace(expiry_temporary_path, expiry_target)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        if os.path.exists(expiry_temporary_path):
            os.unlink(expiry_temporary_path)


def _expand_blocked(
    request: ExpandRequest,
    message: str,
    stored: dict | None = None,
) -> ReadResponse:
    source_unresolved = _source_coverage_unresolved(stored or {})
    return ReadResponse(
        request_id=request.request_id,
        material_id=request.material_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[ReadError(code=ErrorCode.BUDGET_EXCEEDED, message=message)],
        coverage=Coverage(unresolved=source_unresolved),
    )


def _material_budget_state(stored: dict, request: ExpandRequest) -> dict:
    state = stored.get("budget_state")
    if not isinstance(state, dict):
        state = {}
    source_text = str(stored.get("text") or "")
    ranges = _material_returned_ranges(state, len(source_text))
    if state.get("returned_tokens") is not None:
        returned_tokens = int(state["returned_tokens"])
    else:
        returned_tokens = _estimated_tokens(str(stored.get("title_text") or ""))
        returned_tokens += sum(
            _estimated_tokens(source_text[start:min(end, len(source_text))])
            for start, end in ranges
        )
    return {
        "max_total_response_chars": min(
            int(state.get("max_total_response_chars") or request.max_total_response_chars),
            request.max_total_response_chars,
        ),
        "max_total_response_tokens": (
            min(
                int(state["max_total_response_tokens"]),
                int(request.max_total_response_tokens),
            )
            if state.get("max_total_response_tokens") is not None
            and request.max_total_response_tokens is not None
            else int(state["max_total_response_tokens"])
            if state.get("max_total_response_tokens") is not None
            else request.max_total_response_tokens
        ),
        "max_expansion_rounds": (
            int(state["max_expansion_rounds"])
            if state.get("max_expansion_rounds") is not None
            else request.max_expansion_rounds
        ),
        "returned_chars": int(state.get("returned_chars") or 0),
        "returned_tokens": returned_tokens,
        "expansion_rounds": int(state.get("expansion_rounds") or 0),
        "returned_ranges": ranges,
    }


def _material_returned_ranges(state: dict, text_length: int) -> list[list[int]]:
    ranges = []
    for field in ("returned_ranges", "search_returned_ranges"):
        for item in state.get(field) or []:
            if not isinstance(item, list) or len(item) != 2:
                continue
            try:
                start, end = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if 0 <= start < end <= text_length:
                ranges.append([start, end])
    return _merge_ranges(ranges)


def _record_material_range(stored: dict, state: dict, start: int, end: int, content: str) -> None:
    state["returned_ranges"] = _merge_ranges(state["returned_ranges"] + [[start, end]])
    state["returned_chars"] += end - start
    state["returned_tokens"] += _estimated_tokens(content)
    state["expansion_rounds"] += 1
    stored["budget_state"] = state


@_serialize_material_id_first
def record_visual_observation(
    material_id: str | None,
    source_url: str,
    resource_id: str,
    source_block_id: str,
    observation: str,
    locator_quote: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Persist a bounded host observation without exposing raw image bytes."""
    if not material_id:
        return {"persisted": False, "reason": "material_id_missing"}
    root = _material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        return {"persisted": False, "reason": "material_unavailable"}
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError):
        return {"persisted": False, "reason": "material_unavailable"}
    if expires_at <= time.time():
        return {"persisted": False, "reason": "material_unavailable"}
    if stored.get("session_id") and session_id != stored.get("session_id"):
        return {"persisted": False, "reason": "session_mismatch"}
    expected_resources = stored.get("visual_resources")
    expected = next(
        (
            item for item in expected_resources
            if isinstance(item, dict)
            and str(item.get("resource_id") or "") == resource_id
            and str(item.get("source_block_id") or "") == source_block_id
        ),
        None,
    ) if isinstance(expected_resources, list) else None
    safe_source_url = _redact_url(source_url)
    if (
        expected is None
        or _visual_source_identity(str(expected.get("source_url") or ""))
        != _visual_source_identity(safe_source_url)
    ):
        return {"persisted": False, "reason": "resource_mismatch"}
    observations = stored.get("visual_observations")
    if not isinstance(observations, list):
        observations = []
    record = {
        "source_url": safe_source_url,
        "resource_id": resource_id,
        "source_block_id": source_block_id,
        "observation": observation,
        "quote": locator_quote or observation[:MAX_LOCATOR_QUOTE_CHARS],
        "created_at": int(time.time()),
    }
    observations = [
        item for item in observations
        if not (
            isinstance(item, dict)
            and item.get("resource_id") == resource_id
            and item.get("source_block_id") == source_block_id
        )
    ]
    observations.append(record)
    stored["visual_observations"] = observations[-32:]
    try:
        _store_material(material_id, stored)
    except OSError:
        return {"persisted": False, "reason": "storage_unavailable"}
    return {"persisted": True, "observation_count": len(stored["visual_observations"])}


@_serialize_material_request
def read_visual_observations(request: VisualObservationHistoryRequest) -> ReadResponse:
    material_id = request.material_id
    root = _material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        raise PublicPageError("Material is unavailable", ErrorCode.EXPIRED_RESOURCE)
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE) from exc
    if expires_at <= time.time():
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE)
    if stored.get("session_id") and request.session_id != stored.get("session_id"):
        raise PublicPageError("Material belongs to a different session", ErrorCode.INVALID_REQUEST)
    source_unresolved = _source_coverage_unresolved(stored)

    rows = stored.get("visual_observations")
    if not isinstance(rows, list):
        rows = []
    if request.resource_id:
        rows = [
            item for item in rows
            if isinstance(item, dict) and item.get("resource_id") == request.resource_id
        ]
    def observation_timestamp(item: dict) -> int:
        try:
            return int(item.get("created_at") or 0)
        except (TypeError, ValueError):
            return 0

    rows = sorted(
        (item for item in rows if isinstance(item, dict)),
        key=observation_timestamp,
        reverse=True,
    )
    blocks = []
    consumed_chars = 0
    truncated = False
    for index, item in enumerate(rows[:request.max_items], start=1):
        remaining = request.max_chars - consumed_chars
        if remaining <= 0:
            truncated = True
            break
        content = str(item.get("observation") or "")
        if len(content) > remaining:
            content = content[:remaining]
            truncated = True
        if not content:
            continue
        resource_id = str(item.get("resource_id") or "")
        source_block_id = str(item.get("source_block_id") or "")
        source_url = _redact_url(str(item.get("source_url") or ""))
        blocks.append(MaterialBlock(
            id=f"{source_block_id}-observation-{index}",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=content),
            origin=MaterialOrigin.HOST_VISUAL,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id=source_block_id,
                quote=str(item.get("quote") or content[:MAX_LOCATOR_QUOTE_CHARS]),
            ),
            parent_id=source_block_id,
            order=index - 1,
            relations=[{
                "type": "derived_from",
                "target_id": source_block_id,
                "basis": "host_observation",
            }],
            quality={
                "engine": "host_visual_observation",
                "unresolved": ["Untrusted host-reported visual evidence"],
            },
        ))
        consumed_chars += len(content)
    if len(rows) > len(blocks):
        truncated = True

    return ReadResponse(
        request_id=request.request_id,
        session_id=stored.get("session_id"),
        material_id=material_id,
        source_revision=str(stored.get("source_revision") or ""),
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if truncated or source_unresolved else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={
            "visual_observations": CoverageItem(
                discovered=len(rows),
                extracted=len(rows),
                returned=len(blocks),
                observed=len(blocks),
                extraction="partial" if truncated else "complete",
                unresolved=["Visual observation history was limited by the request budget"] if truncated else [],
                details={
                    "max_items": request.max_items,
                    "max_chars": request.max_chars,
                    "returned_chars": consumed_chars,
                    "resource_id": request.resource_id,
                    "untrusted": True,
                },
            )
        }, unresolved=source_unresolved),
    )


_ENGLISH_SEARCH_STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "can", "could",
    "did", "do", "does", "doing", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here",
    "hers", "herself", "him", "himself", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "itself", "me", "more", "most", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "our", "ours", "ourselves", "out", "over", "own", "same",
    "she", "should", "so", "some", "such", "than", "that", "the", "their",
    "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "whom", "why", "will", "with", "would", "you", "your", "yours",
    "yourself", "yourselves",
}


def _material_search_terms(query: str) -> list[str]:
    terms = [
        term for term in _goal_terms(query)
        if not (term.isascii() and term.isalpha() and term in _ENGLISH_SEARCH_STOP_WORDS)
    ]
    cjk_sequences = re.findall(
        r"[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af\U00020000-\U0002FA1F]+",
        query,
    )
    for sequence in cjk_sequences:
        if len(sequence) >= 2 and sequence not in terms:
            terms.append(sequence)
        for index in range(len(sequence) - 1):
            pair = sequence[index:index + 2]
            if pair not in terms:
                terms.append(pair)
    return terms[:64]


def _search_hit_offset(text: str, terms: list[str]) -> int:
    haystack = text.lower()
    offsets = [
        position
        for term in terms
        if term and (position := haystack.find(term.lower())) >= 0
    ]
    return min(offsets, default=0)


@_serialize_material_request
def search_material(request: MaterialSearchRequest) -> ReadResponse:
    material_id = request.material_id
    root = _material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        raise PublicPageError("Material is unavailable", ErrorCode.EXPIRED_RESOURCE)
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE) from exc
    if expires_at <= time.time():
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE)
    if stored.get("session_id") and request.session_id != stored.get("session_id"):
        raise PublicPageError("Material belongs to a different session", ErrorCode.INVALID_REQUEST)

    total_text = str(stored.get("text") or "")
    source_unresolved = _source_coverage_unresolved(stored)
    state = stored.get("budget_state") if isinstance(stored.get("budget_state"), dict) else {}
    returned_ranges = _material_returned_ranges(state, len(total_text))
    terms = _material_search_terms(request.query)
    candidates = []
    lexical_matches = 0
    already_returned_matches = 0
    for match in re.finditer(r"[^\n]+", total_text):
        raw_line = match.group(0)
        snippet = raw_line.strip()
        if not snippet:
            continue
        start = match.start() + len(raw_line) - len(raw_line.lstrip())
        score = _goal_score(snippet, terms)
        if score:
            lexical_matches += 1
            end = match.end() - (len(raw_line) - len(raw_line.rstrip()))
            segments = _unseen_ranges(
                start,
                end,
                returned_ranges,
            )
            if not segments:
                already_returned_matches += 1
            else:
                preserve_line_context = stored.get("source_kind") == "video"
                candidate_start = start if preserve_line_context else segments[0][0]
                candidate_end = end if preserve_line_context else segments[0][1]
                segment_text = total_text[candidate_start:candidate_end]
                segment_score = _goal_score(segment_text, terms)
                if not segment_score:
                    already_returned_matches += 1
                else:
                    candidates.append({
                        "start": candidate_start,
                        "end": candidate_end,
                        "text": segment_text,
                        "score": segment_score,
                        "hit_offset": _search_hit_offset(segment_text, terms),
                        "unseen_segments": segments,
                    })
    candidates.sort(key=lambda item: (-item["score"], item["start"]))
    matches_found = lexical_matches
    new_matches = len(candidates)

    max_total_chars = int(state.get("max_total_response_chars") or 48_000)
    returned_chars = int(state.get("returned_chars") or 0)
    returned_tokens = int(state.get("returned_tokens") or 0)
    if "returned_tokens" not in state and "returned_ranges" in state:
        returned_tokens = _estimated_tokens(str(stored.get("title_text") or ""))
        returned_tokens += sum(
            _estimated_tokens(total_text[start:end])
            for start, end in _material_returned_ranges(state, len(total_text))
        )
    max_total_tokens = state.get("max_total_response_tokens")
    max_total_tokens = int(max_total_tokens) if max_total_tokens is not None else None
    remaining_chars = max(0, max_total_chars - returned_chars)
    remaining_tokens = (
        max(0, max_total_tokens - returned_tokens)
        if max_total_tokens is not None
        else None
    )
    response_tokens = request.max_response_tokens
    if remaining_tokens is not None:
        response_tokens = min(response_tokens, remaining_tokens) if response_tokens is not None else remaining_tokens
    char_limit = min(request.max_chars, remaining_chars)
    if not candidates:
        return ReadResponse(
            request_id=request.request_id,
            session_id=stored.get("session_id"),
            material_id=material_id,
            source_revision=str(stored.get("source_revision") or ""),
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.EMPTY,
            coverage=Coverage(modalities={
                "text": CoverageItem(
                    discovered=len(re.findall(r"[^\n]+", total_text)),
                    extracted=len(re.findall(r"[^\n]+", total_text)),
                    returned=0,
                    extraction="complete",
                    details={
                        "query_terms": terms,
                        "matches_found": matches_found,
                        "already_returned_matches": already_returned_matches,
                        "remaining_chars": remaining_chars,
                        "remaining_tokens": remaining_tokens,
                    },
                )
            }, unresolved=source_unresolved),
        )
    if char_limit <= 0 or response_tokens == 0:
        return _expand_blocked(
            ExpandRequest(
                request_id=request.request_id,
                material_id=material_id,
            ),
            "The cumulative material response budget has been exhausted",
            stored,
        )

    blocks = []
    used_chars = 0
    used_tokens = 0
    truncated = new_matches > request.max_results
    for candidate in candidates[:request.max_results]:
        available_chars = char_limit - used_chars
        available_tokens = (
            response_tokens - used_tokens
            if response_tokens is not None
            else None
        )
        if available_chars <= 0 or available_tokens == 0:
            truncated = True
            break
        context_limit = min(48, available_chars // 3)
        if available_tokens is not None:
            context_limit = min(context_limit, available_tokens * 4 // 3)
        if (
            stored.get("source_kind") == "video"
            and len(candidate["text"]) <= available_chars
        ):
            window_start = 0
        else:
            window_start = max(0, candidate["hit_offset"] - context_limit)
        content, _ = _take_text_budgeted(
            candidate["text"][window_start:],
            0,
            available_chars,
            available_tokens,
        )
        if not content:
            truncated = True
            break
        start = candidate["start"] + window_start
        end = start + len(content)
        if stored.get("source_kind") == "pdf":
            span = next(
                (
                    item for item in stored.get("page_spans", [])
                    if isinstance(item, dict)
                    and int(item.get("start") or 0) <= start < int(item.get("end") or 0)
                ),
                None,
            )
            locator = PdfLocator(
                type="pdf_page",
                page_number=int(span["page_number"]) if span else 1,
            )
        elif stored.get("source_kind") == "video":
            locator = _video_locator_for_range(
                str(stored.get("source_url") or ""),
                stored.get("video_spans") if isinstance(stored.get("video_spans"), list) else [],
                start,
                end,
            )
            if locator is None:
                locator = WebLocator(
                    type="web",
                    source_url=str(stored.get("source_url") or ""),
                    block_id="blk-video-transcript",
                    quote=content[:MAX_LOCATOR_QUOTE_CHARS],
                )
        else:
            locator = WebLocator(
                type="web",
                source_url=str(stored.get("source_url") or ""),
                block_id="blk-body-0001",
                quote=content[:MAX_LOCATOR_QUOTE_CHARS],
            )
        blocks.append(MaterialBlock(
            id=f"blk-search-{len(blocks) + 1:04d}",
            kind=MaterialKind.TRANSCRIPT if stored.get("source_kind") == "video" else MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=content),
            origin=(
                MaterialOrigin.ASR
                if stored.get("source_kind") == "video" and stored.get("transcript_source") == "asr"
                else MaterialOrigin.SUBTITLE
                if stored.get("source_kind") == "video"
                else MaterialOrigin.SOURCE_TEXT
            ),
            locator=locator,
            order=start,
            quality={
                "engine": "goal_keyword_search",
                "engine_score": min(1.0, candidate["score"] / max(len(terms), 1)),
                "unresolved": ["Lexical relevance is a retrieval hint, not semantic verification"],
            },
        ))
        new_segments = _unseen_ranges(start, end, returned_ranges)
        used_chars += sum(segment_end - segment_start for segment_start, segment_end in new_segments)
        used_tokens += sum(
            _estimated_tokens(total_text[segment_start:segment_end])
            for segment_start, segment_end in new_segments
        )
        if len(content) < len(candidate["text"]):
            truncated = True
            break

    if not blocks:
        return _expand_blocked(
            ExpandRequest(request_id=request.request_id, material_id=material_id),
            "The requested search results do not fit the remaining material budget",
            stored,
        )
    state["returned_chars"] = returned_chars + used_chars
    state["returned_tokens"] = returned_tokens + used_tokens
    search_ranges = returned_ranges.copy()
    for block in blocks:
        search_ranges.extend(
            [
                [segment_start, segment_end]
                for segment_start, segment_end in _unseen_ranges(
                block.order,
                block.order + len(block.content.text),
                returned_ranges,
                )
            ]
        )
    state["returned_ranges"] = _merge_ranges(search_ranges)
    state["search_returned_ranges"] = state["returned_ranges"]
    stored["budget_state"] = state
    try:
        _store_material(material_id, stored)
    except OSError:
        return _expand_blocked(
            ExpandRequest(request_id=request.request_id, material_id=material_id),
            "The material budget ledger could not be persisted",
            stored,
        )

    return ReadResponse(
        request_id=request.request_id,
        session_id=stored.get("session_id"),
        material_id=material_id,
        source_revision=str(stored.get("source_revision") or ""),
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if truncated or source_unresolved else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(
            modalities={
                "text": CoverageItem(
                    discovered=len(re.findall(r"[^\n]+", total_text)),
                    extracted=len(re.findall(r"[^\n]+", total_text)),
                    returned=len(blocks),
                    extraction="partial" if truncated else "complete",
                    unresolved=[
                        "Search results were limited by result or response budgets"
                    ] if truncated else [],
                    details={
                        "query_terms": terms,
                        "matches_found": matches_found,
                        "already_returned_matches": already_returned_matches,
                        "returned_chars": used_chars,
                        "returned_tokens": used_tokens,
                        "match_offsets": [block.order for block in blocks],
                        "token_estimate_basis": "ascii4_nonascii1_v1",
                        "token_estimate_scope": "returned_material_text_only",
                        "remaining_chars": max(0, max_total_chars - state["returned_chars"]),
                        "remaining_tokens": (
                            max(0, max_total_tokens - state["returned_tokens"])
                            if max_total_tokens is not None
                            else None
                        ),
                    },
            )
        }, unresolved=source_unresolved),
    )


@_serialize_material_request
def expand_material(request: ExpandRequest) -> ReadResponse:
    material_id = request.material_id
    root = _material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        raise PublicPageError("Material is unavailable", ErrorCode.EXPIRED_RESOURCE)
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE)
    if expires_at <= time.time():
        source_path.unlink(missing_ok=True)
        expiry_path.unlink(missing_ok=True)
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE)
    if stored.get("session_id") and request.session_id != stored.get("session_id"):
        raise PublicPageError("Material belongs to a different session", ErrorCode.INVALID_REQUEST)
    source_unresolved = _source_coverage_unresolved(stored)
    budget_state = _material_budget_state(stored, request)
    if (
        budget_state["max_expansion_rounds"] is not None
        and budget_state["expansion_rounds"] >= budget_state["max_expansion_rounds"]
    ):
        return _expand_blocked(request, "The material expansion round budget has been exhausted", stored)
    if budget_state["returned_chars"] >= budget_state["max_total_response_chars"]:
        return _expand_blocked(request, "The cumulative response character budget has been exhausted", stored)
    if (
        budget_state["max_total_response_tokens"] is not None
        and budget_state["returned_tokens"] >= budget_state["max_total_response_tokens"]
    ):
        return _expand_blocked(request, "The cumulative response token budget has been exhausted", stored)
    total_text = str(stored.get("text") or "")
    all_returned_ranges = _material_returned_ranges(
        stored.get("budget_state") if isinstance(stored.get("budget_state"), dict) else {},
        len(total_text),
    )
    offset = request.offset
    max_chars = min(
        request.max_chars,
        int(request.max_response_tokens) * 4
        if request.max_response_tokens is not None
        else request.max_chars,
    )
    if offset > len(total_text):
        raise ValueError("offset must be between zero and the material length")
    end = min(
        len(total_text),
        offset + max_chars,
        offset + max(0, budget_state["max_total_response_chars"] - budget_state["returned_chars"]),
    )
    if budget_state["max_total_response_tokens"] is not None:
        token_remaining_chars = max(
            0,
            (budget_state["max_total_response_tokens"] - budget_state["returned_tokens"]) * 4,
        )
        end = min(end, offset + token_remaining_chars)
    response_token_limit = request.max_response_tokens
    total_token_remaining = (
        max(0, budget_state["max_total_response_tokens"] - budget_state["returned_tokens"])
        if budget_state["max_total_response_tokens"] is not None
        else None
    )
    if total_token_remaining is not None:
        response_token_limit = (
            min(response_token_limit, total_token_remaining)
            if response_token_limit is not None
            else total_token_remaining
        )
    if _unseen_ranges(offset, end, all_returned_ranges) != [(offset, end)]:
        return _expand_blocked(request, "The requested material range overlaps an earlier response", stored)
    if stored.get("source_kind") == "pdf":
        spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
        span = next(
            (
                item for item in spans
                if isinstance(item, dict) and int(item.get("end") or 0) > offset
            ),
            None,
        )
        if span is None:
            raise PublicPageError("No more PDF page text is available", ErrorCode.EXPIRED_RESOURCE)
        span_start = int(span.get("start") or 0)
        span_end = int(span.get("end") or 0)
        page_offset = max(offset, span_start)
        if end <= page_offset:
            raise PublicPageError(
                "The cumulative response character budget has been reached",
                ErrorCode.BUDGET_EXCEEDED,
            )
        content, page_end = _take_text_budgeted(
            total_text,
            page_offset,
            min(end, span_end) - page_offset,
            response_token_limit,
        )
        _record_material_range(stored, budget_state, page_offset, page_end, content)
        try:
            _store_material(material_id, stored)
        except OSError:
            return _expand_blocked(request, "The material budget ledger could not be persisted", stored)
        has_more = any(
            isinstance(item, dict) and int(item.get("end") or 0) > page_end
            for item in spans
        )
        page_number = int(span["page_number"])
        block = MaterialBlock(
            id=f"blk-pdf-page-{page_number:04d}-range-{page_offset}",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=content),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=PdfLocator(type="pdf_page", page_number=page_number),
            order=page_offset,
        )
        return ReadResponse(
            request_id=request.request_id,
            session_id=stored.get("session_id"),
            material_id=material_id,
            source_revision=str(stored["source_revision"]),
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL if has_more or source_unresolved else ReadResult.READY,
            blocks=[block],
            coverage=Coverage(modalities={
                "text": CoverageItem(
                    discovered=len(spans),
                    extracted=len(spans),
                    returned=1,
                    extraction="partial" if has_more else "complete",
                    unresolved=["More PDF text is available"] if has_more else [],
                    details={
                        "offset": page_offset,
                        "returned_chars": len(content),
                        "total_chars": len(total_text),
                        "next_offset": page_end if has_more else None,
                        "page_number": page_number,
                        "continuation_available": has_more,
                        "remaining_chars": max(0, budget_state["max_total_response_chars"] - budget_state["returned_chars"]),
                        "expansion_rounds": budget_state["expansion_rounds"],
                        "remaining_tokens": (
                            max(0, budget_state["max_total_response_tokens"] - budget_state["returned_tokens"])
                            if budget_state["max_total_response_tokens"] is not None
                            else None
                        ),
                        "returned_tokens": budget_state["returned_tokens"],
                        "total_token_budget": budget_state["max_total_response_tokens"],
                    },
                )
            }, unresolved=source_unresolved),
        )
    if end <= offset:
        raise PublicPageError(
            "The cumulative response character budget has been reached",
            ErrorCode.BUDGET_EXCEEDED,
        )
    content, end = _take_text_budgeted(
        total_text,
        offset,
        end - offset,
        response_token_limit,
    )
    _record_material_range(stored, budget_state, offset, end, content)
    try:
        _store_material(material_id, stored)
    except OSError:
            return _expand_blocked(request, "The material budget ledger could not be persisted", stored)
    has_more = end < len(total_text)
    block_id = "blk-body-0001"
    video_locator = (
        _video_locator_for_range(
            str(stored.get("source_url") or ""),
            stored.get("video_spans") if isinstance(stored.get("video_spans"), list) else [],
            offset,
            end,
        )
        if stored.get("source_kind") == "video"
        else None
    )
    block = MaterialBlock(
        id=f"{'blk-video-transcript' if stored.get('source_kind') == 'video' else block_id}-range-{offset}",
        kind=MaterialKind.TRANSCRIPT if stored.get("source_kind") == "video" else MaterialKind.PARAGRAPH,
        content=TextContent(type="text", text=content),
        origin=(
            MaterialOrigin.ASR
            if stored.get("source_kind") == "video" and stored.get("transcript_source") == "asr"
            else MaterialOrigin.SUBTITLE
            if stored.get("source_kind") == "video"
            else MaterialOrigin.SOURCE_TEXT
        ),
        locator=video_locator or WebLocator(
            type="web",
            source_url=str(stored["source_url"]),
            block_id=block_id,
            quote=content[:MAX_LOCATOR_QUOTE_CHARS],
        ),
        order=offset,
    )
    return ReadResponse(
        request_id=request.request_id,
        session_id=stored.get("session_id"),
        material_id=material_id,
        source_revision=str(stored["source_revision"]),
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if has_more or source_unresolved else ReadResult.READY,
        blocks=[block],
        coverage=Coverage(
            modalities={
                "text": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=1,
                    extraction="partial" if has_more else "complete",
                    unresolved=["More source text is available"] if has_more else [],
                    details={
                        "offset": offset,
                        "returned_chars": len(content),
                        "total_chars": len(total_text),
                        "next_offset": end if has_more else None,
                        "continuation_available": has_more,
                        "remaining_chars": max(0, budget_state["max_total_response_chars"] - budget_state["returned_chars"]),
                        "expansion_rounds": budget_state["expansion_rounds"],
                        "remaining_tokens": (
                            max(0, budget_state["max_total_response_tokens"] - budget_state["returned_tokens"])
                            if budget_state["max_total_response_tokens"] is not None
                            else None
                        ),
                        "returned_tokens": budget_state["returned_tokens"],
                        "total_token_budget": budget_state["max_total_response_tokens"],
                    },
                )
            }
        , unresolved=source_unresolved),
    )


def _fetch_html(
    source_url: str,
    max_bytes: int = MAX_HTML_BYTES,
    dns_cache: dict[str, bool] | None = None,
) -> tuple[str, str]:
    current_url = source_url
    dns_cache = dns_cache if dns_cache is not None else {}
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url, dns_cache):
                raise PublicPageError("The source URL or redirect is not a public HTTP(S) address")
            for attempt in range(MAX_FETCH_ATTEMPTS):
                try:
                    with client.stream("GET", current_url, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise PublicPageError("The source returned an invalid redirect")
                            current_url = urljoin(current_url, location)
                            break
                        if response.status_code in {401, 403}:
                            raise PublicPageError(
                                "The source requires access that Envoy cannot provide",
                                ErrorCode.AUTH_REQUIRED,
                            )
                        if response.status_code in {408, 425, 429} or response.status_code >= 500:
                            if attempt + 1 < MAX_FETCH_ATTEMPTS:
                                time.sleep(0.25 * (attempt + 1))
                                continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if content_type == "application/pdf":
                            content_length = response.headers.get("content-length")
                            if content_length:
                                try:
                                    if int(content_length) > max_bytes:
                                        raise PublicPageError("The PDF exceeds Envoy's size limit")
                                except ValueError:
                                    pass
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > max_bytes:
                                    raise PublicPageError("The PDF exceeds Envoy's size limit")
                            pdf_data = bytes(body)
                            if not pdf_data.startswith(b"%PDF-"):
                                raise PublicPageError("The server declared PDF content but returned invalid PDF data")
                            raise DetectedPDF({
                                "url": _redact_url(current_url),
                                "data": pdf_data,
                                "mime_type": "application/pdf",
                            })
                        if content_type.startswith("image/"):
                            raise DetectedImage({
                                "url": _redact_url(current_url),
                                "mime_type": content_type,
                            })
                        if content_type.startswith(("video/", "audio/")):
                            raise DetectedMedia({
                                "url": _redact_url(current_url),
                                "mime_type": content_type,
                                "sensitive_url": any(
                                    _is_sensitive_query_key(key)
                                    for key, _value in parse_qsl(
                                        urlparse(current_url).query,
                                        keep_blank_values=True,
                                    )
                                ),
                                "unsafe_handoff_url": not _video_handoff_url_is_safe(current_url),
                            })
                        text_path = urlparse(current_url).path.lower()
                        is_text_content = (
                            content_type in TEXT_CONTENT_TYPES
                            or (
                                content_type == "application/octet-stream"
                                and text_path.endswith(TEXT_SUFFIXES)
                            )
                        )
                        if is_text_content:
                            content_length = response.headers.get("content-length")
                            if content_length:
                                try:
                                    if int(content_length) > MAX_TEXT_SOURCE_BYTES:
                                        raise PublicPageError("The text document exceeds Envoy's size limit")
                                except ValueError:
                                    pass
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > MAX_TEXT_SOURCE_BYTES:
                                    raise PublicPageError("The text document exceeds Envoy's size limit")
                            charset = getattr(response, "encoding", None) or "utf-8"
                            raise DetectedText({
                                "url": _redact_url(current_url),
                                "data": bytes(body),
                                "mime_type": content_type if content_type in TEXT_CONTENT_TYPES else "text/plain",
                                "encoding": charset,
                            })
                        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                            raise PublicPageError(
                                f"Static reader only accepts HTML pages, received {content_type}",
                                ErrorCode.UNSUPPORTED_SOURCE,
                            )
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise PublicPageError("The public page exceeds Envoy's size limit")
                            except ValueError:
                                pass
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise PublicPageError("The public page exceeds Envoy's size limit")
                        encoding = getattr(response, "encoding", None) or "utf-8"
                        return current_url, bytes(body).decode(encoding, errors="replace")
                except httpx.TransportError:
                    if attempt + 1 >= MAX_FETCH_ATTEMPTS:
                        raise
                    time.sleep(0.25 * (attempt + 1))
                else:
                    break
            else:
                continue
    raise PublicPageError("The source exceeded the redirect limit")


def _fetch_source(
    source_url: str,
    max_bytes: int,
    dns_cache: dict[str, bool],
) -> dict:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url, dns_cache):
                raise PublicPageError("The source URL or redirect is not a public HTTP(S) address")
            with client.stream("GET", current_url, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise PublicPageError("The source returned an invalid redirect")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    raise PublicPageError(
                        f"Image source returned unsupported content type: {content_type or 'unknown'}",
                        ErrorCode.UNSUPPORTED_SOURCE,
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise PublicPageError("The image source exceeds Envoy's size limit")
                return {
                    "kind": "image",
                    "url": _redact_url(current_url),
                    "mime_type": content_type,
                    "size_bytes": int(content_length) if content_length else None,
                }
    raise PublicPageError("The source exceeded the redirect limit")


def _fetch_pdf_source(
    source_url: str,
    max_bytes: int,
    dns_cache: dict[str, bool],
) -> dict:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=60), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url, dns_cache):
                raise PublicPageError("The PDF URL or redirect is not a public HTTP(S) address")
            redirected = False
            for attempt in range(MAX_FETCH_ATTEMPTS):
                try:
                    with client.stream("GET", current_url, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise PublicPageError("The PDF server returned an invalid redirect")
                            current_url = urljoin(current_url, location)
                            redirected = True
                            break
                        if response.status_code in {401, 403}:
                            raise PublicPageError("The PDF source requires authentication", ErrorCode.AUTH_REQUIRED)
                        if response.status_code in {408, 425, 429} or response.status_code >= 500:
                            if attempt + 1 < MAX_FETCH_ATTEMPTS:
                                time.sleep(0.25 * (attempt + 1))
                                continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise PublicPageError("The PDF exceeds Envoy's size limit")
                            except ValueError:
                                pass
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise PublicPageError("The PDF exceeds Envoy's size limit")
                        data = bytes(body)
                        if not data.startswith(b"%PDF-"):
                            raise PublicPageError(
                                f"Expected a PDF source, received {content_type or 'unknown content'}",
                                ErrorCode.UNSUPPORTED_SOURCE,
                            )
                        return {
                            "url": _redact_url(current_url),
                            "data": data,
                            "mime_type": "application/pdf",
                        }
                except httpx.TransportError:
                    if attempt + 1 >= MAX_FETCH_ATTEMPTS:
                        raise
                    time.sleep(0.25 * (attempt + 1))
                else:
                    break
            if redirected:
                continue
    raise PublicPageError("The PDF source exceeded the redirect limit")


def _parse_pdf_source(request: ReadRequest, source: dict) -> ReadResponse:
    try:
        pdf = PdfReader(BytesIO(source["data"]), strict=False)
    except Exception as exc:
        raise PublicPageError(f"Could not parse PDF: {type(exc).__name__}", ErrorCode.INVALID_REQUEST) from exc
    if getattr(pdf, "is_encrypted", False):
        raise PublicPageError("Encrypted PDFs are not supported by the standalone Skill", ErrorCode.AUTH_REQUIRED)
    total_page_count = len(pdf.pages)
    if total_page_count > MAX_PDF_PAGES:
        raise PublicPageError(f"PDF has {total_page_count} pages; limit is {MAX_PDF_PAGES}")

    title = ""
    if pdf.metadata:
        title = str(pdf.metadata.title or "").strip()
    title = title or Path(urlparse(source["url"]).path).name or "PDF document"
    title_block = MaterialBlock(
        id="blk-title",
        kind=MaterialKind.HEADING,
        content=TextContent(type="text", text=title),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(
            type="web",
            source_url=source["url"],
            block_id="blk-title",
            quote=title[:MAX_LOCATOR_QUOTE_CHARS],
        ),
        order=0,
    )
    page_texts = []
    page_spans = []
    cursor = 0
    scanned_pages = []
    for page_number, page in enumerate(pdf.pages, start=1):
        try:
            page_text = re.sub(r"\n{3,}", "\n\n", str(page.extract_text() or "")).strip()
        except Exception:
            page_text = ""
        if not page_text:
            scanned_pages.append(page_number)
            continue
        if page_texts:
            cursor += 2
        start = cursor
        cursor += len(page_text)
        page_texts.append(page_text)
        page_spans.append({"page_number": page_number, "start": start, "end": cursor})

    full_text = "\n\n".join(page_texts)
    if not full_text:
        raise PublicPageError(
            "This PDF has no extractable text layer; OCR capability is required for scanned pages",
            ErrorCode.MISSING_CAPABILITY,
        )

    available_chars = max(
        0,
        _response_char_budget(request) - len(title_block.model_dump_json()),
    )
    initial_token_limit = request.budget.max_response_tokens
    if request.budget.max_total_response_tokens is not None:
        initial_token_limit = (
            min(initial_token_limit, request.budget.max_total_response_tokens)
            if initial_token_limit is not None
            else request.budget.max_total_response_tokens
        )
    returned_tokens = _estimated_tokens(title)
    token_remaining = (
        max(0, initial_token_limit - returned_tokens)
        if initial_token_limit is not None
        else None
    )
    blocks = [title_block]
    returned_chars = 0
    returned_end = 0
    response_budget_exhausted = False
    for span in page_spans:
        page_number = span["page_number"]
        page_text = full_text[span["start"]:span["end"]]
        overhead = 240
        remaining = available_chars - returned_chars - overhead
        if remaining <= 0:
            response_budget_exhausted = True
            break
        page_body, page_end = _take_text_budgeted(page_text, 0, remaining, token_remaining)
        if page_body:
            blocks.append(MaterialBlock(
                id=f"blk-pdf-page-{page_number:04d}",
                kind=MaterialKind.PARAGRAPH,
                content=TextContent(type="text", text=page_body),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=PdfLocator(type="pdf_page", page_number=page_number),
                order=page_number,
            ))
            returned_chars += len(page_body)
            page_tokens = _estimated_tokens(page_body)
            returned_tokens += page_tokens
            if token_remaining is not None:
                token_remaining = max(0, token_remaining - page_tokens)
            returned_end = span["start"] + page_end
        if page_end < len(page_text):
            response_budget_exhausted = True
            break
    if returned_end < len(full_text):
        response_budget_exhausted = True

    source_revision = hashlib.sha256(source["data"]).hexdigest()
    material_id = _material_id(
        source["url"], source_revision, request.session_id, request.request_id
    )
    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_budget_exhausted and not retention_disabled
    source_unresolved = []
    if scanned_pages:
        source_unresolved.append(
            "PDF pages without an extractable text layer require OCR: "
            + ", ".join(str(page_number) for page_number in scanned_pages[:64])
        )
    if response_budget_exhausted:
        source_unresolved.append("PDF source text exceeds the initial response budget")
    should_store_material = can_continue or (
        not retention_disabled and len(full_text) <= MAX_SEARCH_CACHE_CHARS
    )
    continuation_storage_failed = False
    if should_store_material:
        try:
            _store_material(material_id, {
                "source_kind": "pdf",
                "source_url": source["url"],
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": full_text,
                "title_text": title,
                "page_spans": page_spans,
                "source_coverage_unresolved": source_unresolved,
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": request.budget.max_expansion_rounds,
                    "returned_chars": returned_chars,
                    "returned_tokens": returned_tokens,
                    "expansion_rounds": 0,
                    "returned_ranges": [[0, returned_end]] if returned_end else [],
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            can_continue = False
            continuation_storage_failed = True

    unresolved = list(source_unresolved)
    if scanned_pages:
        unresolved.append("Some pages contain no text layer and need OCR")
    if response_budget_exhausted:
        unresolved.append("PDF text was limited by the response budget")
    if retention_disabled and response_budget_exhausted:
        unresolved.append("Continuation is unavailable because local retention is disabled")
    if continuation_storage_failed:
        unresolved.append("Continuation cache could not be written")
    has_unread_pages = bool(scanned_pages)
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if response_budget_exhausted or has_unread_pages else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={
            "text": CoverageItem(
                discovered=total_page_count,
                extracted=len(page_spans),
                returned=len(blocks) - 1,
                extraction="partial" if response_budget_exhausted or has_unread_pages else "complete",
                unresolved=unresolved,
                details={
                    "page_count": total_page_count,
                    "text_pages": len(page_spans),
                    "scanned_pages": scanned_pages,
                    "returned_chars": returned_chars,
                    "total_chars": len(full_text),
                    "continuation_available": can_continue,
                    "next_offset": returned_end if can_continue else None,
                    **_token_budget_details(request, returned_tokens, full_text[returned_end:]),
                },
            )
        }, unresolved=list(dict.fromkeys(source_unresolved))),
    )


def _read_pdf_source(request: ReadRequest, source_url: str, dns_cache: dict[str, bool]) -> ReadResponse:
    source = _fetch_pdf_source(
        source_url,
        min(MAX_PDF_BYTES, request.budget.max_download_bytes or MAX_PDF_BYTES),
        dns_cache,
    )
    return _parse_pdf_source(request, source)


def _parse_text_source(request: ReadRequest, source: dict) -> ReadResponse:
    data = source["data"]
    text = data.decode(str(source.get("encoding") or "utf-8"), errors="replace")
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise PublicPageError("The text document is empty", ErrorCode.EMPTY_CONTENT)
    source_truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    title = Path(urlparse(source["url"]).path).name or "Text document"
    title_block = MaterialBlock(
        id="blk-title",
        kind=MaterialKind.HEADING,
        content=TextContent(type="text", text=title),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(
            type="web",
            source_url=source["url"],
            block_id="blk-title",
            quote=title[:MAX_LOCATOR_QUOTE_CHARS],
        ),
        order=0,
    )
    available_chars = max(
        0,
        _response_char_budget(request) - len(title_block.model_dump_json()) - 200,
    )
    initial_token_limit = request.budget.max_response_tokens
    if request.budget.max_total_response_tokens is not None:
        initial_token_limit = (
            min(initial_token_limit, request.budget.max_total_response_tokens)
            if initial_token_limit is not None
            else request.budget.max_total_response_tokens
        )
    title_tokens = _estimated_tokens(title)
    returned_text, returned_end = _take_text_budgeted(
        text,
        0,
        available_chars,
        max(0, initial_token_limit - title_tokens) if initial_token_limit is not None else None,
    )
    returned_tokens = title_tokens + _estimated_tokens(returned_text)
    response_partial = source_truncated or returned_end < len(text)
    source_revision = hashlib.sha256(data).hexdigest()
    material_id = _material_id(
        source["url"], source_revision, request.session_id, request.request_id
    )
    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_partial and not source_truncated and not retention_disabled
    should_store_material = can_continue or (
        not retention_disabled and not source_truncated and len(text) <= MAX_SEARCH_CACHE_CHARS
    )
    continuation_storage_failed = False
    if should_store_material:
        try:
            _store_material(material_id, {
                "source_kind": "text",
                "source_url": source["url"],
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": text,
                "title_text": title,
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": request.budget.max_expansion_rounds,
                    "returned_chars": len(returned_text),
                    "returned_tokens": returned_tokens,
                    "expansion_rounds": 0,
                    "returned_ranges": [[0, len(returned_text)]] if returned_text else [],
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            can_continue = False
            continuation_storage_failed = True
    unresolved = []
    if returned_end < len(text):
        unresolved.append("Response character budget truncated the text document")
    if source_truncated:
        unresolved.append("Text source exceeded the extraction limit")
    if retention_disabled and response_partial:
        unresolved.append("Continuation is unavailable because local retention is disabled")
    if continuation_storage_failed:
        unresolved.append("Continuation cache could not be written")
    blocks = [title_block]
    if returned_text:
        blocks.append(MaterialBlock(
            id="blk-body-0001",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=returned_text),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=source["url"],
                block_id="blk-body-0001",
                quote=returned_text[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=1,
        ))
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if response_partial else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={
            "text": CoverageItem(
                discovered=1,
                extracted=1,
                returned=1 if returned_text else 0,
                extraction="partial" if response_partial else "complete",
                unresolved=unresolved,
                details={
                    "mime_type": source["mime_type"],
                    "total_chars": len(text),
                    "returned_chars": len(returned_text),
                    "continuation_available": can_continue,
                    "source_truncated": source_truncated,
                    "next_offset": returned_end if can_continue else None,
                    **_token_budget_details(request, returned_tokens, text[returned_end:]),
                },
            )
        }),
    )


def _read_image_source(request: ReadRequest, source_url: str, dns_cache: dict[str, bool]) -> ReadResponse:
    if request.host_capabilities.image_input != ImageInputCapability.URI:
        return _blocked(
            request,
            ErrorCode.MISSING_CAPABILITY,
            "The host must support URI image input to inspect this image source",
            action_required=True,
        )
    source = _fetch_source(
        source_url,
        min(MAX_HTML_BYTES, request.budget.max_download_bytes or MAX_HTML_BYTES),
        dns_cache,
    )
    resource_id = "resource-image"
    source_revision = hashlib.sha256(source["url"].encode()).hexdigest()
    material_id = _material_id(
        source["url"], source_revision, request.session_id, request.request_id
    )
    if request.retention.derived_ttl_seconds > 0:
        try:
            _store_material(material_id, {
                "source_url": source["url"],
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": "",
                "title_text": request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image",
                "visual_resources": [{
                    "resource_id": resource_id,
                    "source_block_id": "blk-image-0001",
                    "source_url": source["url"],
                }],
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": 0,
                    "returned_chars": 0,
                    "returned_tokens": _estimated_tokens(
                        request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image"
                    ),
                    "expansion_rounds": 0,
                    "returned_ranges": [],
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            pass
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.WAITING_FOR_HOST,
        result=ReadResult.NEEDS_HOST,
        blocks=[
            MaterialBlock(
                id="blk-image-0001",
                kind=MaterialKind.IMAGE,
                content=ImageContent(
                    type="image",
                    resource_id=resource_id,
                    alt_text=request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image",
                    mime_type=source["mime_type"],
                ),
                origin=MaterialOrigin.SOURCE_IMAGE,
                locator=WebLocator(
                    type="web",
                    source_url=source["url"],
                    block_id="blk-image-0001",
                    quote=request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image",
                ),
                order=0,
            )
        ],
        resources=[
            ResourceRef(
                resource_id=resource_id,
                mime_type=source["mime_type"],
                delivery=HostDelivery(type="uri", uri=source["url"]),
                sha256=None,
                delivery_evidence="unverified",
            )
        ],
        needs=[
            Need(
                action_id="inspect-image-0001",
                type="inspect_visual",
                purpose=request.goal or "Inspect the source image",
                resource_ids=[resource_id],
                source_block_ids=["blk-image-0001"],
                reason="The source contains visual information that should be inspected by the host model.",
                max_resources=1,
            )
        ],
        coverage=Coverage(
            modalities={
                "images": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=1,
                    delivered=1,
                    extraction="complete",
                )
            }
        ),
    )


def _format_video_time(seconds: float) -> str:
    total_millis = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(total_millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def _video_spans_for_range(spans: list[dict], start: int, end: int) -> list[dict]:
    return [
        item for item in spans
        if isinstance(item, dict)
        and int(item.get("end_offset") or 0) > start
        and int(item.get("start_offset") or 0) < end
    ]


def _video_locator_for_range(source_url: str, spans: list[dict], start: int, end: int):
    matched = _video_spans_for_range(spans, start, end)
    if not matched:
        return None
    return VideoLocator(
        type="video_time",
        source_url=source_url,
        start_seconds=float(matched[0]["start_seconds"]),
        end_seconds=max(
            float(matched[-1]["end_seconds"]),
            float(matched[0]["start_seconds"]) + 0.001,
        ),
    )


def _video_subtitle_response(
    request: ReadRequest,
    source_url: str,
    extracted: dict,
) -> ReadResponse:
    is_asr = str(extracted.get("source") or "") == "asr"
    transcript_source = "asr" if is_asr else (
        "automatic_subtitles" if extracted.get("automatic") else "manual_subtitles"
    )
    transcript_origin = MaterialOrigin.ASR if is_asr else MaterialOrigin.SUBTITLE
    transcript_processing_partial = is_asr and extracted.get("transcription_status") == "partial"
    title = str(extracted.get("title") or request.source.label or "Video transcript")
    language = str(extracted.get("language") or "")
    requested_start = request.scope.start_seconds
    requested_end = request.scope.end_seconds
    parts = []
    spans = []
    cursor = 0
    for cue in extracted.get("cues") or []:
        if not isinstance(cue, dict):
            continue
        text = re.sub(r"\s+", " ", str(cue.get("text") or "")).strip()
        try:
            start_seconds = float(cue.get("start"))
            end_seconds = float(cue.get("end"))
        except (TypeError, ValueError):
            continue
        if not text or start_seconds < 0 or end_seconds <= start_seconds:
            continue
        if (
            requested_start is not None
            and requested_end is not None
            and (end_seconds <= requested_start or start_seconds >= requested_end)
        ):
            continue
        line = f"[{_format_video_time(start_seconds)}] {text}"
        start_offset = cursor
        cursor += len(line)
        parts.append(line)
        spans.append({
            "start_offset": start_offset,
            "end_offset": cursor,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
        })
        cursor += 2
    full_text = "\n\n".join(parts)
    no_cues_in_requested_range = (
        requested_start is not None
        and requested_end is not None
        and not spans
    )
    source_truncated = len(full_text) > MAX_TEXT_CHARS
    if source_truncated:
        full_text = full_text[:MAX_TEXT_CHARS]
        spans = [item for item in spans if item["start_offset"] < len(full_text)]
        for item in spans:
            item["end_offset"] = min(item["end_offset"], len(full_text))
    source_revision = hashlib.sha256(
        f"{source_url}\n{language}\n{full_text}".encode("utf-8", errors="replace")
    ).hexdigest()
    material_id = _material_id(
        source_url, source_revision, request.session_id, request.request_id
    )
    existing_material = _load_matching_material(
        material_id,
        source_revision,
        request.session_id,
    )
    if existing_material is not None:
        return _cached_material_read_response(
            request,
            material_id,
            source_revision,
            existing_material,
        )
    title_block = MaterialBlock(
        id="blk-title",
        kind=MaterialKind.HEADING,
        content=TextContent(type="text", text=title),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(
            type="web",
            source_url=_redact_url(source_url),
            block_id="blk-title",
            quote=title[:MAX_LOCATOR_QUOTE_CHARS],
        ),
        order=0,
    )
    available_chars = max(
        0,
        _response_char_budget(request) - len(title_block.model_dump_json()) - 256,
    )
    response_token_limit = request.budget.max_response_tokens
    if request.budget.max_total_response_tokens is not None:
        response_token_limit = (
            min(response_token_limit, request.budget.max_total_response_tokens)
            if response_token_limit is not None
            else request.budget.max_total_response_tokens
        )
    title_tokens = _estimated_tokens(title)
    body_token_limit = (
        max(0, response_token_limit - title_tokens)
        if response_token_limit is not None
        else None
    )
    returned_text, returned_end = _take_text_budgeted(
        full_text,
        0,
        available_chars,
        body_token_limit,
    )
    returned_tokens = title_tokens + _estimated_tokens(returned_text)
    response_partial = returned_end < len(full_text) or source_truncated or transcript_processing_partial
    text_block = None
    if returned_text:
        text_block = MaterialBlock(
            id="blk-video-transcript",
            kind=MaterialKind.TRANSCRIPT,
            content=TextContent(type="text", text=returned_text, language=language or None),
            origin=transcript_origin,
            locator=_video_locator_for_range(source_url, spans, 0, returned_end),
            parent_id="blk-title",
            order=1,
        )
    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_partial and not source_truncated and not retention_disabled
    source_unresolved = [
        "Video frames were not inspected; this material contains transcript evidence only"
    ]
    if requested_start is not None and requested_end is not None:
        source_unresolved.append(
            f"Transcript coverage is limited to the requested range "
            f"{_format_video_time(float(requested_start))}-"
            f"{_format_video_time(float(requested_end))}; other audio ranges were not read"
        )
    if no_cues_in_requested_range:
        source_unresolved.append(
            "No transcript cues intersect the requested video time range"
        )
    if transcript_processing_partial:
        processed_until = extracted.get("processed_until_seconds")
        if processed_until is not None:
            source_unresolved.append(
                f"Local ASR stopped at {_format_video_time(float(processed_until))}; later audio remains untranscribed"
            )
        else:
            source_unresolved.append(
                "Local ASR returned a partial transcript; audio coverage is incomplete"
            )
    should_store_material = (
        bool(full_text)
        and not retention_disabled
        and (
            can_continue
            or transcript_processing_partial
            or len(full_text) <= MAX_SEARCH_CACHE_CHARS
        )
    )
    continuation_storage_failed = False
    if should_store_material:
        try:
            _store_material(material_id, {
                "source_kind": "video",
                "source_url": _redact_url(source_url),
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": full_text,
                "title_text": title,
                "subtitle_language": language,
                "transcript_source": transcript_source,
                "source_coverage_unresolved": source_unresolved,
                "video_spans": spans,
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": request.budget.max_expansion_rounds,
                    "returned_chars": len(returned_text),
                    "returned_tokens": returned_tokens,
                    "expansion_rounds": 0,
                    "returned_ranges": [[0, returned_end]] if returned_end else [],
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            can_continue = False
            continuation_storage_failed = True
    unresolved_text = []
    if no_cues_in_requested_range:
        unresolved_text.append(
            "No transcript cues intersect the requested video time range"
        )
    if returned_end < len(full_text) or source_truncated:
        unresolved_text.append("Transcript text is limited by the response or local extraction budget")
    if transcript_processing_partial:
        unresolved_text.append(
            "Local ASR only covered the reported time range before its work budget was reached"
        )
    if continuation_storage_failed:
        unresolved_text.append("Subtitle continuation cache could not be written")
    if retention_disabled and response_partial:
        unresolved_text.append("Subtitle continuation is unavailable because local retention is disabled")
    blocks = [title_block]
    if text_block:
        blocks.append(text_block)
    video_unresolved = [
        "Video frames were not inspected; this material contains transcript evidence only"
    ]
    if no_cues_in_requested_range:
        video_unresolved.append(
            "No transcript cues intersect the requested video time range"
        )
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=(
            ReadResult.EMPTY
            if no_cues_in_requested_range
            else ReadResult.PARTIAL if response_partial else ReadResult.READY
        ),
        blocks=blocks,
        coverage=Coverage(
            modalities={
                "text": CoverageItem(
                    discovered=len(spans),
                    extracted=len(spans),
                    returned=1 if text_block else 0,
                    extraction="partial" if response_partial else "complete",
                    unresolved=unresolved_text,
                    details={
                        "source": "video_asr" if is_asr else "video_subtitles",
                        "language": language,
                        "transcript_source": transcript_source,
                        "total_chars": len(full_text),
                        "returned_chars": len(returned_text),
                        "continuation_available": can_continue,
                        "next_offset": returned_end if can_continue else None,
                        "goal_candidates": _goal_candidates(full_text, _goal_terms(request.goal)),
                        **_token_budget_details(request, returned_tokens, full_text[returned_end:]),
                    },
                ),
                "video": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=1,
                    processed=1,
                    extraction="partial",
                    unresolved=video_unresolved,
                    details={
                        "video_id": extracted.get("video_id"),
                        "duration_seconds": extracted.get("duration"),
                        "subtitle_language": language,
                        "transcript_source": transcript_source,
                        "requested_range": (
                            {
                                "start_seconds": requested_start,
                                "end_seconds": requested_end,
                            }
                            if requested_start is not None
                            else None
                        ),
                        "available_subtitle_languages": extracted.get("available_languages", []),
                        "visual_inspection": "not_performed",
                        "asr_model": extracted.get("model") if is_asr else None,
                        "transcription_status": extracted.get("transcription_status") if is_asr else None,
                        "processed_until_seconds": extracted.get("processed_until_seconds") if is_asr else None,
                    },
                ),
            },
            unresolved=source_unresolved,
            # Only report audio ranges that were actually transcribed.
            sampled_ranges=[{
                "modality": "audio",
                "start_seconds": (
                    float(request.scope.start_seconds)
                    if request.scope.start_seconds is not None
                    else 0
                ),
                "end_seconds": extracted.get("processed_until_seconds"),
            }] if is_asr and extracted.get("processed_until_seconds") is not None else [],
        ),
        usage=Usage(local={
            key: value
            for key, value in {
                "asr_model": extracted.get("model") if is_asr else None,
                "asr_duration_ms": extracted.get("asr_duration_ms") if is_asr else None,
                "downloaded_bytes": extracted.get("downloaded_bytes") if is_asr else None,
                "media_download_ms": extracted.get("media_download_ms") if is_asr else None,
            }.items()
            if value is not None
        }),
    )


def _read_video_asr(request: ReadRequest, source_url: str) -> ReadResponse:
    status = media_runtime_status()
    if not status["ffmpeg"] or not status["ffprobe"]:
        raise MediaCapabilityMissing("Local ASR requires FFmpeg and FFprobe")
    if not status["yt_dlp"]:
        raise MediaCapabilityMissing("Local ASR requires yt-dlp")
    if not status.get("yt_dlp_supported"):
        raise MediaCapabilityMissing(
            f"Local video extraction requires yt-dlp>={'.'.join(map(str, (2026, 8, 19)))}"
        )
    if not status["asr_installed"]:
        raise MediaCapabilityMissing(
            "No public subtitle track is available; install envoy-cli[media] to enable local ASR"
        )
    if not status["asr_model_available"]:
        raise MediaCapabilityMissing(
            f"Local ASR model '{status['asr_model']}' is not prepared; run envoy asr setup --model {status['asr_model']}"
        )
    if request.budget.max_download_bytes <= 0:
        raise MediaBudgetExceeded("max_download_bytes does not allow media download")

    started = time.monotonic()
    work_limit = _remaining_work_seconds(request)
    with tempfile.TemporaryDirectory(prefix="envoy-asr-") as temporary_dir:
        downloaded = download_audio_for_asr(
            source_url,
            temporary_dir,
            max_download_bytes=request.budget.max_download_bytes,
            max_work_seconds=work_limit,
        )
        try:
            remaining_work = min(
                work_limit - int(time.monotonic() - started),
                _remaining_work_seconds(request),
            )
        except MediaBudgetExceeded:
            raise MediaBudgetExceeded("Media download exhausted max_work_seconds before ASR")
        if remaining_work < 1:
            raise MediaBudgetExceeded("Media download exhausted max_work_seconds before ASR")
        asr_result = transcribe_audio_file(
            downloaded["path"],
            model_name=status["asr_model"],
            download_root=asr_model_cache_dir(),
            max_work_seconds=max(int(remaining_work), 1),
            start_seconds=request.scope.start_seconds or 0,
            end_seconds=request.scope.end_seconds or downloaded.get("duration"),
        )
    cues = asr_result.get("segments") if isinstance(asr_result.get("segments"), list) else []
    if not cues:
        raise MediaNoSpeech("Local ASR found no speech segments")
    return _video_subtitle_response(
        request,
        _redact_url(source_url),
        {
            "status": "ready",
            "source": "asr",
            "video_id": downloaded.get("video_id"),
            "title": downloaded.get("title"),
            "duration": downloaded.get("duration"),
            "language": asr_result.get("language"),
            "model": asr_result.get("model"),
            "transcription_status": asr_result.get("status"),
            "processed_until_seconds": asr_result.get("next_seconds"),
            "asr_duration_ms": asr_result.get("elapsed_ms"),
            "downloaded_bytes": downloaded.get("downloaded_bytes"),
            "media_download_ms": downloaded.get("elapsed_ms"),
            "automatic": False,
            "available_languages": [],
            "cues": cues,
        },
    )


def _video_frame_timestamps(request: ReadRequest, duration: float) -> list[float]:
    limit = min(max(int(request.budget.max_visual_inputs), 0), 6)
    if limit <= 0 or duration <= 0:
        return []
    if request.scope.start_seconds is not None and request.scope.end_seconds is not None:
        start = min(float(request.scope.start_seconds), duration)
        end = min(float(request.scope.end_seconds), duration)
        if end <= start:
            return []
        count = min(limit, 3)
        if count == 1:
            return [(start + end) / 2]
        return [
            start + ((end - start) * index / (count - 1))
            for index in range(count)
        ]
    count = min(limit, 6)
    return [duration * (index + 0.5) / count for index in range(count)]


def _video_frame_base_response(
    request: ReadRequest,
    source_url: str,
    title: str,
    duration: float | None,
    unresolved: list[str] | None = None,
) -> ReadResponse:
    safe_url = _redact_url(source_url)
    safe_title = title or request.source.label or "Video"
    source_revision = hashlib.sha256(f"video-url\n{safe_url}".encode()).hexdigest()
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=_material_id(
            safe_url,
            source_revision,
            request.session_id,
            request.request_id,
        ),
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
        blocks=[MaterialBlock(
            id="blk-video-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=safe_title),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=safe_url,
                block_id="blk-video-title",
                quote=safe_title[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=0,
        )],
        coverage=Coverage(modalities={
            "video": CoverageItem(
                discovered=1,
                extracted=0,
                returned=1,
                extraction="partial",
                unresolved=unresolved or ["Speech transcript is not available"],
                details={
                    "duration_seconds": duration,
                    "visual_inspection": "not_performed",
                },
            ),
        }),
    )


def _replace_video_coverage(
    coverage: Coverage,
    video_item: CoverageItem,
    sampled_ranges: list[dict] | None = None,
) -> Coverage:
    modalities = dict(coverage.modalities)
    modalities["video"] = video_item
    return coverage.model_copy(update={
        "modalities": modalities,
        "sampled_ranges": [*coverage.sampled_ranges, *(sampled_ranges or [])],
    })


def _attach_video_frames(
    request: ReadRequest,
    source_url: str,
    response: ReadResponse,
    *,
    downloaded_media: dict | None = None,
) -> ReadResponse:
    if not request.scope.include_visual or response.result == ReadResult.BLOCKED:
        return response

    visual_capable = (
        request.host_capabilities.video_input == "frames"
        and request.host_capabilities.image_input == ImageInputCapability.LOCAL_FILE
    )
    video_coverage = response.coverage.modalities.get("video") or CoverageItem()
    if not visual_capable:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "partial",
            "unresolved": list(dict.fromkeys([
                *video_coverage.unresolved,
                "Visual frames were requested but the host did not declare video_input=frames and image_input=local_file",
            ])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })
    if request.retention.derived_ttl_seconds <= 0:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "partial",
            "unresolved": list(dict.fromkeys([
                *video_coverage.unresolved,
                "Local frame delivery requires a temporary material lease",
            ])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })

    duration = float(video_coverage.details.get("duration_seconds") or 0)
    total_visuals = max(int(request.budget.max_visual_inputs), 0)
    pixel_capacity = max(int(request.budget.max_total_visual_pixels), 0)
    per_frame_limit = max(int(request.budget.max_visual_pixels_per_input), 0)
    frame_limit = min(
        total_visuals,
        pixel_capacity // per_frame_limit if per_frame_limit > 0 else 0,
        6,
    )
    if frame_limit <= 0:
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(
                response.coverage,
                video_coverage.model_copy(update={
                    "extraction": "partial",
                    "unresolved": list(dict.fromkeys([
                        *video_coverage.unresolved,
                        "No frame fits the declared visual input and pixel budgets",
                    ])),
                }),
            ),
        })

    root = _material_root()
    material_id = response.material_id or _material_id(
        _redact_url(source_url),
        hashlib.sha256(f"{source_url}\nvideo-frames".encode()).hexdigest(),
        request.session_id,
        request.request_id,
    )
    frame_dir = root / f"frames-{material_id}"
    if frame_dir.is_symlink():
        return _blocked(
            request,
            ErrorCode.INVALID_REQUEST,
            "The local frame lease path is not a regular Envoy directory",
        )
    if frame_dir.exists() and not frame_dir.is_dir():
        return _blocked(
            request,
            ErrorCode.INVALID_REQUEST,
            "The local frame lease path is not a directory",
        )
    material_path = root / f"{material_id}.json"
    if not material_path.is_file() or material_path.is_symlink():
        _store_material(material_id, {
            "source_kind": "video",
            "source_url": _redact_url(source_url),
            "source_revision": response.source_revision or hashlib.sha256(source_url.encode()).hexdigest(),
            "session_id": request.session_id,
            "text": "",
            "title_text": request.source.label or "Video",
            "source_coverage_unresolved": list(video_coverage.unresolved),
            "budget_state": {
                "max_total_response_chars": request.budget.max_total_response_chars,
                "max_total_response_tokens": request.budget.max_total_response_tokens,
                "max_expansion_rounds": 0,
                "returned_chars": 0,
                "returned_tokens": 0,
                "expansion_rounds": 0,
                "returned_ranges": [],
            },
            "expires_at": time.time() + min(
                request.retention.derived_ttl_seconds,
                MATERIAL_TTL_SECONDS,
            ),
        })
    frame_dir.mkdir(mode=0o700, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="envoy-video-frames-") as temporary_dir:
            downloaded = downloaded_media or download_video_for_frames(
                source_url,
                temporary_dir,
                max_download_bytes=request.budget.max_download_bytes,
                max_work_seconds=_remaining_work_seconds(request),
            )
            media_path = Path(str(downloaded.get("path") or "")).resolve()
            if not media_path.is_file():
                raise MediaProcessingError("Downloaded video file is unavailable for frame extraction")
            duration = duration or float(downloaded.get("duration") or 0)
            if duration <= 0:
                remaining_work = _remaining_work_seconds(request)
                duration = probe_media_duration(
                    media_path,
                    max_work_seconds=remaining_work,
                )
            timestamps = _video_frame_timestamps(request, duration)[:frame_limit]
            if not timestamps:
                raise MediaProcessingError("The requested video time range is outside the video duration")
            remaining_work = _remaining_work_seconds(request)
            extracted_frames = extract_video_frames(
                media_path,
                Path(temporary_dir) / "frames",
                timestamps,
                max_visual_pixels_per_input=per_frame_limit,
                max_work_seconds=remaining_work,
            )
            if not extracted_frames:
                raise MediaProcessingError("No video frames were extracted")

            blocks = list(response.blocks)
            resources = list(response.resources)
            visual_records = []
            frame_pixels = 0
            for index, frame in enumerate(extracted_frames[:len(timestamps)], start=1):
                source_frame = Path(frame["path"]).resolve()
                if source_frame.parent != (Path(temporary_dir) / "frames").resolve():
                    raise MediaProcessingError("Extracted frame escaped its temporary directory")
                frame_pixels += int(frame["pixels"])
                if int(frame["pixels"]) > per_frame_limit or frame_pixels > pixel_capacity:
                    raise MediaBudgetExceeded("Extracted frames exceeded the declared visual pixel budget")
                frame_name = f"frame-{index:03d}.jpg"
                destination = frame_dir / frame_name
                shutil.copyfile(source_frame, destination)
                destination.chmod(0o600)
                block_id = f"blk-video-frame-{index:03d}"
                resource_id = f"resource-{hashlib.sha256((material_id + frame_name + frame['sha256']).encode()).hexdigest()[:16]}"
                timestamp = float(frame["timestamp"])
                frame_block = MaterialBlock(
                    id=block_id,
                    kind=MaterialKind.VIDEO_FRAME,
                    content=ImageContent(
                        type="image",
                        resource_id=resource_id,
                        alt_text=f"Video frame at {_format_video_time(timestamp)}",
                        mime_type="image/jpeg",
                    ),
                    origin=MaterialOrigin.SOURCE_VIDEO,
                    locator=VideoLocator(
                        type="video_time",
                        source_url=_redact_url(source_url),
                        start_seconds=timestamp,
                        end_seconds=timestamp + 0.001,
                        frame_id=frame_name,
                    ),
                    parent_id=blocks[0].id if blocks else None,
                    order=len(blocks),
                    quality=Quality(
                        engine="ffmpeg",
                        unresolved=["Sparse frame sample; not full-video visual coverage"],
                    ),
                )
                blocks.append(frame_block)
                resources.append(ResourceRef(
                    resource_id=resource_id,
                    mime_type="image/jpeg",
                    delivery=HostDelivery(type="local_file", path=str(destination)),
                    width_px=int(frame["width_px"]),
                    height_px=int(frame["height_px"]),
                    sha256=str(frame["sha256"]),
                    delivery_evidence="generated_only",
                    expires_at=datetime.fromtimestamp(
                        time.time() + min(
                            request.retention.derived_ttl_seconds,
                            MATERIAL_TTL_SECONDS,
                        ),
                        tz=timezone.utc,
                    ),
                ))
                visual_records.append({
                    "resource_id": resource_id,
                    "source_block_id": block_id,
                    "source_url": _redact_url(source_url),
                    "frame_path": str(destination),
                    "timestamp": timestamp,
                })
    except MediaBudgetExceeded as exc:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "partial",
            "unresolved": list(dict.fromkeys([*video_coverage.unresolved, str(exc)])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })
    except MediaUnsafeAddress:
        return _blocked(
            request,
            ErrorCode.SOURCE_UNAVAILABLE,
            "Media extraction resolved a non-public network address",
        )
    except (MediaCapabilityMissing, MediaProcessingError) as exc:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "failed",
            "unresolved": list(dict.fromkeys([*video_coverage.unresolved, str(exc)])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })
    except OSError as exc:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "failed",
            "unresolved": list(dict.fromkeys([
                *video_coverage.unresolved,
                f"Could not persist leased video frames ({type(exc).__name__})",
            ])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })

    try:
        material_path = root / f"{material_id}.json"
        if material_path.is_file() and not material_path.is_symlink():
            stored = json.loads(material_path.read_text(encoding="utf-8"))
        else:
            stored = {
                "source_kind": "video",
                "source_url": _redact_url(source_url),
                "source_revision": response.source_revision or hashlib.sha256(source_url.encode()).hexdigest(),
                "session_id": request.session_id,
                "text": "",
                "title_text": request.source.label or "Video",
                "source_coverage_unresolved": list(video_coverage.unresolved),
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": 0,
                    "returned_chars": 0,
                    "returned_tokens": 0,
                    "expansion_rounds": 0,
                    "returned_ranges": [],
                },
            }
        existing_visuals = stored.get("visual_resources")
        if not isinstance(existing_visuals, list):
            existing_visuals = []
        stored["visual_resources"] = [*existing_visuals, *visual_records]
        stored["frame_dir"] = str(frame_dir)
        stored["expires_at"] = time.time() + min(
            request.retention.derived_ttl_seconds,
            MATERIAL_TTL_SECONDS,
        )
        _store_material(material_id, stored)
    except (OSError, ValueError, TypeError) as exc:
        video_coverage = video_coverage.model_copy(update={
            "extraction": "failed",
            "unresolved": list(dict.fromkeys([
                *video_coverage.unresolved,
                f"Frame material lease could not be stored ({type(exc).__name__})",
            ])),
        })
        return response.model_copy(update={
            "result": ReadResult.PARTIAL,
            "coverage": _replace_video_coverage(response.coverage, video_coverage),
        })

    needs = list(response.needs)
    needs.append(Need(
        action_id=f"inspect-video-frames-{material_id[-8:]}",
        type="inspect_visual",
        purpose=request.goal or "Inspect the requested video frames",
        resource_ids=[record["resource_id"] for record in visual_records],
        source_block_ids=[record["source_block_id"] for record in visual_records],
        reason="The host declared local-file image input and Envoy extracted a bounded frame sample for model inspection.",
        max_resources=len(visual_records),
    ))
    video_coverage = video_coverage.model_copy(update={
        "returned": max(int(video_coverage.returned or 0), 1),
        "processed": max(int(video_coverage.processed or 0), 1),
        "delivered": len(visual_records),
        "extraction": "partial",
        "unresolved": list(dict.fromkeys([
            *video_coverage.unresolved,
            "Only the reported sparse frames were inspected; unreported times remain unobserved",
        ])),
        "details": {
            **video_coverage.details,
            "duration_seconds": duration,
            "visual_inspection": "waiting_for_host",
            "frame_count": len(visual_records),
            "frame_timestamps": [item["timestamp"] for item in visual_records],
            "downloaded_bytes": downloaded.get("downloaded_bytes"),
            "media_download_ms": downloaded.get("elapsed_ms"),
            "frame_pixels": frame_pixels,
        },
    })
    updated_usage = dict(response.usage.local)
    updated_usage.update({
        "video_downloaded_bytes": downloaded.get("downloaded_bytes"),
        "video_download_ms": downloaded.get("elapsed_ms"),
        "frame_count": len(visual_records),
        "frame_pixels": frame_pixels,
    })
    return response.model_copy(update={
        "material_id": material_id,
        "blocks": blocks,
        "resources": resources,
        "needs": needs,
        "execution_state": ExecutionState.WAITING_FOR_HOST,
        "result": ReadResult.NEEDS_HOST,
        "coverage": _replace_video_coverage(
            response.coverage,
            video_coverage,
            [{
                "modality": "video_frame",
                "start_seconds": min(item["timestamp"] for item in visual_records),
                "end_seconds": max(item["timestamp"] for item in visual_records) + 0.001,
            }],
        ),
        "usage": response.usage.model_copy(update={"local": updated_usage}),
    })


def _read_video_source(request: ReadRequest, source: dict) -> ReadResponse:
    if request.host_capabilities.video_input != "native":
        return _blocked(
            request,
            ErrorCode.MISSING_CAPABILITY,
            "The host must support native video input to inspect this direct video",
            action_required=True,
        )
    source_url = str(source["url"])
    if source.get("sensitive_url") or source.get("unsafe_handoff_url") or not _video_handoff_url_is_safe(source_url):
        return _blocked(
            request,
            ErrorCode.MISSING_CAPABILITY,
            "This media URL has signed or unknown query parameters and cannot be sent to the host model",
        )
    resource_id = f"resource-{hashlib.sha256(source_url.encode()).hexdigest()[:16]}"
    source_revision = hashlib.sha256(source_url.encode()).hexdigest()
    material_id = _material_id(
        source_url, source_revision, request.session_id, request.request_id
    )
    block_id = "blk-video-0001"
    if request.retention.derived_ttl_seconds > 0:
        try:
            _store_material(material_id, {
                "source_kind": "video",
                "source_url": source_url,
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": "",
                "title_text": request.source.label or source.get("title") or Path(urlparse(source_url).path).name or "Video",
                "visual_resources": [{
                    "resource_id": resource_id,
                    "source_block_id": block_id,
                    "source_url": source_url,
                }],
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": 0,
                    "returned_chars": 0,
                    "returned_tokens": 0,
                    "expansion_rounds": 0,
                    "returned_ranges": [],
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            pass
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.WAITING_FOR_HOST,
        result=ReadResult.NEEDS_HOST,
        blocks=[MaterialBlock(
            id=block_id,
            kind=MaterialKind.VIDEO,
            content=VideoContent(
                type="video",
                resource_id=resource_id,
                alt_text=request.goal or request.source.label or source.get("title") or "Source video",
                mime_type=str(source["mime_type"]),
            ),
            origin=MaterialOrigin.SOURCE_VIDEO,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id=block_id,
                quote=request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Direct video source",
            ),
            order=0,
        )],
        resources=[ResourceRef(
            resource_id=resource_id,
            mime_type=str(source["mime_type"]),
            delivery=HostDelivery(type="uri", uri=source_url),
            delivery_evidence="unverified",
        )],
        needs=[Need(
            action_id="inspect-video-0001",
            type="inspect_video",
            purpose=request.goal or "Inspect the direct video",
            resource_ids=[resource_id],
            source_block_ids=[block_id],
            reason=(
                "The host advertises native video input; Envoy passes through this public source without local model processing."
            ),
            max_resources=1,
        )],
        coverage=Coverage(modalities={
            "video": CoverageItem(
                discovered=1,
                extracted=0,
                returned=1,
                delivered=1,
                extraction="unknown",
                unresolved=["Video interpretation is delegated to the host model"],
                details={
                    "mime_type": source["mime_type"],
                    "processing": "host_native_page" if source.get("source_type") == "platform_page" else "host_native",
                },
            )
        }),
    )


def _read_video_media_url(request: ReadRequest, media_source: dict) -> ReadResponse:
    if request.host_capabilities.video_input == "native" and not request.scope.include_visual:
        return _read_video_source(request, media_source)
    media_url = str(media_source["url"])
    try:
        transcript_response = _read_video_asr(request, media_url)
        return _attach_video_frames(request, media_url, transcript_response)
    except MediaUnsafeAddress:
        return _blocked(
            request,
            ErrorCode.SOURCE_UNAVAILABLE,
            "Media extraction resolved a non-public network address",
        )
    except MediaNoSpeech as exc:
        if request.scope.include_visual:
            frame_base = _video_frame_base_response(
                request,
                media_url,
                request.source.label or "",
                None,
                [str(exc)],
            )
            return _attach_video_frames(request, media_url, frame_base)
        return _blocked(request, ErrorCode.EMPTY_CONTENT, str(exc))
    except MediaCapabilityMissing as exc:
        if request.scope.include_visual:
            frame_base = _video_frame_base_response(
                request,
                media_url,
                request.source.label or "",
                None,
                [f"Local speech extraction is unavailable ({exc})"],
            )
            return _attach_video_frames(request, media_url, frame_base)
        if request.host_capabilities.video_input == "native":
            return _read_video_source(request, media_source)
        return _blocked(
            request,
            ErrorCode.MISSING_CAPABILITY,
            f"Direct video requires local ASR or a host native-video capability ({exc})",
            action_required=True,
        )
    except MediaBudgetExceeded as exc:
        return _blocked(request, ErrorCode.BUDGET_EXCEEDED, str(exc))
    except MediaProcessingError as exc:
        return _blocked(
            request,
            ErrorCode.NETWORK_ERROR,
            f"Direct video local processing failed ({exc})",
        )


@_serialize_read_request
def read_public_page(request: ReadRequest) -> ReadResponse:
    source_url = request.source.url
    dns_cache: dict[str, bool] = {}
    browser_snapshot = None
    browser_snapshot_for_video = False
    if not _is_public_http_url(source_url, dns_cache):
        return _blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "Only public HTTP(S) URLs are allowed")
    source_path = urlparse(source_url).path.lower()
    direct_video_path = source_path.endswith(DIRECT_VIDEO_SUFFIXES)
    direct_douyin_note = bool(re.match(r"^/note/[^/]+", source_path))
    if (
        not direct_video_path
        and not direct_douyin_note
        and has_specialized_extractor(source_url)
    ):
        subtitle_result = extract_public_subtitles(
            source_url,
            preferred_language=request.video_subtitle_language,
            max_download_bytes=request.budget.max_download_bytes,
            max_work_seconds=_remaining_work_seconds(request),
            public_url_validator=lambda url: _is_public_http_url(url, dns_cache),
        )
        if subtitle_result.get("status") == "ready":
            transcript_response = _video_subtitle_response(request, source_url, subtitle_result)
            return _attach_video_frames(request, source_url, transcript_response)
        if subtitle_result.get("status") == "unsafe_target":
            return _blocked(
                request,
                ErrorCode.SOURCE_UNAVAILABLE,
                "Media extractor resolved a non-public network address",
            )
        if subtitle_result.get("status") not in {"not_single_video", "extractor_unavailable"}:
            status = str(subtitle_result.get("status") or "failed")
            message = str(subtitle_result.get("error") or "")
            auth_markers = ("login", "sign in", "403", "authentication", "fresh cookies")
            if any(marker in message.lower() for marker in auth_markers):
                browser_snapshot, browser_error = _read_with_isolated_browser(
                    request,
                    source_url,
                    dns_cache,
                )
                if browser_snapshot is not None:
                    browser_snapshot_for_video = _browser_page_has_video(
                        browser_snapshot,
                        request.source.kind.value,
                    )
                else:
                    error_code = _browser_read_failure_code(browser_error)
                    return _blocked(
                        request,
                        error_code,
                        (
                            f"Public subtitle extraction requires access ({message or status}). "
                            f"{browser_error or 'Envoy could not read the page in its isolated browser.'}"
                        ),
                        action_required=True,
                    )
            if browser_snapshot is None:
                try:
                    transcript_response = _read_video_asr(request, source_url)
                    return _attach_video_frames(request, source_url, transcript_response)
                except MediaUnsafeAddress:
                    return _blocked(
                        request,
                        ErrorCode.SOURCE_UNAVAILABLE,
                        "Media extraction resolved a non-public network address",
                    )
                except MediaNoSpeech as exc:
                    if request.scope.include_visual:
                        frame_base = _video_frame_base_response(
                            request,
                            source_url,
                            str(subtitle_result.get("title") or ""),
                            subtitle_result.get("duration"),
                            [str(exc)],
                        )
                        return _attach_video_frames(request, source_url, frame_base)
                    return _blocked(request, ErrorCode.EMPTY_CONTENT, str(exc))
                except MediaCapabilityMissing as exc:
                    if request.scope.include_visual:
                        frame_base = _video_frame_base_response(
                            request,
                            source_url,
                            str(subtitle_result.get("title") or ""),
                            subtitle_result.get("duration"),
                            [f"No transcript is available ({exc})"],
                        )
                        return _attach_video_frames(request, source_url, frame_base)
                    subtitle_state = (
                        "No public subtitle track is available"
                        if status == "no_subtitles"
                        else "Public subtitle extraction failed"
                    )
                    return _blocked(
                        request,
                        ErrorCode.MISSING_CAPABILITY,
                        f"{subtitle_state}; {exc}",
                        action_required=True,
                    )
                except MediaBudgetExceeded as exc:
                    return _blocked(request, ErrorCode.BUDGET_EXCEEDED, str(exc))
                except MediaProcessingError as exc:
                    if request.scope.include_visual:
                        frame_base = _video_frame_base_response(
                            request,
                            source_url,
                            str(subtitle_result.get("title") or ""),
                            subtitle_result.get("duration"),
                            [f"Local speech extraction failed ({exc})"],
                        )
                        return _attach_video_frames(request, source_url, frame_base)
                    subtitle_message = message or status
                    return _blocked(
                        request,
                        ErrorCode.NETWORK_ERROR,
                        f"Public subtitle extraction failed ({subtitle_message}); local ASR failed ({exc})",
                    )
    if request.source.kind.value == "image" or source_path.endswith(IMAGE_SUFFIXES):
        try:
            return _read_image_source(request, source_url, dns_cache)
        except PublicPageError as exc:
            return _blocked(
                request,
                exc.code,
                str(exc),
                action_required=exc.code == ErrorCode.MISSING_CAPABILITY,
            )
        except httpx.HTTPError as exc:
            return _blocked(
                request,
                ErrorCode.NETWORK_ERROR,
                f"Image request failed ({type(exc).__name__}) for {_redact_url(source_url)}",
            )
    if request.source.kind.value == "document" or source_path.endswith(".pdf"):
        try:
            return _read_pdf_source(request, source_url, dns_cache)
        except PublicPageError as exc:
            return _blocked(
                request,
                exc.code,
                str(exc),
                action_required=exc.code == ErrorCode.MISSING_CAPABILITY,
            )
        except httpx.HTTPError as exc:
            return _blocked(
                request,
                ErrorCode.NETWORK_ERROR,
                f"PDF request failed ({type(exc).__name__}) for {_redact_url(source_url)}",
            )
    if direct_video_path:
        mime_type = mimetypes.guess_type(source_path)[0] or "video/octet-stream"
        return _read_video_media_url(request, {
            "url": source_url,
            "mime_type": mime_type,
        })
    if request.source.kind.value not in {"auto", "article", "video"}:
        return _blocked(request, ErrorCode.UNSUPPORTED_SOURCE, "The independent Skill currently reads public HTML pages")
    try:
        browser_error = None
        browser_attempted = browser_snapshot is not None
        browser_used = browser_snapshot is not None
        if browser_snapshot is not None:
            final_url, html = browser_snapshot.url, browser_snapshot.html
        else:
            try:
                final_url, html = _fetch_html(
                    source_url,
                    min(MAX_HTML_BYTES, request.budget.max_download_bytes or MAX_HTML_BYTES),
                    dns_cache,
                )
            except DetectedPDF as detected_pdf:
                return _parse_pdf_source(request, detected_pdf.source)
            except DetectedText as detected_text:
                return _parse_text_source(request, detected_text.source)
            except DetectedImage as detected_image:
                if request.host_capabilities.image_input.value != "uri":
                    return _blocked(
                        request,
                        ErrorCode.MISSING_CAPABILITY,
                        "The host must support URI image input to inspect this image source",
                        action_required=True,
                    )
                return _read_image_source(request, source_url, dns_cache)
            except DetectedMedia as detected_media:
                if detected_media.source["mime_type"].startswith("video/"):
                    return _read_video_media_url(request, detected_media.source)
                return _blocked(
                    request,
                    ErrorCode.MISSING_CAPABILITY,
                    f"Standalone media extraction is unavailable for {detected_media.source['mime_type']}",
                )
            except PublicPageError as exc:
                if exc.code != ErrorCode.AUTH_REQUIRED:
                    raise
                browser_page, browser_error = _read_with_isolated_browser(
                    request,
                    source_url,
                    dns_cache,
                )
                browser_attempted = True
                if browser_page is None:
                    return _blocked(
                        request,
                        _browser_read_failure_code(browser_error),
                        f"{exc}. {browser_error or 'The isolated browser could not read the page.'}",
                        action_required=True,
                    )
                browser_used = True
                final_url, html = browser_page.url, browser_page.html
                browser_snapshot = browser_page
                browser_snapshot_for_video = (
                    _browser_page_has_video(browser_page, request.source.kind.value)
                )
        if (
            browser_snapshot is None
            and (request.source.kind.value == "video" or _url_is_video_page(final_url))
        ):
            browser_page, browser_error = _read_with_isolated_browser(
                request,
                final_url,
                dns_cache,
            )
            browser_attempted = True
            if browser_page is not None:
                browser_used = True
                final_url, html = browser_page.url, browser_page.html
                browser_snapshot = browser_page
                browser_snapshot_for_video = _browser_page_has_video(
                    browser_page,
                    request.source.kind.value,
                )
            else:
                browser_snapshot_for_video = (
                    request.source.kind.value == "video"
                    or _url_is_video_page(final_url)
                )
        parser = _PageParser()
        parser.feed(html)
        title = _clean_text(parser.title_parts) or final_url
        source_text_truncated = _source_text_was_truncated(parser.text_parts)
        text = _clean_text(parser.text_parts)
        if _looks_like_access_challenge(title, text):
            if not browser_attempted:
                browser_page, browser_error = _read_with_isolated_browser(
                    request,
                    source_url,
                    dns_cache,
                )
                browser_attempted = True
                if browser_page is not None:
                    browser_used = True
                    final_url, html = browser_page.url, browser_page.html
                    browser_snapshot = browser_page
                    browser_snapshot_for_video = (
                        _browser_page_has_video(browser_page, request.source.kind.value)
                    )
                    parser = _PageParser()
                    parser.feed(html)
                    title = _clean_text(parser.title_parts) or final_url
                    source_text_truncated = _source_text_was_truncated(parser.text_parts)
                    text = _clean_text(parser.text_parts)
        if _looks_like_access_challenge(title, text):
            return _blocked(
                request,
                _browser_read_failure_code(browser_error)
                if isinstance(browser_error, Exception)
                else ErrorCode.CHALLENGE_REQUIRED,
                "The page still requires login or security verification. "
                f"{browser_error or 'Complete the action in Envoy’s isolated browser and retry.'}",
                action_required=True,
            )
        if len(text) < 20 and not parser.images and not parser.video_urls:
            if not browser_attempted:
                browser_page, browser_error = _read_with_isolated_browser(
                    request,
                    source_url,
                    dns_cache,
                )
                browser_attempted = True
                if browser_page is not None:
                    browser_used = True
                    final_url, html = browser_page.url, browser_page.html
                    browser_snapshot = browser_page
                    browser_snapshot_for_video = (
                        _browser_page_has_video(browser_page, request.source.kind.value)
                    )
                    parser = _PageParser()
                    parser.feed(html)
                    title = _clean_text(parser.title_parts) or final_url
                    source_text_truncated = _source_text_was_truncated(parser.text_parts)
                    text = _clean_text(parser.text_parts)
            if _looks_like_access_challenge(title, text):
                return _blocked(
                    request,
                    _browser_read_failure_code(browser_error)
                    if isinstance(browser_error, Exception)
                    else ErrorCode.CHALLENGE_REQUIRED,
                    "The page still requires login or security verification. "
                    f"{browser_error or 'Complete the action in Envoy’s isolated browser and retry.'}",
                    action_required=True,
                )
            if isinstance(browser_error, Exception):
                return _blocked(
                    request,
                    _browser_read_failure_code(browser_error),
                    str(browser_error),
                    action_required=True,
                )
            if len(text) < 20 and not parser.images and not parser.video_urls:
                if browser_attempted and browser_error is None:
                    browser_error = "The isolated browser returned no readable page content."
                detail = f" Browser fallback: {browser_error}" if browser_error else ""
                return _blocked(
                    request,
                    ErrorCode.EMPTY_CONTENT,
                    f"No readable public HTML text was found.{detail}",
                )
        safe_source_url = _redact_url(final_url)
        image_note_page = bool(re.match(
            r"^/note/[^/]+",
            urlparse(final_url).path,
            re.IGNORECASE,
        ))
        image_note_total: int | None = None
        if image_note_page:
            parser.images, image_note_total = extract_douyin_note_gallery(
                html,
                final_url,
            )
        title_block = MaterialBlock(
            id="blk-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=title),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=safe_source_url,
                block_id="blk-title",
                quote=title[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=0,
        )
        visual_budget = max(0, request.budget.max_visual_inputs)
        goal_terms = _goal_terms(request.goal)
        image_candidates = [
            {
                **image,
                "score": _goal_score(f"{image.get('alt', '')} {image.get('url', '')}", goal_terms),
                "source_index": index,
            }
            for index, image in enumerate(parser.images)
        ]
        if goal_terms and not image_note_page:
            image_candidates.sort(key=lambda item: (-int(item["score"]), int(item["source_index"])))
        safe_image_catalog, skipped_sensitive_images = _safe_visual_candidates(
            image_candidates,
            final_url,
            dns_cache,
        )
        selected_images = image_candidates[:visual_budget]
        safe_images = safe_image_catalog[:visual_budget]
        available_chars = max(
            0,
            _response_char_budget(request)
            - len(title)
            - sum(
                len(item["image"].get("alt", ""))
                for item in safe_images
            ),
        )
        initial_token_limit = request.budget.max_response_tokens
        if request.budget.max_total_response_tokens is not None:
            initial_token_limit = (
                min(initial_token_limit, request.budget.max_total_response_tokens)
                if initial_token_limit is not None
                else request.budget.max_total_response_tokens
            )
        title_tokens = _estimated_tokens(title)
        returned_text, returned_end = _take_text_budgeted(
            text,
            0,
            available_chars,
            max(0, initial_token_limit - title_tokens) if initial_token_limit is not None else None,
        )
        returned_text = returned_text.rstrip()
        returned_end = len(returned_text)
        returned_tokens = title_tokens + _estimated_tokens(returned_text)
        text_is_partial = returned_end < len(text)
        image_only = len(text) < 20 and bool(parser.images)
        video_urls = [
            _redact_url(urljoin(final_url, item))
            for item in parser.video_urls
            if _is_public_http_url(urljoin(final_url, item), dns_cache)
        ]
        text_goal_candidates = _goal_candidates(text, goal_terms)
        source_revision = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
        material_id = _material_id(
            final_url, source_revision, request.session_id, request.request_id
        )
        existing_material = _load_matching_material(
            material_id,
            source_revision,
            request.session_id,
        )
        already_returned = existing_material is not None
        if already_returned:
            returned_text = ""
            returned_end = 0
            returned_tokens = 0
            text_is_partial = True
            text_goal_candidates = []
        retention_disabled = request.retention.derived_ttl_seconds <= 0
        can_continue = (
            text_is_partial
            and not source_text_truncated
            and not retention_disabled
            and not already_returned
        )
        source_coverage_unresolved = [
            *(
                ["Text source exceeded the local extraction limit"]
                if source_text_truncated
                else []
            ),
            *(
                ["Some source images were not included in this response"]
                if len(safe_images) < len(parser.images)
                else []
            ),
            *(
                ["Embedded video resources were discovered but not processed"]
                if video_urls
                else []
            ),
            *(
                [
                    "The isolated browser read the video page only; no transcript or video-frame inspection was completed"
                ]
                if browser_snapshot_for_video
                else []
            ),
        ]
        should_store_material = (
            can_continue
            or bool(selected_images and not retention_disabled)
            or (
                not retention_disabled
                and not source_text_truncated
                and len(text) <= MAX_SEARCH_CACHE_CHARS
            )
        )
        continuation_storage_failed = False
        if should_store_material:
            try:
                _store_material(material_id, {
                    "source_url": safe_source_url,
                    "source_revision": source_revision,
                    "session_id": request.session_id,
                    "text": text,
                    "title_text": title,
                    "source_coverage_unresolved": source_coverage_unresolved,
                    "visual_resources": [
                        {
                            "resource_id": item["resource_id"],
                            "source_block_id": item["block_id"],
                            "source_url": _redact_url(item["image_url"]),
                        }
                        for item in safe_image_catalog
                    ],
                    "budget_state": {
                        "max_total_response_chars": request.budget.max_total_response_chars,
                        "max_total_response_tokens": request.budget.max_total_response_tokens,
                        "max_expansion_rounds": (
                            request.budget.max_expansion_rounds
                            if not source_text_truncated
                            else 0
                        ),
                        "returned_chars": len(returned_text),
                        "returned_tokens": returned_tokens,
                        "expansion_rounds": 0,
                        "returned_ranges": [[0, len(returned_text)]] if returned_text else [],
                    },
                    "expires_at": time.time() + min(
                        request.retention.derived_ttl_seconds,
                        MATERIAL_TTL_SECONDS,
                    ),
                })
            except OSError:
                can_continue = False
                continuation_storage_failed = True
        blocks = [title_block]
        if returned_text:
            blocks.append(MaterialBlock(
                id="blk-body-0001",
                kind=MaterialKind.PARAGRAPH,
                content=TextContent(type="text", text=returned_text),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=WebLocator(
                    type="web",
                    source_url=safe_source_url,
                    block_id="blk-body-0001",
                    quote=returned_text[:MAX_LOCATOR_QUOTE_CHARS],
                ),
                order=1,
            ))
        resources = []
        for item in safe_images:
            image = item["image"]
            image_url = item["image_url"]
            block_id = item["block_id"]
            resource_id = item["resource_id"]
            blocks.append(
                MaterialBlock(
                    id=block_id,
                    kind=MaterialKind.IMAGE,
                    content=ImageContent(type="image", resource_id=resource_id, alt_text=image["alt"]),
                    origin=MaterialOrigin.SOURCE_IMAGE,
                    locator=WebLocator(type="web", source_url=safe_source_url, block_id=block_id, quote=image["alt"] or image_url),
                    parent_id="blk-body-0001",
                    order=len(blocks),
                )
            )
            resources.append(
                ResourceRef(
                    resource_id=resource_id,
                    mime_type="image/*",
                    delivery=HostDelivery(type="uri", uri=image_url),
                    delivery_evidence="unverified",
                )
            )
            if len(resources) >= min(MAX_IMAGES, visual_budget):
                break
        image_note_unknown_total = image_note_page and image_note_total is None
        image_partial = (
            len(resources) < len(parser.images)
            or (image_note_total is not None and len(resources) < image_note_total)
            or image_note_unknown_total
        )
        response = ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            material_id=material_id,
            source_revision=source_revision,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL if text_is_partial or source_text_truncated or image_partial or image_only or video_urls or browser_snapshot_for_video else ReadResult.READY,
            blocks=blocks,
            resources=resources,
            coverage=Coverage(
                modalities={
                    "text": CoverageItem(
                        discovered=1 if text else 0,
                        extracted=1 if text else 0,
                        returned=1 if returned_text else 0,
                        extraction=(
                            "partial" if text_is_partial or source_text_truncated
                            else "failed" if not text
                            else "complete"
                        ),
                        unresolved=(
                            [
                                *(
                                    ["Response character budget truncated the extracted text"]
                                    if text_is_partial
                                    else []
                                ),
                                *(
                                    ["Continuation is unavailable because local retention is disabled"]
                                    if len(text) > len(returned_text)
                                    and retention_disabled
                                    and not source_text_truncated
                                    else []
                                ),
                                *(
                                    ["Continuation cache could not be written"]
                                    if continuation_storage_failed
                                    else []
                                ),
                                *(
                                    ["Source text exceeded the local extraction limit; remaining text was not retained"]
                                    if source_text_truncated
                                    else []
                                ),
                            ]
                        ),
                        details={
                            "reader": "isolated_chromium" if browser_used else "static_http",
                            "total_chars": len(text),
                            "returned_chars": len(returned_text),
                            "continuation_available": can_continue,
                            "next_offset": len(returned_text) if can_continue else None,
                            "remaining_chars": max(0, len(text) - returned_end),
                            **_token_budget_details(request, returned_tokens, text[returned_end:]),
                            "source_truncated": source_text_truncated,
                            "image_only": image_only,
                            **(
                                {
                                    "goal_terms": goal_terms,
                                    "goal_candidates": text_goal_candidates,
                                }
                                if goal_terms
                                else {}
                            ),
                        },
                    ),
                    "images": CoverageItem(
                        discovered=len(parser.images),
                        extracted=len(resources),
                        returned=len(resources),
                        delivered=len(resources),
                        extraction="partial" if image_partial else "complete",
                        unresolved=[
                            *(
                                ["Image note album total was not exposed; discovered images may be incomplete"]
                                if image_note_unknown_total
                                else []
                            ),
                            *(
                                ["The note album contains images that were not returned"]
                                if image_note_total is not None and len(resources) < image_note_total
                                else []
                            ),
                            *(
                                ["Image input budget limited returned images"]
                                if len(selected_images) < len(parser.images)
                                else []
                            ),
                            *(
                                ["Image resources with credential-like query parameters were omitted"]
                                if skipped_sensitive_images
                                else []
                            ),
                            *(
                                ["Some image resources could not be safely delivered"]
                                if len(resources) < len(parser.images)
                                and not skipped_sensitive_images
                                and len(selected_images) == len(parser.images)
                                else []
                            ),
                        ],
                        details={
                            **(
                                {
                                "goal_terms": goal_terms,
                                "goal_ranked": True,
                                "selected_candidates": [
                                    {
                                        "source_index": image["source_index"],
                                        "alt": image.get("alt", ""),
                                        "score": image["score"],
                                    }
                                    for image in selected_images
                                ],
                            }
                                if goal_terms and not image_note_page
                                else {}
                            ),
                            **(
                                {
                                    "item_type": "image_note",
                                    "album_total": image_note_total,
                                    "album_total_known": image_note_total is not None,
                                    "discovered_image_count": len(parser.images),
                                    "read_image_count": len(resources),
                                    "uncovered_image_count": (
                                        max(0, image_note_total - len(resources))
                                        if image_note_total is not None
                                        else None
                                    ),
                                    "image_order": [item.resource_id for item in resources],
                                }
                                if image_note_page
                                else {}
                            ),
                        },
                    ),
                    **({"video": CoverageItem(
                        discovered=max(len(video_urls), 1),
                        extracted=0,
                        returned=0,
                        extraction="partial" if browser_snapshot_for_video else "failed" if video_urls else "complete",
                        unresolved=(
                            [
                                "Browser page was read, but video transcript and frames have not been extracted"
                                if browser_snapshot_for_video
                                else "Embedded video was discovered but standalone video extraction is unavailable"
                            ]
                            if video_urls or browser_snapshot_for_video
                            else []
                        ),
                        details={
                            "urls": video_urls,
                            "browser_media_candidates": len(browser_snapshot.media_urls)
                            if browser_snapshot is not None
                            else 0,
                        },
                    )} if video_urls or browser_snapshot_for_video else {}),
                    },
                    unresolved=source_coverage_unresolved,
                ),
        )
        if browser_snapshot_for_video and browser_snapshot is not None:
            response = _attach_browser_video_evidence(
                request,
                browser_snapshot.url,
                list(browser_snapshot.media_urls),
                response,
                bound_media_urls=list(browser_snapshot.page_video_urls),
            )
        return response
    except PublicPageError as exc:
        return _blocked(request, exc.code, str(exc))
    except httpx.HTTPError as exc:
        return _blocked(
            request,
            ErrorCode.NETWORK_ERROR,
            f"Public page request failed ({type(exc).__name__}) for {_redact_url(source_url)}",
        )


def _blocked(
    request: ReadRequest,
    code: ErrorCode,
    message: str,
    action_required: bool = False,
) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[
            ReadError(
                code=code,
                message=message,
                retryable=code == ErrorCode.NETWORK_ERROR,
                action_required=action_required,
            )
        ],
    )
