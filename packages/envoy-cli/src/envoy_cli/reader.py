from __future__ import annotations

import contextvars
import hashlib
import html
import io
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
import zipfile
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

from ._lazy_module import lazy_attr, lazy_module

from .video import extract_public_subtitles, has_specialized_extractor
from .netpolicy import detected_proxy, explicit_proxy, proxy_plan
from .platforms import (
    classify_public_url,
    extract_xhs_note_images,
    is_collection_url,
    is_platform_page,
)
from . import storage
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
    optional_interprocess_lock,
    media_runtime_status,
    probe_media_duration,
    transcribe_audio_file,
)
from .ocr import (
    DEFAULT_OCR_BACKEND,
    OCR_PIPELINE_REVISION,
    _engine_version,
    available_ocr_backends,
    extract_image_ocr,
    ocr_cache_key,
    resolve_ocr_language,
    select_ocr_backend,
)
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ErrorCode,
    EvidenceCheckRequest,
    EvidenceCheckResponse,
    ExpandRequest,
    ExecutionState,
    HostDelivery,
    ImageContent,
    ImageRegionLocator,
    ImageInputCapability,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    MaterialReplayRequest,
    ReadError,
    ReadRequest,
    ReadResponse,
    ReadResult,
    ResourceRef,
    Relation,
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

# Imported on first use: every invocation paid for both of these even when it only
# needed the argument parser (see DEV/LIGHTWEIGHT-WINDOWS.md, L1).
httpx = lazy_module("httpx")
PdfReader = lazy_attr("pypdf", "PdfReader")

# Both modules import httpx at their own module scope; keeping them lazy keeps
# the network stack out of `envoy --version` and `import envoy_cli.reader`.
public_api_fallback = lazy_attr("envoy_cli.fallbacks", "public_api_fallback")
fetch_feed = lazy_attr("envoy_cli.feeds", "fetch_feed")
looks_like_feed_url = lazy_attr("envoy_cli.feeds", "looks_like_feed_url")


MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_TEXT_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_TEXT_CHARS = 2_000_000
MAX_SEARCH_CACHE_CHARS = 256_000
MAX_IMAGES = 40
CHALLENGE_MARKERS = (
    "安全限制",
    "ip存在风险",
    "切换可靠网络",
    "访问过于频繁",
    "请求过于频繁",
    "当前笔记暂时无法浏览",
    "验证码",
    "安全验证",
    "人机验证",
    "登录后继续",
    "verify you are human",
    "checking your browser",
    "captcha",
    # Volcengine / ByteDance WAF interstitial: 36kr and other Chinese portals
    # serve this shell to a plain fetch and open the page only to a browser.
    "正在进行安全检测",
    "检测当前网络环境",
    "安全检测中",
    "just a moment",
    "enable javascript and cookies",
)
MAX_FETCH_ATTEMPTS = 3
MIN_EXPANSION_CHARS = 128
# Upper bound for the subtitle-discovery leg of a video read. Sites whose
# extractor negotiates against a signed API can stall for minutes; the page
# fallback must still get its turn inside the caller's budget.
_SUBTITLE_PHASE_SECONDS = 45
# A site's own JSON endpoint answers in a second or two; give it a short slice
# of the budget and fall through to the page when it cannot.
_PUBLIC_API_FALLBACK_SECONDS = 15
_PUBLIC_API_FALLBACK_MIN_CHARS = 200
# Below this a platform page is a script shell rather than content.
_THIN_PLATFORM_PAGE_CHARS = 300
_EXPIRED_MATERIAL_NEXT_ACTION = (
    "read the source URL again with envoy_read; a retained material can only be reused while its lease is alive"
)

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
TEXT_SUFFIXES = (
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log", ".rst",
)
# ZIP container documents. They are parsed with the standard library only, so
# reading a .docx or .epub link costs nothing at install time.
OFFICE_SUFFIXES = (
    ".docx", ".docm", ".dotx", ".dotm",
    ".pptx", ".pptm", ".potx", ".potm", ".ppsx", ".ppsm",
    ".xlsx", ".xlsm", ".xltx", ".xltm",
    ".epub", ".odt", ".ods",
)
OFFICE_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "application/vnd.ms-word.template.macroenabled.12",
    "application/vnd.ms-word.document.macroenabled.12",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    "application/vnd.openxmlformats-officedocument.presentationml.template",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/vnd.ms-powerpoint.template.macroenabled.12",
    "application/vnd.ms-powerpoint.slideshow.macroenabled.12",
    "application/epub+zip",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    "application/vnd.ms-excel.template.macroenabled.12",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/zip",
}
RTF_SUFFIXES = (".rtf",)
RTF_CONTENT_TYPES = {"application/rtf", "text/rtf", "application/x-rtf"}
# Legacy binary Office files are OLE2 containers we do not parse; they get a
# clear "save it as .docx/.xlsx" answer instead of a generic HTML error.
LEGACY_OFFICE_SUFFIXES = (".doc", ".xls", ".ppt")
# Files Envoy cannot unpack, but which users hit often enough to deserve a
# concrete "save it as" answer instead of a binary-content error.
IWORK_SUFFIXES = (".pages", ".numbers", ".key")
BINARY_SHEET_SUFFIXES = (".xlsb",)
LEGACY_OFFICE_CONTENT_TYPES = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}
_MAX_OFFICE_UNCOMPRESSED = 64 * 1024 * 1024
_MAX_OFFICE_ENTRIES = 4000
_SNIFF_MAX_BYTES = 1 << 20
_PRINTABLE_TABLES = ("utf-8", "gb18030")
TEXT_CONTENT_TYPES = {
    "application/atom+xml",
    "application/rss+xml",
    "application/rdf+xml",
    "application/x-rss+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/tab-separated-values",
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
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SOURCE_UNAVAILABLE,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _http_proxy_configured() -> bool:
    return any(
        os.getenv(name, "").strip()
        for name in (
            "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
            "ALL_PROXY", "all_proxy",
        )
    )


class DetectedPDF(PublicPageError):
    def __init__(self, source: dict):
        super().__init__("The URL resolved to a PDF document", ErrorCode.UNSUPPORTED_SOURCE)
        self.source = source


class DetectedOffice(PublicPageError):
    def __init__(self, source: dict) -> None:
        super().__init__("This URL serves a ZIP container document")
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


PUBLIC_URL = "public"
POLICY_REJECTION = "policy"
DNS_FAILURE = "dns"


def _public_url_rejection(value: str, dns_cache: dict[str, str] | None = None) -> str | None:
    """Why a URL must not be fetched, or ``None`` for a public HTTP(S) URL.

    The verdict is one value rather than a bool because the read path has to
    tell a policy refusal (loopback, private address, embedded credentials)
    from a host name that simply does not resolve; those are different errors
    and one of them is retryable. A host's verdict is cached as this reason,
    so a read attempt resolves a name once and the error code cannot flip
    between two lookups taken milliseconds apart.
    """

    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return POLICY_REJECTION
        if parsed.username or parsed.password:
            return POLICY_REJECTION
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return POLICY_REJECTION
        try:
            addresses = {ipaddress.ip_address(host)}
        except ValueError:
            if dns_cache is not None and host in dns_cache:
                cached = dns_cache[host]
                return None if cached == PUBLIC_URL else cached
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(
                        host,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                    )
                }
            except OSError:
                if dns_cache is not None:
                    dns_cache[host] = DNS_FAILURE
                return DNS_FAILURE
            verdict = PUBLIC_URL if _all_global(addresses) else POLICY_REJECTION
            if dns_cache is not None:
                dns_cache[host] = verdict
            return None if verdict == PUBLIC_URL else verdict
        return None if _all_global(addresses) else POLICY_REJECTION
    except (ValueError, TypeError):
        return POLICY_REJECTION


def _all_global(addresses: set) -> bool:
    return bool(addresses) and all(address.is_global for address in addresses)


_GOOGLE_DOC_FORMATS = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pptx"}


def _with_flag(parsed, flag: str) -> str:
    """Ask a share link for the bytes while leaving its other parameters alone.

    Re-encoding the whole query would rewrite values like `resid=ABC!123`, so the
    raw parameter text is kept and only the requested flag is replaced.
    """

    kept = [
        part
        for part in parsed.query.split("&")
        if part and not part.startswith(f"{flag}=")
    ]
    kept.append(f"{flag}=1")
    return parsed._replace(query="&".join(kept)).geturl()


def share_export_url(url: str) -> str:
    """Turn a share/view link into the URL that returns the file itself.

    Drive and Docs answer their share links with a JavaScript shell, so a reader
    that fetches the link as written sees an empty page. Their export endpoints
    return the bytes under the same permissions. Dropbox is the same story with
    `dl=0` instead of `dl=1`. Anything unrecognised is returned unchanged.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"drive.google.com", "docs.google.com"}:
        segments = [part for part in parsed.path.split("/") if part]
        if len(segments) >= 3 and segments[1] == "d":
            kind, file_id = segments[0], segments[2]
            if kind == "file" and host == "drive.google.com":
                return f"https://drive.google.com/uc?export=download&id={file_id}"
            if kind in _GOOGLE_DOC_FORMATS and host == "docs.google.com":
                return (
                    f"https://docs.google.com/{kind}/d/{file_id}/export"
                    f"?format={_GOOGLE_DOC_FORMATS[kind]}"
                )
        return url
    if host == "1drv.ms" or host == "onedrive.live.com":
        return _with_flag(parsed, "download")
    if (host == "dropbox.com" or host.endswith(".dropbox.com")) and (
        host != "dl.dropboxusercontent.com"
    ):
        return _with_flag(parsed, "dl")
    return url


def _is_public_http_url(value: str, dns_cache: dict[str, str] | None = None) -> bool:
    return _public_url_rejection(value, dns_cache) is None


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
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = host
    if port is not None:
        netloc = f"{netloc}:{port}"
    safe_query = [
        (key, "[REDACTED]" if _is_sensitive_query_key(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse((
        parsed.scheme,
        netloc,
        parsed.path,
        parsed.params,
        urlencode(safe_query),
        "",
    ))


def _media_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return f"{parsed.scheme}://{host}"
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme}://{netloc}"


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


def _is_probable_ui_image(image: dict) -> bool:
    image_url = urlparse(str(image.get("url") or ""))
    filename = image_url.path.rsplit("/", 1)[-1]
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", filename).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    markers = {
        "avatar", "background", "bg", "btn", "button", "close", "icon",
        "logo", "loading", "placeholder", "sprite",
    }
    return bool(markers & set(normalized.split()))


def _safe_visual_candidates(
    images: list[dict],
    page_url: str,
    dns_cache: dict[str, str],
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


def _download_image_for_ocr(
    image_url: str,
    *,
    max_bytes: int,
    dns_cache: dict[str, str],
) -> tuple[Path, str]:
    if not _is_public_http_url(image_url, dns_cache):
        raise PublicPageError("OCR image URL is not a public HTTP(S) address")
    root = _material_root() / "ocr-inputs"
    root.mkdir(mode=0o700, exist_ok=True)
    current_url = image_url
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False, trust_env=False, proxy=explicit_proxy()) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url, dns_cache):
                raise PublicPageError("OCR image redirect resolved to a non-public address")
            with client.stream("GET", current_url, headers={"User-Agent": "Envoy/0.1"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise PublicPageError("OCR image returned an invalid redirect")
                    current_url = urljoin(current_url, location)
                    continue
                return _download_image_response(response, root, max_bytes)
    raise PublicPageError("OCR image exceeded redirect limit")


def _download_image_response(response, root: Path, max_bytes: int) -> tuple[Path, str]:
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if not content_type.startswith("image/"):
        raise PublicPageError(
            f"OCR source returned unsupported content type: {content_type or 'unknown'}",
            ErrorCode.UNSUPPORTED_SOURCE,
        )
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
        "image/gif": ".gif",
    }.get(content_type, ".img")
    digest = hashlib.sha256()
    temporary = tempfile.NamedTemporaryFile(
        prefix=".ocr-", suffix=extension, dir=root, delete=False
    )
    total = 0
    try:
        with temporary:
            for chunk in response.iter_bytes(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaBudgetExceeded("OCR image exceeded max_download_bytes")
                digest.update(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        target = root / f"{digest.hexdigest()}{extension}"
        legacy_target = root / f"{digest.hexdigest()}.img"
        if not target.exists() and legacy_target.is_file():
            try:
                legacy_target.rename(target)
            except OSError:
                target = legacy_target
        if not target.exists():
            os.replace(temporary.name, target)
        else:
            Path(temporary.name).unlink(missing_ok=True)
        return target, digest.hexdigest()
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def _ocr_zero_confidence_regions(regions) -> int:
    count = 0
    for region in regions or []:
        try:
            count += float(region.get("confidence") or 0) <= 0
        except (AttributeError, TypeError, ValueError):
            continue
    return count


def _read_cached_image_ocr(
    image_path: Path,
    image_sha256: str,
    *,
    profile: str,
    language: str,
    backend: str = DEFAULT_OCR_BACKEND,
) -> dict:
    cache_root = _material_root() / "ocr-cache"
    cache_root.mkdir(mode=0o700, exist_ok=True)
    available = available_ocr_backends()
    selected_backend = select_ocr_backend(backend, available=available)
    # The lookup key has to be computable before extraction, so it uses the
    # language the backend is configured for. Environment-aware backends (the
    # Tesseract traineddata fallback) may report a narrower language in the
    # result, and the entry is still stored under this stable key.
    cache_language = resolve_ocr_language(selected_backend, language)
    cache_engine_version = (
        f"{_engine_version(selected_backend)}+{OCR_PIPELINE_REVISION}"
    )
    key = ocr_cache_key(
        image_sha256,
        profile=profile,
        language=cache_language,
        backend=selected_backend,
        engine_version=cache_engine_version,
    )
    cache_path = cache_root / f"{key}.json"
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("source_sha256") == image_sha256:
            return {**cached, "cache_hit": True}
    except (FileNotFoundError, OSError, ValueError):
        pass
    result = extract_image_ocr(
        image_path,
        profile=profile,
        language=language,
        backend=selected_backend,
        available=available,
    )
    result_backend = str(result.get("backend") or selected_backend)
    result_language = str(result.get("language") or cache_language)
    cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    try:
        cache_path.chmod(0o600)
    except OSError:
        pass
    return {
        **result,
        "backend": result_backend,
        "language": result_language,
        "cache_hit": False,
    }


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


# A challenge interstitial is a stub page: title, one sentence of script or a
# captcha box. Read as full text those markers also show up inside real
# articles -- a release note that discusses captchas, a security-industry post,
# this project's own changelog -- so a marker only counts when the title
# carries it or the page body is too short to be the source itself.
CHALLENGE_MARKER_MAX_CHARS = 400


def _looks_like_access_challenge(title: str, text: str) -> bool:
    title_haystack = title.lower()
    if any(marker in title_haystack for marker in CHALLENGE_MARKERS):
        return True
    if len(text.strip()) > CHALLENGE_MARKER_MAX_CHARS:
        return False
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


MISSING_PAGE_MARKERS = (
    "你访问的页面不见了",
    "笔记不存在",
    "笔记已删除",
    "该内容已被删除",
    "内容已删除",
    "page not found",
    "this page does not exist",
)


def _looks_like_missing_page(title: str, text: str) -> bool:
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in MISSING_PAGE_MARKERS)


EGRESS_CHALLENGE_MARKERS = (
    "安全限制",
    "ip存在风险",
    "ip 存在风险",
    "切换可靠网络",
    "访问过于频繁",
    "请求过于频繁",
    "too many requests",
    "300012",
    "请求存在异常",
    "限制本次访问",
)


LOGIN_WALL_MARKERS = (
    "log in to continue",
    "sign in to continue",
    "you must be logged in",
    "sign up for instagram",
    "log in to instagram",
    "post isn't available",
    "this post isn't available",
    "create an account to continue",
    "join to view",
    "登录后查看",
    "登录后继续",
    "请先登录",
    "扫码登录",
)
LOGIN_WALL_MAX_CHARS = 600


def _looks_like_login_wall(title: str, text: str) -> bool:
    """A short page that is a sign-in prompt rather than the source itself.

    Pointing a reader at a login wall yields a plausible-looking excerpt that is
    entirely about logging in. Counting that as partial text would inflate
    coverage with text the human already knows; the honest answer is "this page
    wants a session".
    """

    if len(text.strip()) > LOGIN_WALL_MAX_CHARS:
        return False
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in LOGIN_WALL_MARKERS)


def _looks_like_machine_error_payload(text: str) -> bool:
    """True when a browser-read "page" is really a JSON error envelope.

    Some sites answer an automated visit with a 200 response whose body is a
    short JSON object such as ``{"error":{"message":"...restricted..."}}``.
    Parsed as HTML that becomes a hundred characters of error JSON, which would
    be handed to the caller as if it were the page's material.
    """

    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    if len(stripped) > 600 or "\n\n" in stripped:
        return False
    lowered = stripped.lower()
    if any(marker in lowered for marker in ('"error"', '"errmsg"', '"errorcode"')):
        return True
    code = re.search(r'"code"\s*:\s*(-?\d+)', lowered)
    return bool(code and code.group(1) not in {"0", "200"})


def _challenge_suggests_egress_issue(title: str, text: str) -> bool:
    """True when a block page blames the network exit rather than the visitor."""
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in EGRESS_CHALLENGE_MARKERS)


def _read_with_isolated_browser(
    request: ReadRequest,
    source_url: str,
    dns_cache: dict[str, str],
    only_proxy: bool = False,
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
            only_proxy=only_proxy,
        )
    except MediaBudgetExceeded as exc:
        raise PublicPageError(str(exc), ErrorCode.BUDGET_EXCEEDED) from exc
    except browser.BrowserChallengeRequired as exc:
        return None, exc
    except (browser.BrowserRuntimeUnavailable, browser.BrowserReadError) as exc:
        # The caller distinguishes these by type and maps them to ErrorCode, so
        # the failure must stay an exception rather than a formatted string.
        return None, exc
    except Exception as exc:
        return None, exc
    if not _is_public_http_url(page.url, dns_cache):
        return None, PublicPageError(
            "The browser resolved the source to a non-public address",
            ErrorCode.SOURCE_UNAVAILABLE,
        )
    return page, None


def _browser_read_failure_code(error) -> ErrorCode:
    from .browser import BrowserChallengeRequired, BrowserReadError, BrowserRuntimeUnavailable

    if isinstance(error, BrowserRuntimeUnavailable):
        # A missing browser binary or runtime is a capability gap, not a page
        # that demanded credentials; doctor and the model can act on that.
        return ErrorCode.MISSING_CAPABILITY
    if isinstance(error, BrowserChallengeRequired):
        return ErrorCode.CHALLENGE_REQUIRED
    if isinstance(error, BrowserReadError):
        return ErrorCode.NETWORK_ERROR
    if isinstance(error, TimeoutError) or type(error).__name__ == "TimeoutError":
        return ErrorCode.NETWORK_ERROR
    # A browser driver failure (navigation reset, connection closed, target
    # crashed) is an egress/transport problem, not a page asking for a sign-in.
    # Labelling it `auth_required` told the model to run `envoy_auth` on a site
    # that never presented a login wall.
    message = str(error)
    if "net::ERR" in message or "Page.goto" in message or "Target closed" in message:
        return ErrorCode.NETWORK_ERROR
    return ErrorCode.AUTH_REQUIRED


def _blocked_challenge_code(error) -> ErrorCode:
    """Code for a page that already presented a verification wall.

    The static body says the source demands login or verification. A fallback
    browser that cannot even start does not turn that into a capability gap:
    the page still asks the human for something, so keep the challenge code and
    let the message name the runtime failure too.
    """

    from .browser import BrowserRuntimeUnavailable

    if isinstance(error, BrowserRuntimeUnavailable):
        return ErrorCode.CHALLENGE_REQUIRED
    if isinstance(error, Exception):
        return _browser_read_failure_code(error)
    return ErrorCode.CHALLENGE_REQUIRED


def _browser_page_has_video(page, requested_kind: str) -> bool:
    path = urlparse(page.url).path.lower()
    return (
        requested_kind == "video"
        or bool(page.page_video_urls)
        or classify_public_url(page.url) == "video_page"
        or bool(re.search(r"/(?:video|watch|shorts?)/", path))
    )


def _should_read_media_first(url: str, include_visual: bool) -> bool:
    """Decide whether a page URL is a single video worth a media-first read.

    Media extractors also match pages that merely embed a player (a tweet, a
    subreddit thread, a product page, a record page). Reading those through
    subtitles/ASR first turns a perfectly readable link into a
    missing-capability block, so only a single-video page - or a caller that
    asked for visuals - takes that route.
    """
    if _SKIP_MEDIA_FIRST.get():
        return False
    return include_visual or _url_is_video_page(url) or _is_single_video_path(url)


# Set while a video page is being re-read as plain text after the media route
# found nothing readable (no subtitle track and no local ASR). Without this the
# retry would immediately take the same media route again.
_SKIP_MEDIA_FIRST: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "envoy_skip_media_first", default=False
)

# A public endpoint sometimes answers with real but short material (a three
# comment forum item, a one line package description). That answer is below the
# floor for *replacing* a page, but when the page itself turns out to be
# unreachable it is strictly better than a block, so it is kept here and only
# used as the last resort.
_SHORT_PUBLIC_FALLBACK: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "envoy_short_public_fallback", default=None
)


def _url_is_video_page(value: str) -> bool:
    return (
        classify_public_url(value) == "video_page"
        or bool(re.search(r"/(?:video|watch|shorts?)/", urlparse(value).path.lower()))
    )


def _is_single_video_path(value: str) -> bool:
    """A path ending in a player segment is one video, not a page with a player."""

    return bool(
        re.search(r"/(?:video|watch|shorts?)(?:/|$)", urlparse(value).path.lower())
    )


def _http_media_candidates(values: list[str] | None) -> list[str]:
    """Keep only directly downloadable media URLs.

    A page can expose its player internals next to the real file: Douyin
    publishes a ``blob:`` MSE source while a ``<source>`` element carries the
    signed CDN URL. Counting the ``blob:`` entry as a competing candidate hid
    the single URL that can actually be fetched.
    """

    candidates = [
        text
        for text in (str(value or "").strip() for value in values or [])
        if text.lower().startswith(("http://", "https://"))
    ]
    return list(dict.fromkeys(candidates))


def _is_cover_preview_media_url(url: str) -> bool:
    """True for the feed's static cover-preview stack, not a title source."""

    from .browser import _is_preview_media_url

    return _is_preview_media_url(url)


_PAGE_MEDIA_PROBE_BYTES = 64 * 1024
# A prefix probe spends a request per candidate, so it obeys a hop cap, its own
# wall-clock deadline, and a byte allowance drawn from the download budget: a
# page cannot turn a size hint into an unbounded fetch.
_PAGE_MEDIA_PROBE_MAX_HOPS = 5
_PAGE_MEDIA_PROBE_MAX_CANDIDATES = 5
_PAGE_MEDIA_PROBE_DEADLINE_SECONDS = 15.0


def _probe_page_media(
    url: str,
    dns_cache: dict[str, str],
    *,
    deadline: float | None = None,
) -> tuple[str, str, int] | None:
    """Fingerprint the first bytes of a page media URL.

    Redirects are followed here instead of by httpx so every hop passes the
    public-address check: a CDN that answers ``302 http://127.0.0.1/...`` must
    be refused before the second request is sent. The digest covers only the
    first :data:`_PAGE_MEDIA_PROBE_BYTES`, so it proves a shared prefix, never
    file equality; callers compare the reported full size as well.
    """

    if not str(url).lower().startswith(("http://", "https://")):
        return None
    if not _is_public_http_url(url, dns_cache):
        return None
    if deadline is None:
        deadline = time.monotonic() + _PAGE_MEDIA_PROBE_DEADLINE_SECONDS
    headers = {
        "Range": f"bytes=0-{_PAGE_MEDIA_PROBE_BYTES - 1}",
        "User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)",
    }
    for proxy in proxy_plan():
        if time.monotonic() >= deadline:
            return None
        try:
            with httpx.Client(
                timeout=httpx.Timeout(10),
                follow_redirects=False,
                trust_env=False,
                proxy=proxy,
            ) as client:
                current = url
                for _hop in range(_PAGE_MEDIA_PROBE_MAX_HOPS + 1):
                    if time.monotonic() >= deadline:
                        return None
                    with client.stream("GET", current, headers=headers) as response:
                        status = int(response.status_code)
                        if status in {301, 302, 303, 307, 308}:
                            location = str(response.headers.get("location") or "").strip()
                            if not location:
                                return None
                            current = urljoin(current, location)
                            if not _is_public_http_url(current, dns_cache):
                                return None
                            continue
                        if status < 200 or status >= 400:
                            return None
                        content_type = str(response.headers.get("content-type") or "").lower()
                        content_length = _page_media_total_bytes(
                            response.headers.get("content-range"),
                            response.headers.get("content-length"),
                        )
                        digest = hashlib.sha256()
                        remaining = _PAGE_MEDIA_PROBE_BYTES
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            digest.update(chunk[:remaining])
                            remaining -= min(len(chunk), remaining)
                            if remaining <= 0:
                                break
                        return digest.hexdigest(), content_type, content_length
                return None
        except (httpx.TimeoutException, httpx.TransportError):
            continue
    return None


def _page_media_total_bytes(content_range: str | None, content_length: str | None) -> int:
    """Full media size, preferring the range total over the partial chunk size."""

    total = str(content_range or "").rpartition("/")[2].strip()
    for value in (total, content_length):
        try:
            size = int(str(value or "").strip())
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return 0


def _media_identity_key(identity: tuple[str, str, int] | None) -> tuple[str, str, int]:
    """The full identity of one file: prefix digest, type, and total size."""

    if identity is None:
        return ("", "", 0)
    digest, content_type, size = identity
    return (str(digest), str(content_type or ""), int(size or 0))


def _probe_page_media_identities(
    candidates: list[str],
    dns_cache: dict[str, str],
    *,
    max_probe_bytes: int | None = None,
    deadline: float | None = None,
) -> tuple[dict[str, tuple[str, str, int]], int]:
    """Probe candidates in DOM order until bytes or time run out.

    Returns the identities that came back and the bytes the probing charged;
    a failed probe still spends a request, so it is charged too.
    """

    identities: dict[str, tuple[str, str, int]] = {}
    spent = 0
    remaining = max_probe_bytes
    for candidate in candidates[:_PAGE_MEDIA_PROBE_MAX_CANDIDATES]:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if remaining is not None:
            if remaining < _PAGE_MEDIA_PROBE_BYTES:
                break
            remaining -= _PAGE_MEDIA_PROBE_BYTES
        spent += _PAGE_MEDIA_PROBE_BYTES
        identity = _probe_page_media(candidate, dns_cache)
        if identity is not None:
            identities[candidate] = identity
    return identities, spent


def _order_media_urls(
    candidates: list[str],
    identities: dict[str, tuple[str, str, int]],
) -> list[str]:
    """Group mirrors of one file and order groups from the largest file down.

    Two URLs are one file only when digest, content type, *and* total size
    match; a shared 64 KiB prefix with a different size is two files, and
    treating it as a mirror is exactly how a recommendation outranked the post.
    Unprobed candidates keep their DOM order after the known files.
    """

    groups: dict[tuple, dict] = {}
    ordered_groups: list[dict] = []
    for url in candidates:
        identity = identities.get(url)
        key = ("file", *_media_identity_key(identity)) if identity is not None else ("unprobed", url)
        group = groups.get(key)
        if group is None:
            group = {
                "urls": [],
                "bytes": _media_identity_key(identity)[2] if identity is not None else 0,
                "probed": identity is not None,
            }
            groups[key] = group
            ordered_groups.append(group)
        group["urls"].append(url)
    ordered_groups.sort(key=lambda item: (0 if item["probed"] else 1, -int(item["bytes"])))
    return list(dict.fromkeys(url for group in ordered_groups for url in group["urls"]))


def _rank_page_media_urls(
    candidates: list[str],
    dns_cache: dict[str, str],
    *,
    max_probe_bytes: int | None = None,
    deadline: float | None = None,
) -> list[str]:
    """Order player sources from the largest file down.

    One player publishes the same title through several signed URLs (mirror
    CDNs, an ``aweme/v1/play`` redirect) and a feed page sometimes binds a short
    teaser clip beside it. Grouping real mirrors keeps one entry per file;
    ordering groups by size keeps a 200 KB cover teaser from standing in for
    the real title when both are exposed.

    ``max_probe_bytes`` and ``deadline`` bound the probing requests themselves,
    which is a network cost the download budget also pays. A caller that omits
    them keeps the historical unbounded behaviour for ranking-only use.
    """

    identities, _spent = _probe_page_media_identities(
        candidates,
        dns_cache,
        max_probe_bytes=max_probe_bytes,
        deadline=deadline,
    )
    return _order_media_urls(candidates, identities)


def _choose_page_media_url(
    candidates: list[str],
    dns_cache: dict[str, str],
) -> tuple[str | None, int]:
    """Choose one player source, grouping mirrors of the same file together."""

    ranked = _rank_page_media_urls(candidates, dns_cache)
    if not ranked:
        return None, 0
    return ranked[0], len(ranked)


def _select_browser_video_url(
    candidates: list[str],
    dns_cache: dict[str, str],
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


_WORK_ID_PATH_PATTERNS = (
    re.compile(r"/(?:video|videos|shorts|reel|reels|watch|p|status|note|item|moments?)/([0-9A-Za-z_-]{6,})"),
    re.compile(r"(?<![0-9A-Za-z])(BV[0-9A-Za-z]{8,})"),
)
_WORK_ID_QUERY_KEYS = (
    "v",
    "vid",
    "video_id",
    "videoid",
    "item_id",
    "aweme_id",
    "note_id",
    "object_id",
)


def _page_work_tokens(source_url: str) -> list[str]:
    """Identifiers the requested page uses for its own work.

    A media URL that repeats one of these is the requested post; a
    recommendation repeats a different id. Only long, id-shaped values count,
    so a shared host or a generic ``video`` path segment decides nothing.
    """

    try:
        parsed = urlparse(source_url)
    except ValueError:
        return []
    tokens: list[str] = []
    path = str(parsed.path or "")
    for pattern in _WORK_ID_PATH_PATTERNS:
        for match in pattern.finditer(path):
            tokens.append(match.group(1))
    for segment in path.split("/"):
        value = segment.strip()
        if len(value) >= 6 and value.isdigit():
            tokens.append(value)
    for key, value in parse_qsl(parsed.query or "", keep_blank_values=False):
        if str(key).lower() in _WORK_ID_QUERY_KEYS:
            tokens.append(str(value).strip())
    return list(dict.fromkeys(token.lower() for token in tokens if len(token) >= 6))


def _media_url_carries_token(candidate: str, tokens: list[str]) -> str | None:
    lowered = candidate.lower()
    for token in tokens:
        if token in lowered:
            return token
    return None


def _browser_media_binding(
    source_url: str,
    candidates: list[str],
    identities: dict[str, tuple[str, str, int]],
) -> dict:
    """Decide which candidate may be published as the requested post.

    Signals, strongest first, using only what the browser page exposes:

    * the requested work id appearing in the media URL (a CDN path or signed
      filename that repeats the post id),
    * a page that exposes exactly one distinct file.

    Two distinct files with no matching id is not a ranking problem: byte size
    says nothing about which file the post is, so the honest answer is
    "unconfirmed" rather than the largest neighbour.
    """

    tokens = _page_work_tokens(source_url)
    matched: list[tuple[str, str]] = []
    for candidate in candidates:
        token = _media_url_carries_token(candidate, tokens)
        if token is not None:
            matched.append((candidate, token))
    if len(matched) == 1:
        return {
            "selected_url": matched[0][0],
            "reason": "work_id",
            "work_id": matched[0][1],
        }
    if len(matched) > 1:
        matched_urls = [url for url, _token in matched]
        keys = {_media_identity_key(identities[url]) for url in matched_urls if url in identities}
        if len(keys) == 1 and all(url in identities for url in matched_urls):
            return {
                "selected_url": matched_urls[0],
                "reason": "work_id",
                "work_id": matched[0][1],
            }
        return {"selected_url": None, "reason": "unconfirmed", "work_id": None}
    if len(candidates) == 1:
        return {"selected_url": candidates[0], "reason": "single_player", "work_id": None}
    probed = {url: identity for url, identity in identities.items() if url in candidates}
    keys = {_media_identity_key(identity) for identity in probed.values()}
    if keys and len(keys) == 1 and len(probed) == len(candidates):
        return {"selected_url": candidates[0], "reason": "single_player", "work_id": None}
    return {"selected_url": None, "reason": "unconfirmed", "work_id": None}


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


def _chapter_span_state(
    chapter_blocks: list[MaterialBlock],
    duration: float | None,
) -> tuple[tuple[float, float] | None, bool]:
    starts = [
        float(block.locator.start_seconds)
        for block in chapter_blocks
        if getattr(block.locator, "start_seconds", None) is not None
    ]
    if not starts:
        return None, False
    ends = [
        float(block.locator.end_seconds)
        for block in chapter_blocks
        if getattr(block.locator, "end_seconds", None) is not None
    ]
    span = (min(starts), max(ends or starts))
    if not duration:
        return span, False
    return span, span[1] < float(duration) * 0.9


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
    # Only the page's own player binds a URL to the requested post; passively
    # captured responses are diagnostics and may belong to other clips. The
    # page player also publishes undownloadable player internals (`blob:` MSE
    # sources), which must not compete with the signed URL beside them.
    bound_urls = _http_media_candidates(bound_media_urls)
    # A cover teaser is a legal HTTPS player source, so no scheme or host rule
    # rejects it, and autoplay makes it look loaded. Dropping it means a player
    # that never mounted reports the login wall instead of publishing a 2.6 s
    # silent preview as the post.
    eligible_urls = [url for url in bound_urls if not _is_cover_preview_media_url(url)]
    preview_only = bool(bound_urls) and not eligible_urls
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
    frame_enabled = (
        request.scope.include_visual
        and request.host_capabilities.video_input == "frames"
        and request.host_capabilities.image_input == ImageInputCapability.LOCAL_FILE
    )
    retention_mode = str(getattr(request.retention, "raw_media", "ephemeral") or "ephemeral")
    keep_media = retention_mode == "persistent" and int(
        getattr(request.retention, "derived_ttl_seconds", 0) or 0
    ) > 0
    runtime = media_runtime_status()
    asr_ready = bool(runtime.get("asr_installed") and runtime.get("asr_model_available"))
    media_budget = int(request.budget.max_download_bytes or 0)

    def _browser_video_gap(
        reason: str,
        details: dict,
        *,
        handoff: bool = False,
    ) -> ReadResponse:
        """Return a gap response that keeps the media out of the answer.

        A gap never carries a media URL: the whole point is that this read could
        not decide (or afford) which file is the post, so publishing a signed
        CDN link would assert the association it just refused to make.
        """

        gap_coverage = video_coverage.model_copy(update={
            "discovered": max(int(video_coverage.discovered or 0), 1),
            "extraction": "partial",
            "unresolved": list(dict.fromkeys([*unresolved, reason])),
            "details": {**video_coverage.details, **details},
        })
        modalities = dict(response.coverage.modalities)
        modalities["video"] = gap_coverage
        update: dict = {
            "result": ReadResult.NEEDS_HOST if handoff else ReadResult.PARTIAL,
            "coverage": response.coverage.model_copy(update={"modalities": modalities}),
        }
        if handoff:
            update["execution_state"] = ExecutionState.WAITING_FOR_HOST
            update["needs"] = [
                Need(
                    action_id=f"confirm-page-video-{response.material_id or request.request_id}",
                    type="request_input",
                    purpose="confirm which page video is the requested post",
                    reason=(
                        "The page exposes several different video files and none matches "
                        "the requested post id"
                    ),
                )
            ]
        return response.model_copy(update=update)

    if not eligible_urls:
        # The player stayed unbound: a login or verification wall is the usual
        # reason on feed pages, and it needs a cookie or a one-time sign-in
        # rather than another silent attempt.
        return _browser_video_gap(
            (
                "The page player bound no downloadable source; a login or verification "
                "wall can keep it from mounting, so retry after importing browser cookies "
                "or signing in through a visible browser"
            ),
            {
                "browser_media_candidates": len(media_urls),
                "rejected_media_candidates": 0,
                "page_player_bound": False,
                "browser_media_preview_only": preview_only,
                "needs_user_action": _login_user_action(source_url),
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
        )
    if not asr_ready and not frame_enabled:
        # Nothing in this response would consume the media, so do not spend the
        # probe requests or the download only to report that ASR cannot run.
        visual_requirement = None
        if request.scope.include_visual:
            visual_requirement = {
                "host_capabilities": {
                    "video_input": "frames",
                    "image_input": "local_file",
                },
                "retention": {"raw_media": "persistent"},
            }
            unresolved.append(
                "Visual frames were requested but the host did not declare "
                "video_input=frames and image_input=local_file; send "
                "host_capabilities {video_input: frames, image_input: local_file} "
                "to receive frames"
            )
        unresolved.append(
            "Local video ASR is unavailable because Whisper is not installed"
            if not runtime.get("asr_installed")
            else "Local video ASR is unavailable because its Whisper model is not prepared"
        )
        return _browser_video_gap(
            (
                "Browser media was not fetched because no local consumer "
                "(ASR or frame inspection) is available"
            ),
            {
                "browser_media_candidates": len(media_urls),
                "browser_media_candidate_files": 0,
                "page_player_bound": False,
                "browser_media_preview_only": preview_only,
                "needs_user_action": None,
                "media_fetched": False,
                "media_fetch_skipped": "local_asr_unavailable",
                "media_selection": "not_probed",
                "media_selection_reason": "no_consumer",
                "raw_media_retention": retention_mode,
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
                **(
                    {"visual_requirement": visual_requirement}
                    if visual_requirement
                    else {}
                ),
            },
        )
    if media_budget <= 0:
        return _browser_video_gap(
            (
                "Browser media was not fetched because budget.max_download_bytes is 0; "
                "raise budget.max_download_bytes to let Envoy download and transcribe "
                "the page video"
            ),
            {
                "browser_media_candidates": len(media_urls),
                "browser_media_candidate_files": 0,
                "page_player_bound": False,
                "browser_media_preview_only": preview_only,
                "needs_user_action": None,
                "media_fetched": False,
                "media_fetch_skipped": "download_budget_exhausted",
                "media_selection": "not_probed",
                "media_selection_reason": "download_budget_exhausted",
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
        )

    # Probing is a network cost, so it obeys the download budget and the read
    # deadline; a page cannot make this read fetch more than the download would.
    probe_deadline = time.monotonic() + max(1.0, float(_remaining_work_seconds(request)))
    identities, probe_spent = _probe_page_media_identities(
        eligible_urls,
        {},
        max_probe_bytes=min(
            media_budget,
            _PAGE_MEDIA_PROBE_MAX_CANDIDATES * _PAGE_MEDIA_PROBE_BYTES,
        ),
        deadline=probe_deadline,
    )
    ranked_urls = _order_media_urls(eligible_urls, identities)
    binding = _browser_media_binding(source_url, ranked_urls, identities)
    selected_url = binding["selected_url"]
    rejected_count = 0
    if selected_url is not None:
        selected_url, rejected_count = _select_browser_video_url([selected_url], {})
        if selected_url is None:
            rejected_count = max(rejected_count, 1)
    distinct_files = (
        len({_media_identity_key(identity) for identity in identities.values()})
        or len(ranked_urls)
    )
    if selected_url is None:
        if rejected_count:
            reason = (
                "Browser media URLs were private, malformed, or otherwise unsafe "
                "for local processing"
            )
            selection_reason = "unsafe_candidate"
        else:
            reason = (
                "Browser media could not be confirmed as the requested post: the page "
                f"exposes {distinct_files} different video files and nothing in this read "
                "identifies which one belongs to this post, so open the post video "
                "directly or confirm the player and retry"
            )
            selection_reason = "multiple_distinct_page_media"
        return _browser_video_gap(
            reason,
            {
                "browser_media_candidates": len(media_urls),
                "browser_media_candidate_files": distinct_files,
                "rejected_media_candidates": rejected_count,
                "page_player_bound": False,
                "browser_media_preview_only": preview_only,
                "needs_user_action": None,
                "media_fetched": False,
                "media_fetch_skipped": "media_selection_unconfirmed",
                "media_selection": "unconfirmed",
                "media_selection_reason": selection_reason,
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
            handoff=frame_enabled,
        )

    selection_details = {
        "media_selection": binding["reason"],
        "media_selection_reason": None,
        "browser_media_candidate_files": distinct_files,
        "media_probe_bytes": probe_spent,
    }
    if binding.get("work_id"):
        selection_details["media_selected_work_id"] = binding["work_id"]
    download_budget = max(0, media_budget - probe_spent)

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
    if download_budget <= 0:
        return _browser_video_gap(
            (
                "Browser media was not fetched because the media prefix probes used the "
                "whole budget.max_download_bytes allowance"
            ),
            {
                **selection_details,
                "browser_media_candidates": len(media_urls),
                "page_player_bound": True,
                "browser_media_preview_only": preview_only,
                "needs_user_action": None,
                "media_fetched": False,
                "media_fetch_skipped": "download_budget_exhausted",
                "transcript_source": "unavailable",
                "visual_inspection": "not_performed",
            },
        )
    if keep_media:
        media_dir.mkdir(mode=0o700, exist_ok=True)

    duration = float(duration_seconds or 0)
    downloaded = None
    media_reused = False
    transcript_source = "unavailable"
    transcript_segments: list[dict] = []
    language = None
    asr_model = None
    asr_status = None
    asr_processed_until = None
    asr_elapsed_ms = None
    downloaded_bytes = None
    frame_response = response
    try:
        with tempfile.TemporaryDirectory(prefix="envoy-browser-video-") as temporary_dir:
            media_prefix = _browser_media_prefix(
                selected_url, response.material_id, response.source_revision
            )
            cached_media = None
            if keep_media:
                cached_media = next(
                    (
                        item for item in sorted(media_dir.glob(f"{media_prefix}*"))
                        if item.is_file() and not item.is_symlink() and item.stat().st_size > 0
                    ),
                    None,
                )
            if cached_media is not None:
                downloaded = {
                    "path": str(cached_media),
                    "duration": None,
                    "downloaded_bytes": cached_media.stat().st_size,
                }
                media_reused = True
            else:
                downloaded = download_video_for_frames(
                    selected_url,
                    temporary_dir,
                    max_download_bytes=download_budget,
                    max_work_seconds=_remaining_work_seconds(request),
                )
                media_reused = False
            video_path = Path(downloaded["path"]).resolve()
            if keep_media and not media_reused and video_path.is_file():
                target = media_dir / f"{media_prefix}{video_path.suffix or '.mp4'}"
                try:
                    os.replace(video_path, target)
                    video_path = target
                    downloaded = {**downloaded, "path": str(target)}
                    _prune_browser_media(media_dir, keep_prefix=media_prefix)
                except OSError:
                    pass  # fall back to the temporary copy for this read
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
            chapter_span, chapters_incomplete = _chapter_span_state(chapter_blocks, duration)
            if chapters_incomplete:
                unresolved.append(
                    "Page chapters stop at "
                    f"{chapter_span[1]:.0f}s of a {float(duration):.0f}s video, "
                    "so the chapter list is incomplete; reload the page for a fuller list"
                )
            page_text = "\n".join(
                block.content.text
                for block in blocks
                if isinstance(block.content, TextContent)
            )
            asr_extracted = {
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
            }
            # Charge the page blocks already in this answer against the same
            # budget the transcript is taken under, then derive the delivered
            # ASR blocks from the text that budget actually produced.
            transcript_lines = _video_transcript_lines(request, asr_extracted)
            transcript_title_block = _video_title_block(
                source_url, transcript_lines["title"]
            )
            delivery = _video_transcript_budget(
                request,
                transcript_lines,
                transcript_title_block,
                consumed_chars=len(page_text),
                consumed_tokens=_estimated_tokens(page_text),
            )
            delivered_end = int(delivery["returned_end"])
            considered_spans = transcript_lines["spans"][:200]
            delivered_spans = [
                span for span in considered_spans
                if int(span["end_offset"]) <= delivered_end
            ]
            for index, span in enumerate(delivered_spans, start=1):
                cue_text = re.sub(
                    r"^\[[0-9:.]+\]\s*",
                    "",
                    transcript_lines["full_text"][
                        int(span["start_offset"]):int(span["end_offset"])
                    ],
                )
                blocks.append(MaterialBlock(
                    id=f"blk-video-asr-{index:04d}",
                    kind=MaterialKind.TRANSCRIPT,
                    content=TextContent(
                        type="text",
                        text=cue_text,
                        language=transcript_lines["language"] or None,
                    ),
                    origin=MaterialOrigin.ASR,
                    locator=VideoLocator(
                        type="video_time",
                        source_url=_redact_url(source_url),
                        start_seconds=float(span["start_seconds"]),
                        end_seconds=float(span["end_seconds"]),
                    ),
                    parent_id=response.blocks[0].id if response.blocks else None,
                    order=len(blocks),
                    quality=Quality(engine=f"whisper-{asr_model or 'local'}"),
                ))
            returned_segments = len(delivered_spans)
            segment_inventory = len(transcript_lines["spans"])
            delivered_until = (
                float(delivered_spans[-1]["end_seconds"])
                if delivered_spans
                else (
                    float(asr_processed_until)
                    if asr_processed_until is not None
                    else None
                )
            )
            continuation_start = (
                float(delivered_spans[-1]["end_seconds"]) if delivered_spans else None
            )
            retention_available = request.retention.derived_ttl_seconds > 0
            continuation_available = (
                delivery["response_partial"]
                and not transcript_lines["source_truncated"]
                and retention_available
            )
            if delivered_end < len(transcript_lines["full_text"]):
                limiting = (
                    "response token budget"
                    if delivery["response_token_limit"] is not None
                    else "response character budget"
                )
                unresolved.append(
                    f"ASR transcript was truncated by the {limiting}; "
                    f"{segment_inventory - returned_segments} later segment(s) remain "
                    "undelivered, so continue with scope.start_seconds"
                )
            elif segment_inventory > returned_segments:
                unresolved.append(
                    f"The transcript has {segment_inventory} segments but only the first "
                    f"{returned_segments} are returned; continue after "
                    f"{delivered_until:.1f}s with scope.start_seconds"
                )
            coverage_end = (
                float(request.scope.end_seconds)
                if request.scope.end_seconds is not None
                else (float(duration) if duration else None)
            )
            unprocessed_seconds = None
            if (
                asr_status == "partial"
                and coverage_end is not None
                and delivered_until is not None
                and coverage_end > delivered_until
            ):
                unprocessed_seconds = coverage_end - delivered_until
                unresolved.append(
                    f"Local ASR stopped at {_format_video_time(delivered_until)}; "
                    f"{unprocessed_seconds:.0f}s of the {coverage_end:.0f}s window "
                    "remains untranscribed, continue with "
                    f"scope.start_seconds={delivered_until:.1f}"
                )

            persisted_transcript = None
            if transcript_segments:
                persisted_transcript = _video_subtitle_response(
                    request,
                    source_url,
                    asr_extracted,
                    consumed_chars=len(page_text),
                    consumed_tokens=_estimated_tokens(page_text),
                )

            processed_from = float(request.scope.start_seconds or 0.0)
            confidence, speech_seconds, speech_ratio = _transcript_confidence(
                transcript_segments,
                duration,
                processed_from=processed_from,
                processed_until=asr_processed_until,
            )
            if confidence == "low" and transcript_source == "asr":
                scored_span = max(
                    float(asr_processed_until or duration or 0) - processed_from, 0.0
                )
                unresolved.append(
                    "The audio is mostly music or noise: "
                    f"{len(transcript_segments)} short speech segment(s) cover "
                    f"{speech_ratio:.1%} of the {scored_span:.0f}s transcribed so far, "
                    "so the transcript may be unreliable"
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
                    **selection_details,
                    "browser_media_url": _media_origin(selected_url),
                    "media_downloaded_bytes": downloaded_bytes,
                    "duration_seconds": duration or None,
                    "transcript_source": transcript_source,
                    "transcript_language": language,
                    "asr_model": asr_model,
                    "asr_duration_ms": asr_elapsed_ms,
                    "page_chapter_count": len(chapter_blocks),
                    "page_chapter_span": list(chapter_span) if chapter_span else None,
                    "chapters_incomplete": chapters_incomplete,
                    "transcript_segment_count": len(transcript_segments),
                    "transcript_segments_returned": returned_segments,
                    "transcript_truncated": segment_inventory > returned_segments,
                    "transcript_processed_until_seconds": (
                        float(asr_processed_until)
                        if asr_processed_until is not None
                        else None
                    ),
                    "transcript_delivered_until_seconds": delivered_until,
                    "transcript_unprocessed_seconds": (
                        round(unprocessed_seconds, 3)
                        if unprocessed_seconds is not None
                        else None
                    ),
                    "continuation_available": continuation_available,
                    "transcript_continuation_start_seconds": continuation_start,
                    "requested_start_seconds": request.scope.start_seconds,
                    "requested_end_seconds": request.scope.end_seconds,
                    **_token_budget_details(
                        request,
                        int(delivery["returned_tokens"]),
                        transcript_lines["full_text"][delivered_end:],
                    ),
                    "speech_seconds": round(speech_seconds, 3),
                    "speech_coverage": round(speech_ratio, 4),
                    "transcript_confidence": confidence,
                    "frame_inspection": "requested" if frame_enabled else "host_capability_missing",
                    "media_fetched": True,
                    "raw_media_retention": retention_mode,
                    "raw_media_reused": media_reused,
                    "raw_media_path": str(video_path) if keep_media else None,
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
                "browser_media_url": _media_origin(selected_url),
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


BROWSER_MEDIA_PREFIX = "source-"


def _browser_media_prefix(source_url: str, material_id, source_revision) -> str:
    """A stable file prefix so the same video is fetched once per revision."""

    seed = "|".join([
        _redact_url(str(source_url or "")),
        str(material_id or ""),
        str(source_revision or ""),
    ])
    return f"{BROWSER_MEDIA_PREFIX}{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}-"


def _prune_browser_media(media_dir: Path, keep_prefix: str) -> None:
    for item in media_dir.glob(f"{BROWSER_MEDIA_PREFIX}*"):
        if item.name.startswith(keep_prefix):
            continue
        try:
            item.unlink()
        except OSError:
            pass


def _material_root() -> Path:
    root = storage.resolve_material_root()
    now = time.time()
    expiry_paths = sorted(
        root.glob("mat-*.expires"),
        key=lambda item: item.lstat().st_mtime,
        reverse=True,
    )
    valid_material_names = set()
    for expiry_path in expiry_paths:
        material_id = expiry_path.name.removesuffix(".expires")
        if not re.fullmatch(r"mat-[a-f0-9]{24}", material_id):
            continue
        try:
            with interprocess_file_lock(
                root / f".material-{hashlib.sha256(material_id.encode()).hexdigest()[:2]}.lock",
                max_wait_seconds=5,
                busy_message="Material is being updated",
            ):
                if expiry_path.is_symlink():
                    expiry_path.unlink(missing_ok=True)
                    continue
                expires_at = float(expiry_path.read_text(encoding="ascii").strip())
                material_path = expiry_path.with_suffix(".json")
                ranked_materials = sorted(
                    (
                        (path.lstat().st_mtime, path.name)
                        for path in root.glob("mat-*.expires")
                        if not path.is_symlink()
                    ),
                    reverse=True,
                )
                rank = next(
                    (
                        position for position, (_, name) in enumerate(ranked_materials)
                        if name == expiry_path.name
                    ),
                    MAX_CACHED_MATERIALS,
                )
                should_remove = (
                    now >= expires_at
                    or rank >= MAX_CACHED_MATERIALS
                    or material_path.is_symlink()
                    or not material_path.is_file()
                )
                if should_remove:
                    expiry_path.unlink(missing_ok=True)
                    material_path.unlink(missing_ok=True)
                    continue
                valid_material_names.add(material_path.name)
        except (OSError, TimeoutError):
            continue
        except ValueError:
            try:
                with interprocess_file_lock(
                    root / f".material-{hashlib.sha256(material_id.encode()).hexdigest()[:2]}.lock",
                    max_wait_seconds=5,
                    busy_message="Material is being updated",
                ):
                    expiry_path.unlink(missing_ok=True)
            except (OSError, TimeoutError):
                continue
    for material_path in root.glob("mat-*.json"):
        if material_path.name not in valid_material_names:
            material_id = material_path.stem
            if not re.fullmatch(r"mat-[a-f0-9]{24}", material_id):
                continue
            try:
                with interprocess_file_lock(
                    root / f".material-{hashlib.sha256(material_id.encode()).hexdigest()[:2]}.lock",
                    max_wait_seconds=5,
                    busy_message="Material is being updated",
                ):
                    expiry_path = material_path.with_suffix(".expires")
                    if expiry_path.is_file() and not expiry_path.is_symlink():
                        continue
                    material_path.unlink(missing_ok=True)
            except (OSError, TimeoutError):
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


def material_is_retained(
    material_id: str,
    source_revision: str,
    session_id: str | None,
) -> bool:
    """Whether the material a finished response names can still be expanded.

    The MCP bridge keeps a finished read in memory for the life of the process;
    the material behind it lives on disk under a lease. Advertising or serving a
    material that is already gone turns a recoverable read into a dead end, so the
    bridge asks this before it reuses one.
    """
    if not material_id or not source_revision:
        return False
    return (
        _load_matching_material(material_id, source_revision, session_id) is not None
    )


def persist_operation_response(
    material_id: str | None,
    response: dict,
    *,
    metadata: dict | None = None,
) -> None:
    """Persist the final response before the idempotency ledger is completed."""
    if not material_id:
        return
    root = _material_root()
    path = root / f"{material_id}.json"
    if path.is_symlink() or not path.is_file():
        return
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(stored, dict):
        return
    stored["operation_response"] = response
    if metadata:
        stored["operation_metadata"] = metadata
    _store_material(material_id, stored, merge=False)


def recover_operation_response(
    request: ReadRequest,
    source_url: str,
    *,
    original_request_id: str = "",
) -> dict | None:
    """Find a response persisted before a process exited during ledger commit."""
    root = _material_root()
    safe_source_url = _redact_url(source_url)
    for path in sorted(root.glob("mat-*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        material_id = path.stem
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stored, dict):
            continue
        stored_sources = {str(stored.get("source_url") or "")}
        operation_metadata = stored.get("operation_metadata")
        if isinstance(operation_metadata, dict) and operation_metadata.get("source_url"):
            stored_sources.add(_redact_url(str(operation_metadata["source_url"])))
        if safe_source_url not in stored_sources:
            continue
        if stored.get("session_id") != request.session_id:
            continue
        operation_response = stored.get("operation_response")
        if not isinstance(operation_response, dict):
            continue
        if request.session_id is None and (
            not original_request_id
            or str(operation_response.get("request_id") or "") != original_request_id
        ):
            continue
        source_revision = str(stored.get("source_revision") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", source_revision):
            continue
        if _load_matching_material(material_id, source_revision, request.session_id) is None:
            continue
        return operation_response
    return None


def _source_coverage_unresolved(stored: dict) -> list[str]:
    values = stored.get("source_coverage_unresolved")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item) for item in values if str(item).strip()))


# A delivery gap is not a source gap: once `expand` has handed over the rest of
# the text the material is complete. Real gaps (scans, parse errors, pages the
# work budget never reached) stay unresolved no matter how often we expand.
_PDF_DELIVERY_GAP_MARKERS = (
    "exceeds the initial response budget",
    "limited by the response budget",
)


def _pdf_source_unresolved(stored: dict) -> list[str]:
    gaps = _source_coverage_unresolved(stored)
    if stored.get("source_kind") != "pdf":
        return gaps
    return [
        gap
        for gap in gaps
        if not any(marker in gap for marker in _PDF_DELIVERY_GAP_MARKERS)
    ]


def _pdf_page_coverage_details(stored: dict) -> dict:
    if stored.get("source_kind") != "pdf":
        return {}
    spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
    coverage = stored.get("page_coverage") if isinstance(stored.get("page_coverage"), dict) else {}
    indexed = sorted({
        int(span["page_number"])
        for span in spans
        if isinstance(span, dict) and str(span.get("page_number", "")).isdigit()
    })
    page_count = int(stored.get("page_count") or coverage.get("page_count") or len(spans))
    return {
        "page_count": page_count,
        "indexed_page_count": len(indexed),
        "indexed_pages": indexed,
        "unindexed_pages": sorted(set(range(1, page_count + 1)) - set(indexed)),
        "page_coverage": coverage,
        "delivery_gap": bool(stored.get("delivery_gap")),
    }


def _remaining_work_seconds(request: ReadRequest) -> int:
    deadline = _READ_WORK_DEADLINE.get()
    if deadline is None:
        return max(int(request.budget.max_work_seconds), 1)
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise MediaBudgetExceeded("The request max_work_seconds budget is exhausted")
    return remaining


def _read_operation_result_is_cacheable(payload: dict) -> bool:
    try:
        response = ReadResponse.model_validate(payload)
    except (TypeError, ValueError):
        return True
    retryable_codes = {
        ErrorCode.NETWORK_ERROR,
        ErrorCode.SOURCE_UNAVAILABLE,
        ErrorCode.AUTH_REQUIRED,
        ErrorCode.CHALLENGE_REQUIRED,
        ErrorCode.MISSING_CAPABILITY,
        ErrorCode.BUDGET_ENFORCEMENT_UNAVAILABLE,
        ErrorCode.BUDGET_EXCEEDED,
    }
    if response.result == ReadResult.BLOCKED:
        return not any(
            error.retryable
            or error.action_required
            or error.code in retryable_codes
            for error in response.errors
        )
    if response.result == ReadResult.PARTIAL:
        # A partial read that stopped on authentication would replay the same
        # truncated result after the user signs in; keep it retryable instead.
        return not any(error.code in retryable_codes for error in response.errors)
    return True


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


def _annotate_unserialized_read(response: ReadResponse) -> ReadResponse:
    """Record that the read ran without its cross-process lock.

    The lock is an optimization against concurrent duplicate reads; the state
    directory being unwritable is a host fact worth surfacing, not a reason to
    withhold the evidence that was already gathered.
    """

    report = storage.fallback_report()
    note = (
        "Envoy could not write its state directory in this host, so this read ran "
        "without process serialization and its material may not persist"
    )
    detail: dict = {"state_lock": "skipped"}
    if report:
        detail["state_root_fallback"] = report
    modalities = dict(response.coverage.modalities)
    for name, item in modalities.items():
        if not isinstance(item, CoverageItem):
            continue
        modalities[name] = item.model_copy(update={
            "unresolved": list(dict.fromkeys([*item.unresolved, note])),
            "details": {**item.details, **detail},
        })
    errors = [
        error.model_copy(update={"details": {**(error.details or {}), **detail}})
        for error in response.errors
    ]
    return response.model_copy(update={
        "coverage": response.coverage.model_copy(update={"modalities": modalities}),
        "errors": errors,
    })


def _annotate_reacquired_read(response: ReadResponse) -> ReadResponse:
    """Record that a stale completed result was replaced by a fresh read.

    Returning the re-read evidence with a note is better than an EXPIRED_RESOURCE
    error: the caller asked for the source, not for the ledger's copy of it.
    """
    note = (
        "The completed result recorded for this operation had expired, so the "
        "source was read again instead of returning stale evidence"
    )
    detail = {"reacquired_after_expiry": True}
    modalities = dict(response.coverage.modalities)
    for name, item in modalities.items():
        if not isinstance(item, CoverageItem):
            continue
        modalities[name] = item.model_copy(update={
            "unresolved": list(dict.fromkeys([*item.unresolved, note])),
            "details": {**item.details, **detail},
        })
    return response.model_copy(update={
        "coverage": response.coverage.model_copy(update={"modalities": modalities}),
    })


def _pretty_json_text(text: str) -> str:
    """Indent a JSON body so a caller reading line-oriented text can find an entry.

    A minified endpoint answer arrives as one enormous line: nothing in it can be
    located, quoted or excerpted, and any host that reads text line by line sees a
    single unusable block. The parsed form carries the same content. Anything that
    is not JSON, or whose formatted form would change truncation behaviour, is
    returned exactly as received.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return text
    if not isinstance(parsed, (dict, list)):
        return text
    try:
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return text
    if len(pretty) > MAX_TEXT_CHARS:
        return text
    return pretty


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

        def run_locked():
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

            def execute_payload():
                response = execute()
                payload = response.model_dump(mode="json")
                persist_operation_response(
                    response.material_id,
                    payload,
                    metadata={
                        "source_url": _redact_url(request.source.url),
                        "session_id": request.session_id,
                        "request_id": request.request_id,
                    },
                )
                return payload

            try:
                payload = run_idempotent(
                    request.idempotency_key,
                    fingerprint=fingerprint,
                    execute=execute_payload,
                    should_cache=_read_operation_result_is_cacheable,
                    recover=lambda record: recover_operation_response(
                        request,
                        request.source.url,
                        original_request_id=str(
                            (record.get("recovery_metadata") or {}).get("request_id") or ""
                        ),
                    ),
                    recovery_metadata={
                        "source_url": _redact_url(request.source.url),
                        "session_id": request.session_id,
                        "request_id": request.request_id,
                    },
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
                        # The ledger still holds a completed result whose evidence
                        # is gone, and the caller never sets idempotency_key itself,
                        # so "use a new key" would be advice it cannot follow. The
                        # read is idempotent by source, not by bookkeeping: read it.
                        return _annotate_reacquired_read(execute())
                for resource in response.resources:
                    target = resource.delivery.path
                    if target and not Path(target).is_file():
                        raise OperationConflict(
                            "The delivered resources for this completed read expired; "
                            "retry envoy_read with refresh=true to reacquire them"
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

        lock_held = False
        try:
            with optional_interprocess_lock(
                root / f".read-{lock_id[:2]}.lock",
                max_wait_seconds=request.budget.max_work_seconds,
                busy_message="This source is already being read in the current session",
            ) as locked:
                lock_held = locked
                response = run_locked()
        except TimeoutError:
            return _blocked(
                request,
                ErrorCode.BUDGET_EXCEEDED,
                "Timed out waiting for another read of this source in the same session",
            )

        if not lock_held:
            return _annotate_unserialized_read(response)
        return response

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


def _stable_evidence_digest(*parts: object) -> str:
    """Content/locator-derived suffix so a block id survives a re-query.

    Position-based ids (``blk-search-0001``) change whenever a different query
    touches the same material, which makes a citation unverifiable later. The
    digest covers the material, the offset and the text itself.
    """

    identity = "\0".join(str(part) for part in parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _evidence_text(content: object) -> str:
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return ""


def _normalize_quote_text(value: str) -> str:
    """Whitespace-insensitive comparison form; punctuation and wording stay exact."""

    return re.sub(r"\s+", "", value.replace("\u200b", "").replace("\ufeff", ""))


def _coverage_pages(stored: dict) -> set[int]:
    pages: set[int] = set()
    for key in ("page_spans", "pdf_page_spans", "text_pages"):
        spans = stored.get(key)
        if not isinstance(spans, list):
            continue
        for span in spans:
            if isinstance(span, dict):
                page = span.get("page_number", span.get("page"))
                if isinstance(page, int) and page >= 1:
                    pages.add(page)
            elif isinstance(span, int):
                pages.add(span)
    return pages


_EVIDENCE_RANGE_SUFFIX = re.compile(r"^(?P<base>[a-z0-9][a-z0-9-]*?)-range-(?P<offset>\d+)$")
_EVIDENCE_SEARCH_ID = re.compile(r"^blk-search-(?P<start>\d+)-(?P<end>\d+)-[a-f0-9]+$")
_EVIDENCE_WHOLE_TEXT_IDS = (
    "blk-body-0001",
    "blk-video-0001",
    "blk-video-transcript",
    "blk-office-0001",
)


def _evidence_locator_dict(locator) -> dict | None:
    """Keep locators in the stored shape a check reads, not as pydantic objects."""

    if locator is None:
        return None
    if isinstance(locator, dict):
        return locator
    dump = getattr(locator, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return None


def _evidence_text_slice(stored: dict, start: int, end: int) -> str:
    text = str(stored.get("text") or "")
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    return text[start:end]


def _evidence_text_locator(stored: dict, block_id: str, content: str, start: int, end: int):
    """Return the best locator this material can honestly attach to a range.

    A citation check must answer "is this range locatable" from what was stored,
    not from the shape of the id the host typed. PDF page spans, video cue spans
    and OCR regions are the only things that pin a range to a place in the
    source; everything else falls back to a web locator that names the block.
    """

    source_url = _redact_url(str(stored.get("source_url") or ""))
    source_kind = stored.get("source_kind")
    if source_kind == "pdf":
        spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
        span = next(
            (
                item for item in spans
                if isinstance(item, dict)
                and int(item.get("start") or 0) <= start < int(item.get("end") or 0)
            ),
            None,
        )
        return PdfLocator(
            type="pdf_page",
            page_number=int(span["page_number"]) if span else None,
            locator_valid=span is not None,
        )
    if source_kind == "video":
        locator = _video_locator_for_range(
            source_url,
            stored.get("video_spans") if isinstance(stored.get("video_spans"), list) else [],
            start,
            end,
        )
        if locator is not None:
            return locator
    if source_kind == "image":
        spans = stored.get("ocr_spans") if isinstance(stored.get("ocr_spans"), list) else []
        span = next(
            (
                item for item in spans
                if isinstance(item, dict)
                and int(item.get("end") or 0) > start
                and int(item.get("start") or 0) < end
            ),
            None,
        )
        if span is not None:
            return ImageRegionLocator(
                type="image_region",
                resource_id=str(span.get("resource_id") or ""),
                coordinate_system="normalized_top_left",
                bbox=[float(value) for value in span.get("bbox") or []],
            )
    return WebLocator(
        type="web",
        source_url=source_url,
        block_id=block_id,
        quote=content[:MAX_LOCATOR_QUOTE_CHARS],
    )


def _material_evidence_blocks(stored: dict) -> dict[str, dict]:
    """Build the block view a citation check resolves against.

    One index feeds every locator question, so a block id printed by read,
    search, expand or replay resolves against the same stored material record
    instead of each path inventing its own idea of what a block is. Explicitly
    stored blocks win; page, cue and OCR spans are rebuilt from the flat text;
    whole-document blocks are synthesised so later ``-range-`` ids have a base.
    """

    records: dict[str, dict] = {}

    def add(block_id: str, content: str, locator, order: int) -> None:
        if not block_id or block_id in records:
            return
        records[block_id] = {
            "text": content,
            "locator": _evidence_locator_dict(locator),
            "order": order,
        }

    raw = stored.get("blocks")
    if isinstance(raw, list):
        for position, block in enumerate(raw):
            if not isinstance(block, dict):
                continue
            try:
                order = int(block.get("order"))
            except (TypeError, ValueError):
                order = position
            add(
                str(block.get("id") or ""),
                _evidence_text(block.get("content")),
                block.get("locator"),
                order,
            )

    text = str(stored.get("text") or "")
    source_kind = stored.get("source_kind")
    title_text = str(stored.get("title_text") or "").strip()
    if title_text:
        # A title is not a place in the source, so it carries no locator at all;
        # inventing one would let a check claim a passage is pinned when it is not.
        add("blk-title", title_text, None, 0)

    page_spans = stored.get("page_spans")
    if isinstance(page_spans, list):
        for span in page_spans:
            if not isinstance(span, dict):
                continue
            try:
                page_number = int(span.get("page_number") or 0)
                start = int(span.get("start") or 0)
                end = int(span.get("end") or 0)
            except (TypeError, ValueError):
                continue
            if page_number < 1 or end <= start or start < 0 or end > len(text):
                continue
            add(
                f"blk-pdf-page-{page_number:04d}",
                text[start:end],
                PdfLocator(
                    type="pdf_page",
                    page_number=page_number,
                    locator_valid=True,
                ),
                page_number,
            )

    video_spans = stored.get("video_spans")
    if isinstance(video_spans, list):
        for index, span in enumerate(video_spans):
            if not isinstance(span, dict):
                continue
            try:
                start = int(span.get("start_offset") or 0)
                end = int(span.get("end_offset") or 0)
                start_seconds = float(span.get("start_seconds") or 0)
                end_seconds = float(span.get("end_seconds") or 0)
            except (TypeError, ValueError):
                continue
            if end <= start or start < 0 or end > len(text):
                continue
            add(
                f"blk-video-asr-{index + 1:04d}",
                text[start:end],
                VideoLocator(
                    type="video_time",
                    source_url=_redact_url(str(stored.get("source_url") or "")),
                    start_seconds=start_seconds,
                    end_seconds=max(end_seconds, start_seconds + 0.001),
                ),
                index + 1,
            )

    if text:
        if source_kind == "video":
            whole_ids = ("blk-video-transcript", "blk-video-0001")
        elif source_kind == "pdf":
            whole_ids = ()
        elif source_kind == "image":
            whole_ids = ("blk-image-0001", "blk-body-0001")
        else:
            whole_ids = ("blk-body-0001", "blk-office-0001")
        for block_id in whole_ids:
            add(
                block_id,
                text,
                _evidence_text_locator(stored, block_id, text, 0, len(text)),
                1,
            )
    return records


def _resolve_evidence_block(stored: dict, block_id: str | None, records: dict[str, dict]) -> dict | None:
    """Resolve any id a receipt can print back to text plus a locator.

    Read prints ``blk-body-0001``/``blk-video-transcript``, search prints
    ``blk-search-{start}-{end}-{digest}`` and expand prints
    ``{base}-range-{offset}``. All three encode a character range, so a check can
    rebuild the same block without the earlier response still being in hand.
    """

    if not block_id:
        return None
    record = records.get(block_id)
    if record is not None:
        return record
    text = str(stored.get("text") or "")
    search = _EVIDENCE_SEARCH_ID.match(block_id)
    if search:
        start = int(search.group("start"))
        end = int(search.group("end"))
        content = _evidence_text_slice(stored, start, end)
        if not content:
            return None
        return {
            "text": content,
            "locator": _evidence_locator_dict(
                _evidence_text_locator(stored, block_id, content, start, end)
            ),
            "order": start,
        }
    ranged = _EVIDENCE_RANGE_SUFFIX.match(block_id)
    if ranged:
        base = str(ranged.group("base"))
        offset = int(ranged.group("offset"))
        base_record = records.get(base)
        if base_record is None and base not in _EVIDENCE_WHOLE_TEXT_IDS:
            return None
        if offset < 0 or offset > len(text):
            return None
        end = len(text)
        budget_state = stored.get("budget_state")
        if isinstance(budget_state, dict):
            for delivered_start, delivered_end in _material_returned_ranges(budget_state, len(text)):
                if delivered_start <= offset < delivered_end:
                    end = delivered_end
                    break
        content = _evidence_text_slice(stored, offset, end)
        if not content:
            return None
        return {
            "text": content,
            "locator": _evidence_locator_dict(
                _evidence_text_locator(stored, base or block_id, content, offset, end)
            ),
            "order": offset,
        }
    return None


def _stored_evidence_blocks(stored: dict) -> list[dict]:
    """The block index in the list shape older callers expect."""

    return [
        {"id": block_id, **record}
        for block_id, record in _material_evidence_blocks(stored).items()
    ]


def _evidence_revision_kind(stored: dict, source_revision: str | None) -> str:
    """Say what the stored revision string traces to, not more.

    PDF reads hash the file bytes; several media paths fall back to hashing the
    URL. Both are 64 hex characters, so the length proves nothing — only the
    source kind and a comparison against the URL digest separate them.
    """

    if not source_revision:
        return "unknown"
    if stored.get("source_kind") == "pdf":
        return "byte_digest"
    source_url = stored.get("source_url")
    if isinstance(source_url, str) and source_url:
        if source_revision == hashlib.sha256(source_url.encode("utf-8")).hexdigest():
            return "url_digest"
    return "unknown"


def check_evidence(request: EvidenceCheckRequest) -> EvidenceCheckResponse:
    """Settle the deterministic half of a citation check on this machine.

    Envoy answers three questions and refuses the fourth: does the material still
    exist, does the cited block resolve, does the quote occur in it, and does the
    locator name a place that was actually extracted. Whether the cited text
    *supports* the claim stays with the host model; ``semantic_support`` is
    always ``not_evaluated``.
    """

    material_id = request.material_id.strip()
    block_id = request.block_id.strip() if isinstance(request.block_id, str) else None
    quote = request.quote if isinstance(request.quote, str) else None
    base = {
        "material_id": material_id,
        "block_id": block_id,
        "evidence_id": "ev-" + (hashlib.sha256(
            "\0".join((material_id, block_id or "", quote or "")).encode("utf-8")
        ).hexdigest()[:24]),
    }

    def reply(**kwargs) -> EvidenceCheckResponse:
        return EvidenceCheckResponse(**base, **kwargs)

    if not re.fullmatch(r"mat-[a-f0-9]{24}", material_id):
        return reply(
            resolution="unmatched",
            quote_match="none",
            locator_valid=False,
            reason="material_missing",
            notes=["material_id is not one Envoy issued; copy it from a read or search response"],
        )
    # Deliberately skip the expiry sweeper: it deletes an expired lease before a
    # check could report that it expired, which would turn "your lease ran out,
    # re-read the source" into an indistinguishable "no such material".
    root = storage.resolve_material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink() or not source_path.is_file():
        return reply(
            resolution="unmatched",
            quote_match="none",
            locator_valid=False,
            reason="material_missing",
            notes=[
                "no retained material under this id on this machine; read the source URL again with envoy_read to get a live material_id"
            ],
        )
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError, TypeError):
        return reply(
            resolution="unmatched",
            quote_match="none",
            locator_valid=False,
            reason="material_missing",
            notes=["retained material is unreadable"],
        )
    if not isinstance(stored, dict):
        return reply(
            resolution="unmatched", quote_match="none", locator_valid=False,
            reason="material_missing", notes=["retained material is not a material record"],
        )
    source_revision = str(stored.get("source_revision") or "") or None
    base["source_revision"] = source_revision
    base["source_revision_kind"] = _evidence_revision_kind(stored, source_revision)
    records = _material_evidence_blocks(stored)
    blocks = _stored_evidence_blocks(stored)
    if expires_at <= time.time():
        return reply(
            resolution="unmatched",
            quote_match="none",
            locator_valid=False,
            reason="material_expired",
            notes=["material lease expired; read the source URL again with envoy_read to cite it"],
        )

    target = _resolve_evidence_block(stored, block_id, records) if block_id else None
    if block_id and target is None:
        return reply(
            resolution="unmatched",
            quote_match="none",
            locator_valid=False,
            reason="block_missing",
            notes=["this material holds no block with that id; block ids are not portable across materials"],
        )
    search_text = target["text"] if target else "\n".join(
        record["text"] for record in records.values()
    )
    quote_match = "none"
    if quote:
        if quote in search_text:
            quote_match = "exact"
        elif _normalize_quote_text(quote) and _normalize_quote_text(quote) in _normalize_quote_text(search_text):
            quote_match = "normalized"
    found_quote = quote_match in ("exact", "normalized")
    locator = target.get("locator") if target else None
    notes: list[str] = []
    locator_valid = False
    if isinstance(locator, dict):
        locator_type = str(locator.get("type") or "")
        if locator_type == "pdf_page":
            page = locator.get("page_number")
            pages = _coverage_pages(stored)
            locator_valid = (
                locator.get("locator_valid", True) is not False
                and isinstance(page, int)
                and page >= 1
                and (not pages or page in pages)
            )
            if not locator_valid:
                notes.append(
                    "PDF text offset falls outside the extracted page spans; "
                    "re-read with page_start/page_end to obtain a page locator"
                )
        elif locator_type in ("web", "video_time", "audio_time", "image_region", "office_location"):
            locator_valid = True
            if (
                locator_type == "web"
                and locator.get("block_id")
                and _resolve_evidence_block(stored, str(locator["block_id"]), records) is None
            ):
                locator_valid = False
                notes.append("locator points at a block this material no longer holds")

    if target is not None and not quote:
        return reply(
            resolution="matched",
            quote_match="none",
            locator_valid=locator_valid,
            reason="block_found" if locator_valid else "quote_not_provided",
            notes=notes + (["quote not provided; pass the sentence you intend to cite to check it here"] if quote is None else []),
        )
    if found_quote:
        if locator_valid:
            reason = "quote_found"
        else:
            reason = "no_locator" if locator is None else "locator_unverifiable"
            if locator is None:
                notes.append(
                    "this block carries no locator; the words are in the material "
                    "but the passage cannot be pinned to a page, time or region"
                )
        return reply(
            resolution="matched",
            quote_match=quote_match,
            locator_valid=locator_valid,
            reason=reason,
            notes=notes,
        )
    if quote is None:
        return reply(
            resolution="matched" if blocks else "unmatched",
            quote_match="none",
            locator_valid=locator_valid,
            reason="quote_not_provided",
            notes=["material resolves; pass block_id and the quoted sentence for a locatable check"],
        )
    missing_notes = ["quoted text does not occur in the checked block"]
    if target is not None and len(target["text"]) < len(str(stored.get("text") or "")):
        # A block id narrows the check to one range, so a miss here is not a
        # statement about the whole source; say that instead of letting the host
        # read "absent" into a scoped answer.
        missing_notes.append(
            "the check covered only one block of this material, not its whole text; "
            "the sentence may occur elsewhere — verify again without block_id"
        )
    return reply(
        resolution="unmatched",
        quote_match="none",
        locator_valid=locator_valid,
        reason="quote_missing",
        notes=notes + missing_notes,
    )


def replay_material(request: MaterialReplayRequest) -> ReadResponse:
    """Replay retained evidence into a fresh host session without mutating its ledger."""
    root = _material_root()
    source_path = root / f"{request.material_id}.json"
    expiry_path = root / f"{request.material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        raise PublicPageError("Material is unavailable", ErrorCode.EXPIRED_RESOURCE)
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError, TypeError) as exc:
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={
                "next_action": "read the source URL again with envoy_read; a material can only be replayed while its lease is alive"
            },
        ) from exc
    if expires_at <= time.time():
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={
                "next_action": "read the source URL again with envoy_read; a material can only be replayed while its lease is alive"
            },
        )
    stored_revision = str(stored.get("source_revision") or "")
    if stored_revision != request.source_revision:
        raise PublicPageError(
            "Material source revision does not match",
            ErrorCode.INVALID_REQUEST,
            details={
                "expected_source_revision": stored_revision,
                "next_action": (
                    "pass the exact source_revision printed by the read that returned "
                    "this material_id; it is a full hex digest and a shortened prefix "
                    "is rejected"
                ),
            },
        )
    stored_session = str(stored.get("session_id") or "")
    # A model only holds its own host session id, never the id Envoy minted for
    # the old material, so an omitted source_session_id means "the material's
    # own session" rather than a mismatch.
    expected_source = request.source_session_id or stored_session
    if stored_session != expected_source:
        raise PublicPageError(
            "Material source session does not match",
            ErrorCode.INVALID_REQUEST,
            details={"next_action": "omit source_session_id so Envoy uses the retained material's session"},
        )
    if request.session_id == expected_source:
        raise PublicPageError(
            "Replay requires a new session_id distinct from source_session_id",
            ErrorCode.INVALID_REQUEST,
            details={
                "next_action": "pass the new host session's id as session_id; replay exists to move evidence into a different session"
            },
        )

    source_url = _redact_url(str(stored.get("source_url") or ""))
    replay_id = _material_id(
        source_url,
        request.source_revision,
        request.session_id,
        request.request_id,
    )
    replay_path = root / f"{replay_id}.json"
    replay_expiry = root / f"{replay_id}.expires"
    frame_dir = root / f"frames-{replay_id}"
    if (
        replay_path.is_symlink()
        or replay_expiry.is_symlink()
        or frame_dir.is_symlink()
        or (frame_dir.exists() and not frame_dir.is_dir())
    ):
        raise PublicPageError("Replay material path is not a regular Envoy path", ErrorCode.INVALID_REQUEST)
    if _load_matching_material(replay_id, request.source_revision, request.session_id):
        raise PublicPageError(
            "Material was already replayed to this session; continue with envoy_search or envoy_expand",
            ErrorCode.INVALID_REQUEST,
            details={
                "material_id": replay_id,
                "source_revision": request.source_revision,
                "next_action": "continue the existing replay material instead of resetting its budget",
            },
        )

    original_title = str(stored.get("title_text") or "Cached material")
    token_limits = [
        value for value in (
            request.max_response_tokens,
            request.max_total_response_tokens,
        )
        if value is not None
    ]
    title_token_budget = max(0, min(token_limits) - 64) if token_limits else None
    title, _title_end = _take_text_budgeted(
        original_title,
        0,
        min(256, request.max_chars // 4),
        title_token_budget,
    )
    title_block = (
        MaterialBlock(
            id="blk-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=title),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id="blk-title",
                quote=title[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=0,
        )
        if title
        else None
    )

    expiry = min(expires_at, float(stored.get("expires_at") or expires_at))
    replayed = {
        **stored,
        "session_id": request.session_id,
        "expires_at": expiry,
        "budget_state": {
            "max_total_response_chars": request.max_total_response_chars,
            "max_total_response_tokens": request.max_total_response_tokens,
            "max_expansion_rounds": request.max_expansion_rounds,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    }
    old_visual_resources = stored.get("visual_resources")
    old_visual_resources = old_visual_resources if isinstance(old_visual_resources, list) else []
    new_visual_resources = []
    staged_frames: list[tuple[Path, Path]] = []
    staging_dir = Path(tempfile.mkdtemp(prefix=".replay-frames-", dir=root))
    for item in old_visual_resources:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        frame_path = str(item.get("frame_path") or "")
        if frame_path:
            source_frame = Path(frame_path)
            if source_frame.is_symlink() or not source_frame.is_file():
                copied.pop("frame_path", None)
                copied["unavailable"] = True
            else:
                resolved_frame = source_frame.resolve()
                try:
                    relative = resolved_frame.relative_to(root.resolve())
                except ValueError:
                    copied.pop("frame_path", None)
                    copied["unavailable"] = True
                else:
                    if not relative.parts or not relative.parts[0].startswith("frames-mat-"):
                        copied.pop("frame_path", None)
                        copied["unavailable"] = True
                    else:
                        target = frame_dir / resolved_frame.name
                        staged = staging_dir / resolved_frame.name
                        try:
                            if not staged.exists():
                                shutil.copyfile(resolved_frame, staged)
                                staged.chmod(0o600)
                        except OSError:
                            copied.pop("frame_path", None)
                            copied["unavailable"] = True
                        else:
                            copied["frame_path"] = str(target)
                            staged_frames.append((staged, target))
        new_visual_resources.append(copied)
    if old_visual_resources:
        replayed["visual_resources"] = new_visual_resources
    try:
        with _material_transaction(replay_id):
            if replay_path.exists() or replay_expiry.exists():
                raise PublicPageError(
                    "Material was already replayed to this session; continue with envoy_search or envoy_expand",
                    ErrorCode.INVALID_REQUEST,
                    details={
                        "material_id": replay_id,
                        "source_revision": request.source_revision,
                        "next_action": "continue the existing replay material instead of resetting its budget",
                    },
                )
            _store_material_unlocked(replay_id, replayed, merge=False)
            if staged_frames:
                frame_dir.mkdir(mode=0o700, exist_ok=True)
                for staged, target in staged_frames:
                    if not target.exists():
                        os.replace(staged, target)
                    else:
                        staged.unlink(missing_ok=True)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    title_tokens = _estimated_tokens(title)
    body_token_limit = (
        max(0, request.max_response_tokens - title_tokens)
        if request.max_response_tokens is not None
        else None
    )
    total_token_limit = (
        max(64, request.max_total_response_tokens - title_tokens)
        if request.max_total_response_tokens is not None
        else None
    )
    body_char_limit = max(128, request.max_chars - len(title))
    text_response = expand_material(ExpandRequest.model_validate({
        "request_id": request.request_id,
        "material_id": replay_id,
        "session_id": request.session_id,
        "offset": 0,
        "max_chars": body_char_limit,
        "max_response_tokens": body_token_limit,
        "max_total_response_tokens": total_token_limit,
        "max_total_response_chars": request.max_total_response_chars,
        "max_expansion_rounds": request.max_expansion_rounds,
    }))
    body_blocks = [
        block.model_copy(update={"order": block.order + (1 if title_block else 0)})
        for block in text_response.blocks
        if block.id != "blk-title"
    ]
    blocks = ([title_block] if title_block else []) + body_blocks

    resources: list[ResourceRef] = []
    visual_blocks = []
    unavailable_visual_count = sum(
        1 for item in new_visual_resources
        if isinstance(item, dict) and item.get("unavailable")
    )
    visual_candidates = [
        item for item in new_visual_resources
        if isinstance(item, dict)
        and not item.get("unavailable")
        and str(item.get("resource_id") or "") not in {
            str(observation.get("resource_id") or "")
            for observation in (stored.get("visual_observations") or [])
            if isinstance(observation, dict)
        }
    ]
    stored_visual_observation_count = sum(
        1
        for item in (stored.get("visual_observations") or [])
        if isinstance(item, dict)
    )
    host_image_capability = request.host_capabilities.image_input
    if request.include_visual:
        for index, item in enumerate(visual_candidates[:request.max_visual_inputs], start=1):
            resource_id = str(item.get("resource_id") or "")
            source_block_id = str(item.get("source_block_id") or f"blk-image-{index:04d}")
            image_url = _redact_url(str(item.get("source_url") or source_url))
            frame_path = str(item.get("frame_path") or "")
            if frame_path:
                if host_image_capability != ImageInputCapability.LOCAL_FILE:
                    continue
                resources.append(ResourceRef(
                    resource_id=resource_id,
                    mime_type="image/jpeg",
                    delivery=HostDelivery(type="local_file", path=frame_path),
                    delivery_evidence="generated_only",
                    expires_at=datetime.fromtimestamp(expiry, tz=timezone.utc),
                ))
                timestamp = float(item.get("timestamp") or 0)
                visual_blocks.append(MaterialBlock(
                    id=source_block_id,
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
                        source_url=image_url,
                        start_seconds=timestamp,
                        end_seconds=timestamp + 0.001,
                        frame_id=Path(frame_path).name,
                    ),
                    order=len(blocks) + len(visual_blocks),
                    quality=Quality(
                        engine="envoy_material_replay",
                        unresolved=["Sparse frame sample; not full-video visual coverage"],
                    ),
                ))
            else:
                image_uri = str(item.get("source_url") or "")
                if not image_uri or host_image_capability != ImageInputCapability.URI:
                    continue
                resources.append(ResourceRef(
                    resource_id=resource_id,
                    mime_type=mimetypes.guess_type(urlparse(image_uri).path)[0] or "image/jpeg",
                    delivery=HostDelivery(type="uri", uri=image_uri),
                    delivery_evidence="unverified",
                    expires_at=datetime.fromtimestamp(expiry, tz=timezone.utc),
                ))
                visual_blocks.append(MaterialBlock(
                    id=source_block_id,
                    kind=MaterialKind.IMAGE,
                    content=ImageContent(
                        type="image",
                        resource_id=resource_id,
                        alt_text=str(item.get("alt") or ""),
                        mime_type=mimetypes.guess_type(urlparse(image_uri).path)[0] or "image/jpeg",
                    ),
                    origin=MaterialOrigin.SOURCE_IMAGE,
                    locator=WebLocator(
                        type="web",
                        source_url=image_uri,
                        block_id=source_block_id,
                    ),
                    order=len(blocks) + len(visual_blocks),
                ))
    blocks.extend(visual_blocks)
    unresolved = [
        *text_response.coverage.unresolved,
        *(
            ["Stored visual resources were omitted because include_visual was false"]
            if visual_candidates and not request.include_visual
            else []
        ),
        *(
            ["Host image input capability was not declared or does not match the replayed resource delivery type"]
            if request.include_visual and len(visual_candidates) > len(resources)
            else []
        ),
        *(
            ["Some stored visual resources were unavailable under their original lease"]
            if unavailable_visual_count
            else []
        ),
        *(
            ["Some stored visual resources were unavailable or exceeded the new visual budget"]
            if len(visual_candidates) > len(resources)
            else []
        ),
    ]
    needs = []
    needs_host = bool(request.include_visual and resources)
    if needs_host:
        resource_ids = [resource.resource_id for resource in resources]
        block_ids = [block.id for block in visual_blocks]
        needs = [Need(
            action_id=f"inspect-replay-{hashlib.sha256(replay_id.encode()).hexdigest()[:12]}",
            type="inspect_visual",
            purpose="Inspect replayed source visual evidence relevant to the user's goal",
            resource_ids=resource_ids,
            source_block_ids=block_ids,
            reason="Host inspection is required for the replayed visual resources.",
            max_resources=len(resources),
        )]
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=replay_id,
        source_revision=request.source_revision,
        execution_state=(
            ExecutionState.WAITING_FOR_HOST if needs_host else ExecutionState.COMPLETED
        ),
        result=(
            ReadResult.NEEDS_HOST if needs_host
            else ReadResult.PARTIAL if unresolved or text_response.result == ReadResult.PARTIAL
            else ReadResult.READY
        ),
        blocks=blocks,
        resources=resources,
        needs=needs,
        coverage=Coverage(
            modalities={
                "text": (
                    text_response.coverage.modalities.get("text")
                    or CoverageItem(
                        discovered=1,
                        extracted=1,
                        returned=0,
                        extraction="unknown",
                    )
                ),
                "replay": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=len(blocks),
                    delivered=len(resources),
                    extraction="partial" if unresolved or text_response.result == ReadResult.PARTIAL else "complete",
                    unresolved=list(dict.fromkeys(unresolved)),
                    details={
                        "new_session": request.session_id,
                        "returned_chars": sum(
                            len(block.content.text)
                            for block in body_blocks
                            if block.content.type == "text"
                        ),
                        "available_chars": len(str(stored.get("text") or "")),
                        "source_ledger_unchanged": True,
                        "source_material_id": request.material_id,
                        "replayed_material_id": replay_id,
                        "stored_visual_observation_count": stored_visual_observation_count,
                    },
                )
            },
            unresolved=list(dict.fromkeys(unresolved)),
        ),
        expires_at=datetime.fromtimestamp(expiry, tz=timezone.utc),
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
            "The completed material expired or was evicted; retry envoy_read with "
            "refresh=true to read the source again",
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


def _store_material(material_id: str, data: dict, *, merge: bool = True) -> None:
    with _material_transaction(material_id):
        _store_material_unlocked(material_id, data, merge=merge)


def _store_material_unlocked(material_id: str, data: dict, *, merge: bool = True) -> None:
    root = _material_root()
    target = root / f"{material_id}.json"
    expiry_target = root / f"{material_id}.expires"
    data = {
        **data,
        "expires_at": float(data.get("expires_at") or time.time() + MATERIAL_TTL_SECONDS),
    }
    if merge and target.is_file() and not target.is_symlink():
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
    code: ErrorCode = ErrorCode.BUDGET_EXCEEDED,
    reason: str | None = None,
) -> ReadResponse:
    source_unresolved = _source_coverage_unresolved(stored or {})
    material = stored or {}
    text_length = len(str(material.get("text") or ""))
    state = material.get("budget_state")
    delivered_ranges = _material_returned_ranges(
        state if isinstance(state, dict) else {},
        text_length,
    )
    details = {
        "text_length": text_length,
        "delivered_ranges": delivered_ranges,
    }
    # A refusal that is not about budget still has to say where the unread text
    # resumes, or the caller has nowhere to go next.
    undelivered = _unseen_ranges(0, text_length, delivered_ranges)
    if undelivered:
        details["next_offset"] = undelivered[0][0]
    if reason:
        details["reason"] = reason
    return ReadResponse(
        request_id=request.request_id,
        material_id=request.material_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[
            ReadError(
                code=code,
                message=message,
                details=details,
            )
        ],
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


def _valid_range_pairs(values, text_length: int) -> list[list[int]]:
    ranges = []
    for item in values or []:
        if not isinstance(item, list) or len(item) != 2:
            continue
        try:
            start, end = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        if 0 <= start < end <= text_length:
            ranges.append([start, end])
    return ranges


def _material_returned_ranges(state: dict, text_length: int) -> list[list[int]]:
    """Ranges a read or expand actually delivered.

    Search hits are tracked separately: a search only ships a short window
    around each hit, so treating it as full delivery would block a later
    expand of the same region.
    """
    return _merge_ranges(_valid_range_pairs(state.get("returned_ranges"), text_length))


def _material_delivered_ranges(state: dict, text_length: int) -> list[list[int]]:
    """Everything already sent for this material, read text and search hits alike."""
    return _merge_ranges(
        _material_returned_ranges(state, text_length)
        + _valid_range_pairs(state.get("search_returned_ranges"), text_length)
    )


def _record_material_range(stored: dict, state: dict, start: int, end: int, content: str) -> None:
    state["returned_ranges"] = _merge_ranges(state["returned_ranges"] + [[start, end]])
    state["returned_chars"] += end - start
    state["returned_tokens"] += _estimated_tokens(content)
    state["expansion_rounds"] += 1
    stored["budget_state"] = state


def _record_material_ranges(
    stored: dict,
    state: dict,
    ranges: list[tuple[int, int]],
    content_chars: int,
    content_tokens: int,
) -> None:
    """Record every range one answer delivered as a single expansion round."""

    for start, end in ranges:
        state["returned_ranges"] = _merge_ranges(state["returned_ranges"] + [[start, end]])
    state["returned_chars"] += content_chars
    state["returned_tokens"] += content_tokens
    state["expansion_rounds"] += 1
    stored["budget_state"] = state


@_serialize_material_id_first
def record_visual_observation(
    material_id: str | None,
    source_url: str | None,
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
    if expected is None:
        return {"persisted": False, "reason": "resource_mismatch"}
    expected_source_url = str(expected.get("source_url") or "")
    if source_url:
        safe_source_url = _redact_url(source_url)
    else:
        safe_source_url = _redact_url(expected_source_url)
    if (
        _visual_source_identity(str(expected.get("source_url") or ""))
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
    return {
        "persisted": True,
        "observation_count": len(stored["visual_observations"]),
        "source_url": _redact_url(expected_source_url),
    }


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
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        ) from exc
    if expires_at <= time.time():
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        )
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
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        ) from exc
    if expires_at <= time.time():
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        )
    if stored.get("session_id") and request.session_id != stored.get("session_id"):
        raise PublicPageError("Material belongs to a different session", ErrorCode.INVALID_REQUEST)

    total_text = str(stored.get("text") or "")
    source_unresolved = _source_coverage_unresolved(stored)
    state = stored.get("budget_state") if isinstance(stored.get("budget_state"), dict) else {}
    returned_ranges = _material_delivered_ranges(state, len(total_text))
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
                candidate = None
                for segment_start, segment_end in segments:
                    segment_text = total_text[segment_start:segment_end]
                    segment_score = _goal_score(segment_text, terms)
                    if not segment_score:
                        continue
                    candidate_start = start if preserve_line_context else segment_start
                    candidate_end = end if preserve_line_context else segment_end
                    candidate_text = total_text[candidate_start:candidate_end]
                    candidate = {
                        "start": candidate_start,
                        "end": candidate_end,
                        "text": candidate_text,
                        "score": segment_score,
                        "hit_offset": (
                            segment_start - candidate_start
                            + _search_hit_offset(segment_text, terms)
                        ),
                        "unseen_segments": segments,
                    }
                    break
                if candidate is None:
                    already_returned_matches += 1
                else:
                    candidates.append(candidate)
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
        ocr_span = None
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
                page_number=int(span["page_number"]) if span else None,
                locator_valid=span is not None,
            )
        elif stored.get("source_kind") == "image":
            ocr_span = next(
                (
                    item for item in stored.get("ocr_spans", [])
                    if isinstance(item, dict)
                    and int(item.get("end") or 0) > start
                    and int(item.get("start") or 0) < end
                ),
                None,
            )
            locator = (
                ImageRegionLocator(
                    type="image_region",
                    resource_id=str(ocr_span["resource_id"]),
                    coordinate_system="normalized_top_left",
                    bbox=[float(value) for value in ocr_span["bbox"]],
                )
                if ocr_span
                else WebLocator(
                    type="web",
                    source_url=str(stored.get("source_url") or ""),
                    block_id="blk-image-0001",
                    quote=content[:MAX_LOCATOR_QUOTE_CHARS],
                )
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
            id=f"blk-search-{start}-{end}-{_stable_evidence_digest(material_id, start, content)}",
            kind=(
                MaterialKind.TRANSCRIPT
                if stored.get("source_kind") == "video"
                else MaterialKind.OCR
                if ocr_span
                else MaterialKind.PARAGRAPH
            ),
            content=TextContent(type="text", text=content),
            origin=(
                MaterialOrigin.ASR
                if stored.get("source_kind") == "video" and stored.get("transcript_source") == "asr"
                else MaterialOrigin.SUBTITLE
                if stored.get("source_kind") == "video"
                else MaterialOrigin.OCR
                if ocr_span
                else MaterialOrigin.SOURCE_TEXT
            ),
            locator=locator,
            order=start,
            quality={
                "engine": (
                    str(ocr_span.get("engine") or "ocr")
                    if ocr_span
                    else "goal_keyword_search"
                ),
                "engine_score": (
                    float(ocr_span.get("confidence") or 0)
                    if ocr_span
                    else min(1.0, candidate["score"] / max(len(terms), 1))
                ),
                "unresolved": (
                    [
                        "OCR is a derived text hint; inspect the source image for layout or ambiguity",
                        "Lexical relevance is a retrieval hint, not semantic verification",
                        *(
                            ["The OCR engine assigned zero confidence to this region"]
                            if ocr_span and float(ocr_span.get("confidence") or 0) <= 0
                            else []
                        ),
                    ]
                    if ocr_span
                    else ["Lexical relevance is a retrieval hint, not semantic verification"]
                ),
            },
        ))
        used_chars += len(content)
        used_tokens += _estimated_tokens(content)
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
    newly_covered_ranges: list[list[int]] = []
    for block in blocks:
        uncovered = _unseen_ranges(
            block.order,
            block.order + len(block.content.text),
            search_ranges,
        )
        newly_covered_ranges.extend(uncovered)
        search_ranges.extend([[start, end] for start, end in uncovered])
    state["search_returned_ranges"] = _merge_ranges(search_ranges)
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
                        "newly_covered_chars": sum(
                            end - start for start, end in newly_covered_ranges
                        ),
                        "newly_covered_tokens": sum(
                            _estimated_tokens(total_text[start:end])
                            for start, end in newly_covered_ranges
                        ),
                        "match_offsets": [block.order for block in blocks],
                        **_pdf_page_coverage_details(stored),
                        "token_estimate_basis": "ascii4_nonascii1_v1",
                        "token_estimate_scope": "all_returned_material_text_including_repeated_context",
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
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        )
    if expires_at <= time.time():
        source_path.unlink(missing_ok=True)
        expiry_path.unlink(missing_ok=True)
        raise PublicPageError(
            "Material is missing or expired",
            ErrorCode.EXPIRED_RESOURCE,
            details={"next_action": _EXPIRED_MATERIAL_NEXT_ACTION},
        )
    if stored.get("session_id") and request.session_id != stored.get("session_id"):
        raise PublicPageError("Material belongs to a different session", ErrorCode.INVALID_REQUEST)
    source_unresolved = _pdf_source_unresolved(stored)
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
        remaining = _unseen_ranges(0, len(total_text), all_returned_ranges)
        if not remaining:
            # Nothing is left unread: repeating "call envoy_expand" here sends the
            # caller in a circle.
            return _expand_blocked(
                request,
                "The material has already been delivered in full; this range adds "
                "no new text",
                stored,
                code=ErrorCode.RANGE_ALREADY_DELIVERED,
                reason="material_fully_delivered",
            )
        return _expand_blocked(
            request,
            "The requested range overlaps an earlier response; the next undelivered "
            f"range starts at character {remaining[0][0]}",
            stored,
            code=ErrorCode.RANGE_ALREADY_DELIVERED,
            reason="range_already_delivered",
        )
    if stored.get("source_kind") == "pdf":
        spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
        page_blocks: list[MaterialBlock] = []
        page_numbers: list[int] = []
        recorded: list[tuple[int, int]] = []
        cursor = offset
        tokens_left = response_token_limit
        delivered_chars = 0
        delivered_tokens = 0
        # Pack every page that still fits the response budget into this one
        # answer: advancing a page per call turns a long report into dozens of
        # rounds, and the caller never needs a page at a time when a window fits.
        for item in spans:
            if not isinstance(item, dict):
                continue
            span_start = int(item.get("start") or 0)
            span_end = int(item.get("end") or 0)
            if span_end <= cursor:
                continue
            page_offset = max(cursor, span_start)
            room = min(end, span_end) - page_offset
            if room <= 0:
                break
            content, page_end = _take_text_budgeted(total_text, page_offset, room, tokens_left)
            if not content:
                break
            page_number = int(item.get("page_number") or 0)
            page_numbers.append(page_number)
            recorded.append((page_offset, page_end))
            delivered_chars += page_end - page_offset
            page_tokens = _estimated_tokens(content)
            delivered_tokens += page_tokens
            if tokens_left is not None:
                tokens_left = max(0, tokens_left - page_tokens)
            page_blocks.append(MaterialBlock(
                id=f"blk-pdf-page-{page_number:04d}-range-{page_offset}",
                kind=MaterialKind.PARAGRAPH,
                content=TextContent(type="text", text=content),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=PdfLocator(type="pdf_page", page_number=page_number),
                order=page_offset,
            ))
            cursor = page_end
        if not page_blocks:
            raise PublicPageError(
                "The cumulative response character budget has been reached",
                ErrorCode.BUDGET_EXCEEDED,
            )
        _record_material_ranges(stored, budget_state, recorded, delivered_chars, delivered_tokens)
        has_more = any(
            isinstance(item, dict) and int(item.get("end") or 0) > cursor
            for item in spans
        )
        # The delivery gap tracks only what this material has not yet handed
        # over, so it must close as soon as the last page range is delivered.
        stored["delivery_gap"] = bool(has_more)
        try:
            _store_material(material_id, stored)
        except OSError:
            return _expand_blocked(request, "The material budget ledger could not be persisted", stored)
        return ReadResponse(
            request_id=request.request_id,
            session_id=stored.get("session_id"),
            material_id=material_id,
            source_revision=str(stored["source_revision"]),
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL if has_more or source_unresolved else ReadResult.READY,
            blocks=page_blocks,
            coverage=Coverage(modalities={
                "text": CoverageItem(
                    discovered=len(spans),
                    extracted=len(spans),
                    returned=len(page_blocks),
                    extraction="partial" if has_more else "complete",
                    unresolved=(
                        ["More PDF text is available"] if has_more else []
                    ),
                    details={
                        "offset": recorded[0][0],
                        "returned_chars": delivered_chars,
                        "total_chars": len(total_text),
                        "next_offset": cursor if has_more else None,
                        "page_number": page_numbers[-1],
                        "page_numbers": page_numbers,
                        "pages_returned": len(page_numbers),
                        **_pdf_page_coverage_details(stored),
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


def _is_connect_failure(error: Exception) -> bool:
    """True when the failure happened before any byte of the response.

    A refused or timed-out connection is not a transient read hiccup: sleeping
    250ms and repeating it three times only multiplies the wall clock (each
    attempt pays a fresh connect timeout). The one retry worth doing is the
    other exit in `proxy_plan()`, which the caller already performs.
    """

    return isinstance(
        error,
        (httpx.ConnectTimeout, httpx.ConnectError, httpx.ProxyError, httpx.UnsupportedProtocol),
    )


def _credential_cookie_header(url: str) -> str:
    """The Cookie header for one URL, or "" when handoff is off.

    Cookie values exist only in this local process for the length of the
    request. They are never logged, returned in a read result, or written to
    coverage: only the existence of a session can change the outcome.
    """

    from . import features

    if not features.credential_handoff_enabled():
        return ""
    try:
        from .cookies import load_cookies_for

        cookies = load_cookies_for(url)
    except Exception:  # noqa: BLE001 - a broken store must not break a read
        return ""
    pairs = [
        f"{item['name']}={item['value']}"
        for item in cookies
        if item.get("name") and item.get("value") is not None
    ]
    return "; ".join(pairs)


def _fetch_html(
    source_url: str,
    max_bytes: int = MAX_HTML_BYTES,
    dns_cache: dict[str, str] | None = None,
) -> tuple[str, str]:
    dns_cache = dns_cache if dns_cache is not None else {}
    last_error: Exception | None = None
    for proxy in proxy_plan():
        # The direct attempt already proved the exit is slow or dead, so the
        # fallback proxy gets a shorter connect timeout. A dead link used to
        # cost 20s + 20s of connects before failing (measured 46s on
        # news.ycombinator.com while the network path was down); 10s + 6s keeps
        # a live server on a slow link reachable and a dead one cheap.
        connect_timeout = 10 if proxy is None else 6
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect_timeout, read=45),
                follow_redirects=False,
                trust_env=False,
                proxy=proxy,
            ) as client:
                return _fetch_html_via(client, source_url, max_bytes, dns_cache)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
    raise PublicPageError(
        "The source could not be reached directly or through the configured proxy",
        ErrorCode.NETWORK_ERROR,
    ) from last_error


def _fetch_html_via(
    client: httpx.Client,
    source_url: str,
    max_bytes: int,
    dns_cache: dict[str, str],
) -> tuple[str, str]:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}

    for _ in range(5):
        if not _is_public_http_url(current_url, dns_cache):
            raise PublicPageError("The source URL or redirect is not a public HTTP(S) address")
        cookie_header = _credential_cookie_header(current_url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        else:
            headers.pop("Cookie", None)
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
                            details={
                                "http_status": response.status_code,
                                "final_url": _redact_url(current_url),
                                "diagnosis": "access_required",
                                "user_check_recommended": True,
                                "proxy_configured": _http_proxy_configured(),
                            },
                        )
                    if response.status_code == 404:
                        raise PublicPageError(
                            "The source returned HTTP 404. This may be a removed or private item, an invalid share link, or an upstream proxy response; Envoy cannot distinguish these from the response alone.",
                            ErrorCode.SOURCE_UNAVAILABLE,
                            details={
                                "http_status": 404,
                                "final_url": _redact_url(current_url),
                                "diagnosis": "source_or_route_not_found",
                                "user_check_recommended": True,
                                "proxy_configured": _http_proxy_configured(),
                                "suggested_next_action": "Ask the user to open the same link in their regular browser. If it opens there, ask whether to use an already configured or user-approved trusted proxy.",
                            },
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
                    hint_path = urlparse(current_url).path.lower()
                    if hint_path.endswith(IWORK_SUFFIXES):
                        raise PublicPageError(
                            "This is an Apple iWork file (Pages/Numbers/Keynote). "
                            "Export it as .docx/.xlsx/.pptx or PDF and read that link instead",
                            ErrorCode.UNSUPPORTED_SOURCE,
                        )
                    if hint_path.endswith(BINARY_SHEET_SUFFIXES):
                        raise PublicPageError(
                            "This is a binary Excel workbook (.xlsb). "
                            "Save it as .xlsx and read that link instead",
                            ErrorCode.UNSUPPORTED_SOURCE,
                        )
                    document_path = urlparse(current_url).path.lower()
                    is_office_content = content_type in (
                        OFFICE_CONTENT_TYPES - {"application/zip"}
                    )
                    if not is_office_content and document_path.endswith(OFFICE_SUFFIXES):
                        is_office_content = content_type in {
                            "",
                            "application/zip",
                            "application/octet-stream",
                            "binary/octet-stream",
                        }
                    if is_office_content:
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > MAX_TEXT_SOURCE_BYTES:
                                    raise PublicPageError(
                                        "The document exceeds Envoy's size limit"
                                    )
                            except ValueError:
                                pass
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_TEXT_SOURCE_BYTES:
                                raise PublicPageError("The document exceeds Envoy's size limit")
                        document_data = bytes(body)
                        if not document_data.startswith(b"PK\x03\x04"):
                            raise PublicPageError(
                                "The server declared a ZIP document but returned other data",
                                ErrorCode.UNSUPPORTED_SOURCE,
                            )
                        raise DetectedOffice({
                            "url": _redact_url(current_url),
                            "data": document_data,
                            "mime_type": content_type or "application/zip",
                        })
                    text_path = urlparse(current_url).path.lower()
                    is_text_content = (
                        content_type in TEXT_CONTENT_TYPES
                        or content_type in RTF_CONTENT_TYPES
                        or (
                            content_type in {"", "application/octet-stream"}
                            and (
                                text_path.endswith(TEXT_SUFFIXES)
                                or text_path.endswith(RTF_SUFFIXES)
                            )
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
                    if content_type in {"", "application/octet-stream", "binary/octet-stream"}:
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > _SNIFF_MAX_BYTES:
                                raise PublicPageError(
                                    "The source is served as a binary download that is too large to read as text",
                                    ErrorCode.UNSUPPORTED_SOURCE,
                                )
                        sniffed = _sniff_plain_text(bytes(body))
                        if sniffed is not None:
                            sniffed_data, sniffed_encoding = sniffed
                            raise DetectedText({
                                "url": _redact_url(current_url),
                                "data": sniffed_data,
                                "mime_type": "text/plain",
                                "encoding": sniffed_encoding,
                            })
                        raise PublicPageError(
                            f"Static reader only accepts HTML pages, received {content_type}",
                            ErrorCode.UNSUPPORTED_SOURCE,
                        )
                    if content_type in LEGACY_OFFICE_CONTENT_TYPES or text_path.endswith(
                        LEGACY_OFFICE_SUFFIXES
                    ):
                        raise PublicPageError(
                            "This is a legacy binary Office file (.doc/.xls/.ppt). "
                            "Save or export it as .docx/.xlsx and read that link instead",
                            ErrorCode.UNSUPPORTED_SOURCE,
                        )
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
            except httpx.TransportError as exc:
                if _is_connect_failure(exc) or attempt + 1 >= MAX_FETCH_ATTEMPTS:
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
    dns_cache: dict[str, str],
) -> dict:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False, trust_env=False, proxy=explicit_proxy()) as client:
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


def _pdf_deadline(request: ReadRequest) -> float | None:
    """One deadline shared by the PDF download and every page extraction."""

    remaining = _remaining_work_seconds(request)
    if remaining is None:
        return None
    return time.monotonic() + max(0.0, float(remaining))


def _pdf_download_budget(request: ReadRequest) -> int:
    """Bytes a PDF download may use, keeping an explicit zero explicit.

    ``max_download_bytes == 0`` is a caller saying "do not download". The old
    expression ``budget or MAX_PDF_BYTES`` could not tell that apart from an
    unset field and quietly granted the full 64 MiB.
    """

    configured = int(request.budget.max_download_bytes)
    if configured <= 0:
        return 0
    return min(MAX_PDF_BYTES, configured)


def _pdf_source_bytes(source: dict) -> bytes:
    if source.get("data") is not None:
        return bytes(source["data"])
    path = source.get("path")
    if not path:
        raise PublicPageError("The PDF source produced no bytes", ErrorCode.INVALID_REQUEST)
    return Path(str(path)).read_bytes()


def _pdf_page_store(digest: str):
    """Page checkpoint store for one source digest, or None when unavailable."""

    try:
        from .pdf_store import PdfPageStore

        return PdfPageStore(_material_root(), digest)
    except OSError:
        return None


def _pdf_page_window(total: int, start: int | None, end: int | None) -> tuple[int, int]:
    """Return the inclusive 1-based page window a single read extracts.

    A page number past the end is clamped rather than rejected, so a caller that
    guesses too far still gets the tail of the document. A window wider than
    ``MAX_PDF_PAGES`` is cut: one call never extracts an unbounded document, and
    the rest is reported as not processed so the host can ask for it by range.
    """

    last_page = max(int(total), 1)
    first = max(int(start or 1), 1)
    first = min(first, last_page)
    stop = last_page if end is None else max(min(int(end), last_page), first)
    if stop - first + 1 > MAX_PDF_PAGES:
        stop = first + MAX_PDF_PAGES - 1
    return first, stop


def _fetch_pdf_source(
    source_url: str,
    max_bytes: int,
    dns_cache: dict[str, str],
    *,
    deadline: float | None = None,
) -> dict:
    """Download a PDF to a job temp file inside one byte and time budget.

    The byte counter is shared by every redirect and retry, so a chain of five
    redirects cannot deliver five times the cap. Bytes land on disk as they
    arrive instead of in a growing in-memory buffer.
    """

    if max_bytes <= 0:
        raise PublicPageError(
            "max_download_bytes is 0, so the PDF was not downloaded",
            ErrorCode.BUDGET_EXCEEDED,
        )

    def check_deadline() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise PublicPageError(
                "PDF download exhausted max_work_seconds",
                ErrorCode.BUDGET_EXCEEDED,
            )

    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    remaining_bytes = int(max_bytes)
    temp_path: Path | None = None
    handle = None
    size = 0
    digest = hashlib.sha256()
    resumed_bytes = 0

    def discard_partial() -> None:
        """Drop bytes from an earlier attempt instead of appending to garbage."""

        nonlocal temp_path, handle, size, digest
        if handle is not None and not handle.closed:
            handle.close()
        handle = None
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        temp_path = None
        size = 0
        digest = hashlib.sha256()

    def finish_partial() -> None:
        """Flush what arrived so the next attempt can continue from it."""

        nonlocal handle
        if handle is not None and not handle.closed:
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                pass
            handle.close()
        handle = None

    try:
        with httpx.Client(timeout=httpx.Timeout(20, read=60), follow_redirects=False, trust_env=False, proxy=explicit_proxy()) as client:
            for _ in range(5):
                check_deadline()
                if remaining_bytes <= 0:
                    raise PublicPageError("The PDF exceeds Envoy's size limit")
                if not _is_public_http_url(current_url, dns_cache):
                    raise PublicPageError("The PDF URL or redirect is not a public HTTP(S) address")
                redirected = False
                attempt = 0
                while attempt < MAX_FETCH_ATTEMPTS:
                    check_deadline()
                    # A read timeout used to throw away a 20 MB partial download
                    # and ask for the whole file again; a third attempt on a
                    # flaky link could never finish. Ask for the missing tail
                    # instead and let the server say whether it agrees.
                    resumable = temp_path is not None and size > 0
                    request_headers = dict(headers)
                    if resumable:
                        request_headers["Range"] = f"bytes={size}-"
                    try:
                        with client.stream("GET", current_url, headers=request_headers) as response:
                            if response.status_code in {301, 302, 303, 307, 308}:
                                location = response.headers.get("location")
                                if not location:
                                    raise PublicPageError("The PDF server returned an invalid redirect")
                                # A redirect body still spends the same budget.
                                for chunk in response.iter_bytes():
                                    remaining_bytes -= len(chunk)
                                    check_deadline()
                                    if remaining_bytes < 0:
                                        raise PublicPageError("The PDF exceeds Envoy's size limit")
                                discard_partial()
                                current_url = urljoin(current_url, location)
                                redirected = True
                                break
                            if response.status_code in {401, 403}:
                                raise PublicPageError("The PDF source requires authentication", ErrorCode.AUTH_REQUIRED)
                            if response.status_code == 416 and resumable:
                                # The range no longer matches the resource: start over.
                                discard_partial()
                                attempt += 1
                                continue
                            if response.status_code in {408, 425, 429} or response.status_code >= 500:
                                attempt += 1
                                if attempt < MAX_FETCH_ATTEMPTS:
                                    if deadline is not None and deadline - time.monotonic() <= 0.25 * attempt:
                                        raise PublicPageError(
                                            "PDF download exhausted max_work_seconds",
                                            ErrorCode.BUDGET_EXCEEDED,
                                        )
                                    time.sleep(0.25 * attempt)
                                    continue
                            response.raise_for_status()
                            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                            resuming = resumable and response.status_code == 206
                            if resuming:
                                content_range = response.headers.get("content-range", "")
                                if not content_range.replace(" ", "").startswith(f"bytes{size}-"):
                                    discard_partial()
                                    attempt += 1
                                    continue
                            else:
                                discard_partial()
                            content_length = response.headers.get("content-length")
                            if content_length:
                                try:
                                    if size + int(content_length) > max_bytes:
                                        raise PublicPageError("The PDF exceeds Envoy's size limit")
                                except ValueError:
                                    pass
                            if resuming:
                                resumed_bytes += size
                                handle = open(temp_path, "ab")
                            else:
                                handle = tempfile.NamedTemporaryFile(
                                    "wb",
                                    prefix="envoy-pdf-",
                                    suffix=".pdf",
                                    delete=False,
                                )
                                temp_path = Path(handle.name)
                            for chunk in response.iter_bytes():
                                check_deadline()
                                size += len(chunk)
                                if size > max_bytes:
                                    raise PublicPageError("The PDF exceeds Envoy's size limit")
                                digest.update(chunk)
                                handle.write(chunk)
                            finish_partial()
                            with temp_path.open("rb") as check:
                                head = check.read(5)
                            if not head.startswith(b"%PDF-"):
                                raise PublicPageError(
                                    f"Expected a PDF source, received {content_type or 'unknown content'}",
                                    ErrorCode.UNSUPPORTED_SOURCE,
                                )
                            return {
                                "url": _redact_url(current_url),
                                "path": str(temp_path),
                                "mime_type": "application/pdf",
                                "size_bytes": size,
                                "sha256": digest.hexdigest(),
                                "input_kind": "remote_pdf",
                                "resumed_bytes": resumed_bytes,
                            }
                    except httpx.TransportError as exc:
                        attempt += 1
                        finish_partial()
                        if _is_connect_failure(exc) or attempt >= MAX_FETCH_ATTEMPTS:
                            discard_partial()
                            raise
                        if deadline is not None and deadline - time.monotonic() <= 0.25 * attempt:
                            discard_partial()
                            raise PublicPageError(
                                "PDF download exhausted max_work_seconds",
                                ErrorCode.BUDGET_EXCEEDED,
                            )
                        time.sleep(0.25 * attempt)
                if redirected:
                    continue
            raise PublicPageError("The PDF source exceeded the redirect limit")
    except BaseException:
        if handle is not None and not handle.closed:
            handle.close()
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def _parse_pdf_source(
    request: ReadRequest,
    source: dict,
    *,
    deadline: float | None = None,
) -> ReadResponse:
    data = _pdf_source_bytes(source)
    source_digest = str(source.get("sha256") or hashlib.sha256(data).hexdigest())
    try:
        pdf = PdfReader(BytesIO(data), strict=False)
    except Exception as exc:
        raise PublicPageError(f"Could not parse PDF: {type(exc).__name__}", ErrorCode.INVALID_REQUEST) from exc
    if getattr(pdf, "is_encrypted", False):
        raise PublicPageError("Encrypted PDFs are not supported by the standalone Skill", ErrorCode.AUTH_REQUIRED)
    total_page_count = len(pdf.pages)
    window_start, window_end = _pdf_page_window(
        total_page_count,
        getattr(request.scope, "page_start", None),
        getattr(request.scope, "page_end", None),
    )
    window_gaps = [
        gap
        for gap in (
            (1, window_start - 1) if window_start > 1 else None,
            (window_end + 1, total_page_count) if window_end < total_page_count else None,
        )
        if gap is not None
    ]

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

    page_store = _pdf_page_store(source_digest)
    stored_pages = page_store.committed() if page_store is not None else {}
    page_texts: list[str] = []
    page_spans: list[dict] = []
    page_statuses: dict[int, str] = {}
    scanned_pages: list[int] = []
    blank_pages: list[int] = []
    parse_error_pages: list[int] = []
    # Long documents report the skipped window as a range plus a bounded page
    # list: a 10k-page PDF must not put 10k integers into the coverage payload.
    page_list_limit = 200
    gap_pages: list[int] = []
    for gap_first, gap_last in window_gaps:
        room = page_list_limit - len(gap_pages)
        if room <= 0:
            break
        gap_pages.extend(range(gap_first, min(gap_last, gap_first + room - 1) + 1))
    unprocessed_pages: list[int] = list(gap_pages)
    work_budget_exhausted = False
    cursor = 0
    for page_number in range(window_start, window_end + 1):
        if deadline is not None and time.monotonic() >= deadline:
            work_budget_exhausted = True
            unprocessed_pages = (
                gap_pages + list(range(page_number, window_end + 1))
            )[:page_list_limit]
            break
        record = stored_pages.get(page_number)
        if record is not None and str(record.get("status")) in {"text", "ocr_candidate", "blank", "parse_error"}:
            status = str(record.get("status"))
            page_text = str(record.get("text") or "")
            extraction_mode = str(record.get("extraction_mode") or "text")
        else:
            page = pdf.pages[page_number - 1]
            raw = ""
            try:
                raw = str(page.extract_text() or "")
                cleaned = re.sub(r"\n{3,}", "\n\n", raw).strip()
            except Exception:
                status, page_text, extraction_mode = "parse_error", "", "error"
            else:
                page_text = cleaned
                if page_text:
                    status, extraction_mode = "text", "text"
                elif raw.strip():
                    # The page has content but its extracted text is whitespace
                    # only: it is genuinely blank to a text reader, not a scan.
                    status, extraction_mode = "blank", "text"
                else:
                    status, extraction_mode = "ocr_candidate", "none"
            if page_store is not None:
                page_store.commit_page(
                    page_number,
                    status=status,
                    text=page_text,
                    extraction_mode=extraction_mode,
                    page_count=total_page_count,
                )
        page_statuses[page_number] = status
        if status == "ocr_candidate":
            scanned_pages.append(page_number)
            continue
        if status == "blank":
            blank_pages.append(page_number)
            continue
        if status == "parse_error":
            parse_error_pages.append(page_number)
            continue
        if page_texts:
            cursor += 2
        start = cursor
        cursor += len(page_text)
        page_texts.append(page_text)
        page_spans.append({"page_number": page_number, "start": start, "end": cursor})
        if deadline is not None and time.monotonic() >= deadline:
            # This page is finished and committed; stop before starting the next.
            work_budget_exhausted = True
            unprocessed_pages = (
                gap_pages + list(range(page_number + 1, window_end + 1))
            )[:page_list_limit]
            break

    full_text = "\n\n".join(page_texts)
    material_id = _material_id(
        source["url"], source_digest, request.session_id, request.request_id
    )
    page_coverage = {
        "page_count": total_page_count,
        "text_pages": len(page_spans),
        "scan_candidate_pages": scanned_pages,
        "blank_pages": blank_pages,
        "parse_error_pages": parse_error_pages,
        "not_processed_pages": unprocessed_pages,
        "not_processed_count": sum(
            last - first + 1 for first, last in window_gaps
        ) + (len(unprocessed_pages) - len(gap_pages) if work_budget_exhausted else 0),
        "not_processed_ranges": [[first, last] for first, last in window_gaps],
        "page_window": {
            "page_start": window_start,
            "page_end": window_end,
            "limit": MAX_PDF_PAGES,
        },
    }
    real_unresolved: list[str] = []
    if window_gaps:
        real_unresolved.append(
            f"PDF has {total_page_count} pages; this read covered pages "
            f"{window_start}-{window_end} (limit {MAX_PDF_PAGES} per read). Pages not covered: "
            + ", ".join(
                str(first) if first == last else f"{first}-{last}"
                for first, last in window_gaps[:8]
            )
            + ". Send page_start/page_end to read another window"
        )
    if scanned_pages:
        real_unresolved.append(
            "PDF pages without an extractable text layer require OCR: "
            + ", ".join(str(page_number) for page_number in scanned_pages[:64])
        )
    if parse_error_pages:
        real_unresolved.append(
            "PDF pages could not be parsed: "
            + ", ".join(str(page_number) for page_number in parse_error_pages[:64])
        )
    if work_budget_exhausted:
        real_unresolved.append(
            "max_work_seconds was exhausted before every page was processed; "
            "pages "
            + ", ".join(str(page_number) for page_number in unprocessed_pages[:64])
            + " were not processed"
        )
    if not full_text:
        if work_budget_exhausted or unprocessed_pages:
            if page_store is not None:
                try:
                    page_store.mark_not_processed(
                        unprocessed_pages,
                        page_count=total_page_count,
                        material_id=material_id,
                    )
                except OSError:
                    pass
            return ReadResponse(
                request_id=request.request_id,
                session_id=request.session_id,
                material_id=material_id,
                source_revision=source_digest,
                execution_state=ExecutionState.COMPLETED,
                result=ReadResult.PARTIAL,
                blocks=[title_block],
                coverage=Coverage(
                    modalities={
                        "text": CoverageItem(
                            discovered=total_page_count,
                            extracted=0,
                            returned=0,
                            extraction="partial",
                            unresolved=real_unresolved,
                            details={
                                **page_coverage,
                                "returned_chars": 0,
                                "total_chars": 0,
                                "continuation_available": False,
                                "next_offset": None,
                                "input_kind": str(source.get("input_kind") or "remote_pdf"),
                            },
                        )
                    },
                    unresolved=list(dict.fromkeys(real_unresolved)),
                ),
            )
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

    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_budget_exhausted and not retention_disabled
    # Only the initial response reports the delivery gap; the material records
    # what is missing from the source itself, so `expand` can converge once the
    # delivered text reaches the end instead of repeating a fixed gap.
    source_unresolved = list(real_unresolved)
    unresolved = list(real_unresolved)
    if response_budget_exhausted:
        unresolved.append("PDF text was limited by the response budget")
    if scanned_pages:
        unresolved.append("Some pages contain no text layer and need OCR")
    should_store_material = bool(
        can_continue or (not retention_disabled and len(full_text) <= MAX_SEARCH_CACHE_CHARS)
    )
    continuation_storage_failed = False
    if should_store_material:
        try:
            _store_material(material_id, {
                "source_kind": "pdf",
                "source_url": source["url"],
                "source_revision": source_digest,
                "session_id": request.session_id,
                "text": full_text,
                "title_text": title,
                "page_spans": page_spans,
                "page_count": total_page_count,
                "page_coverage": page_coverage,
                "delivery_gap": response_budget_exhausted,
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

    if page_store is not None:
        try:
            page_store.mark_not_processed(
                unprocessed_pages,
                page_count=total_page_count,
                material_id=material_id if should_store_material and not continuation_storage_failed else None,
                reason=(
                    "work budget exhausted"
                    if work_budget_exhausted
                    else "pages outside the requested window"
                ),
            )
        except OSError:
            pass

    if retention_disabled and response_budget_exhausted:
        unresolved.append("Continuation is unavailable because local retention is disabled")
    if continuation_storage_failed:
        unresolved.append("Continuation cache could not be written")
    has_unread_pages = bool(
        scanned_pages or parse_error_pages or unprocessed_pages or window_gaps
    )
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_digest,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if response_budget_exhausted or has_unread_pages else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={
            "text": CoverageItem(
                discovered=total_page_count,
                extracted=len(page_spans),
                returned=len(blocks) - 1,
                extraction="partial" if response_budget_exhausted or has_unread_pages else "complete",
                unresolved=list(dict.fromkeys(unresolved)),
                details={
                    **page_coverage,
                    "returned_chars": returned_chars,
                    "total_chars": len(full_text),
                    "continuation_available": can_continue,
                    "next_offset": returned_end if can_continue else None,
                    "input_kind": str(source.get("input_kind") or "remote_pdf"),
                    **_token_budget_details(request, returned_tokens, full_text[returned_end:]),
                },
            )
        }, unresolved=list(dict.fromkeys(source_unresolved))),
    )


def _read_pdf_source(request: ReadRequest, source_url: str, dns_cache: dict[str, str]) -> ReadResponse:
    try:
        deadline = _pdf_deadline(request)
    except MediaBudgetExceeded as exc:
        return _blocked(request, ErrorCode.BUDGET_EXCEEDED, str(exc))
    max_bytes = _pdf_download_budget(request)
    if max_bytes <= 0:
        reused = _reuse_pdf_material(request, source_url)
        if reused is not None:
            return reused
        return _blocked(
            request,
            ErrorCode.BUDGET_EXCEEDED,
            "max_download_bytes is 0, so the PDF was not downloaded",
            details={
                "input_kind": "remote_pdf",
                "max_download_bytes": 0,
                "downloaded_bytes": 0,
                "reason": "download_budget_zero",
            },
        )
    source = _fetch_pdf_source(source_url, max_bytes, dns_cache, deadline=deadline)
    try:
        return _parse_pdf_source(request, source, deadline=deadline)
    finally:
        temp_path = source.get("path")
        if temp_path:
            try:
                Path(str(temp_path)).unlink()
            except OSError:
                pass


def _reuse_pdf_material(request: ReadRequest, source_url: str) -> ReadResponse | None:
    """Serve an already retained PDF when the caller forbids a download."""

    try:
        root = _material_root()
    except OSError:
        return None
    best: tuple[float, Path, dict] | None = None
    try:
        entries = list(root.glob("*.json"))
    except OSError:
        return None
    for entry in entries:
        try:
            stored = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(stored, dict) or stored.get("source_kind") != "pdf":
            continue
        if stored.get("source_url") != _redact_url(source_url):
            continue
        if request.session_id and stored.get("session_id") != request.session_id:
            continue
        expires_at = float(stored.get("expires_at") or 0)
        if expires_at <= time.time():
            continue
        if best is None or expires_at > best[0]:
            best = (expires_at, entry, stored)
    if best is None:
        return None
    _, entry, stored = best
    material_id = entry.stem
    page_spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
    text = str(stored.get("text") or "")
    stored_pages = stored.get("page_coverage") if isinstance(stored.get("page_coverage"), dict) else {}
    blocks: list[MaterialBlock] = []
    returned_chars = 0
    available = _response_char_budget(request)
    for span in page_spans:
        if not isinstance(span, dict):
            continue
        page_number = int(span.get("page_number") or 0)
        page_text = text[int(span.get("start") or 0):int(span.get("end") or 0)]
        if returned_chars + len(page_text) > available:
            break
        blocks.append(MaterialBlock(
            id=f"blk-pdf-page-{page_number:04d}",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=page_text),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=PdfLocator(type="pdf_page", page_number=page_number),
            order=page_number,
        ))
        returned_chars += len(page_text)
    next_offset = None
    if len(blocks) < len(page_spans):
        pending = page_spans[len(blocks)]
        if isinstance(pending, dict):
            next_offset = int(pending.get("start") or 0)
    unresolved = [str(item) for item in (stored.get("source_coverage_unresolved") or [])]
    details = {
        **stored_pages,
        "returned_chars": returned_chars,
        "total_chars": len(text),
        "continuation_available": next_offset is not None,
        "next_offset": next_offset,
        "input_kind": "retained_material",
        "downloaded_bytes": 0,
        "max_download_bytes": 0,
    }
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=str(stored.get("source_revision") or ""),
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if unresolved else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(modalities={
            "text": CoverageItem(
                discovered=int(stored.get("page_count") or len(page_spans)),
                extracted=len(page_spans),
                returned=len(blocks),
                extraction="partial" if unresolved else "complete",
                unresolved=unresolved,
                details=details,
            )
        }, unresolved=unresolved),
    )


FEED_SEPARATOR = "\n\n"


def _render_feed_text(url: str, text: str) -> str | None:
    """Turn an RSS/Atom document into readable lines instead of raw markup."""
    head = text.lstrip()[:400].lower()
    if not text.lstrip().startswith("<"):
        return None
    if "<rss" not in head and "<feed" not in head and "<rdf:rdf" not in head:
        return None
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    def _text_of(node) -> str:
        for child in node:
            if _local(child.tag) in {"title", "name"}:
                return (child.text or "").strip()
        return ""

    def _plain(value: str) -> str:
        cleaned = re.sub(r"<[^>]+>", " ", value or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:400]

    feed_title = _text_of(root) or urlparse(url).hostname or "Feed"
    entries = [child for child in root.iter() if _local(child.tag) in {"item", "entry"}]
    lines = [f"Feed: {feed_title}", f"Source: {url}"]
    for entry in entries[:60]:
        fields: dict[str, str] = {}
        for child in entry:
            fields.setdefault(_local(child.tag), (child.text if child.text else "").strip())
            if _local(child.tag) == "link":
                href = child.get("href") or ""
                if href and "link" not in fields:
                    fields["link"] = href
        title = fields.get("title") or fields.get("name") or "(untitled)"
        link = fields.get("link") or ""
        summary = _plain(fields.get("description") or fields.get("summary") or fields.get("content") or "")
        stamp = fields.get("pubdate") or fields.get("updated") or fields.get("published") or ""
        line = f"- {title}"
        if stamp:
            line += f" ({stamp})"
        if link:
            line += f"\n  {link}"
        if summary:
            line += f"\n  {summary}"
        lines.append(line)
    if len(lines) <= 2:
        return None
    return FEED_SEPARATOR.join([lines[0], "\n".join(lines[1:])])


def _json_payload_has_content(text: str) -> bool:
    """True when a JSON document carries prose, not just an API status stub.

    Platform share endpoints often answer with ``{"result":2,"error_msg":null}``
    plus an id. That is a machine envelope, not content, so it must not be
    delivered as a complete read.
    """

    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return len(text.strip()) >= 200

    def _strings(value, depth: int = 0):
        if depth > 4:
            return
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from _strings(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value[:50]:
                yield from _strings(item, depth + 1)

    return any(len(item.strip()) >= 40 for item in _strings(payload))


_RTF_SKIP_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "header",
    "footer",
    "headerl",
    "headerr",
    "footerl",
    "footerr",
    "listtable",
    "listoverridetable",
    "rsidtbl",
    "generator",
    "xmlnstbl",
    "themedata",
    "colorschememapping",
    "latentstyles",
    "datastore",
    "filetbl",
    "revtbl",
}


def _rtf_to_text(data: bytes) -> str:
    """Plain text of an RTF file, with no dependency beyond the standard library."""

    raw = data.decode("latin-1", errors="replace")
    output: list[str] = []
    depth = 0
    skip_at: list[int] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == "{":
            depth += 1
            match = re.match(r"\{\\([a-zA-Z]+)", raw[index:])
            if match and match.group(1).lower() in _RTF_SKIP_DESTINATIONS:
                skip_at.append(depth)
            index += 1
            continue
        if char == "}":
            if skip_at and skip_at[-1] == depth:
                skip_at.pop()
            depth = max(0, depth - 1)
            index += 1
            continue
        if char == "\\":
            match = re.match(r"\\'([0-9a-fA-F]{2})", raw[index:])
            if match:
                if not skip_at:
                    output.append(
                        bytes([int(match.group(1), 16)]).decode("cp1252", errors="replace")
                    )
                index += len(match.group(0))
                continue
            match = re.match(r"\\u(-?\d+)\s?", raw[index:])
            if match:
                if not skip_at:
                    code = int(match.group(1))
                    output.append(chr(code + 65536 if code < 0 else code))
                index += len(match.group(0))
                # A \u run is followed by a fallback character for old readers.
                if index < length and raw[index] not in "\\{}":
                    index += 1
                continue
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)?\s?", raw[index:])
            if match:
                if not skip_at:
                    word = match.group(1).lower()
                    if word in {"par", "line", "row", "sect", "page"}:
                        output.append("\n")
                    elif word in {"tab", "cell"}:
                        output.append("\t")
                    elif word in {"emdash", "endash"}:
                        output.append("\u2014" if word == "emdash" else "\u2013")
                    elif word in {"lquote", "rquote", "ldblquote", "rdblquote"}:
                        output.append(
                            {
                                "lquote": "\u2018",
                                "rquote": "\u2019",
                                "ldblquote": "\u201c",
                                "rdblquote": "\u201d",
                            }[word]
                        )
                index += len(match.group(0))
                continue
            if raw[index:index + 2] in {"\\{", "\\}", "\\\\"}:
                if not skip_at:
                    output.append(raw[index + 1])
                index += 2
                continue
            index += 1
            continue
        if char in "{}":
            index += 1
            continue
        if not skip_at:
            output.append(char)
        index += 1
    text = "".join(output)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _office_unescape(value: str) -> str:
    return html.unescape(value)


def _office_run_text(blob: str, tag: str) -> str:
    """Collect the text runs of one XML element name, keeping line breaks."""

    parts: list[str] = []
    for match in re.finditer(
        rf"<{tag}\b[^>]*>(.*?)</{tag}>", blob, re.DOTALL | re.IGNORECASE
    ):
        inner = match.group(1)
        inner = re.sub(r"<[^>]+>", "", inner)
        parts.append(_office_unescape(inner))
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _office_document_text(data: bytes, name: str, mime_type: str) -> tuple[str, str]:
    """Text of a ZIP container document, from the standard library only.

    Returns ``(kind, text)`` where kind is one of ``docx``, ``pptx``, ``epub``,
    ``odt``, ``xlsx`` or ``ods``. Unknown or oversized containers raise
    ``PublicPageError`` so the read reports a clear reason instead of dumping
    binary bytes.
    """

    suffix = Path(name).suffix.lower()
    by_type = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template": "docx",
        "application/vnd.ms-word.document.macroenabled.12": "docx",
        "application/vnd.ms-word.template.macroenabled.12": "docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow": "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.template": "pptx",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12": "pptx",
        "application/vnd.ms-powerpoint.template.macroenabled.12": "pptx",
        "application/vnd.ms-powerpoint.slideshow.macroenabled.12": "pptx",
        "application/epub+zip": "epub",
        "application/vnd.oasis.opendocument.text": "odt",
        "application/vnd.oasis.opendocument.spreadsheet": "ods",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template": "xlsx",
        "application/vnd.ms-excel.sheet.macroenabled.12": "xlsx",
        "application/vnd.ms-excel.template.macroenabled.12": "xlsx",
    }
    by_suffix = {
        ".docx": "docx",
        ".docm": "docx",
        ".dotx": "docx",
        ".dotm": "docx",
        ".pptx": "pptx",
        ".pptm": "pptx",
        ".potx": "pptx",
        ".potm": "pptx",
        ".ppsx": "pptx",
        ".ppsm": "pptx",
        ".epub": "epub",
        ".odt": "odt",
        ".xlsx": "xlsx",
        ".xlsm": "xlsx",
        ".xltx": "xlsx",
        ".xltm": "xlsx",
        ".ods": "ods",
    }
    kind = by_type.get(mime_type) or by_suffix.get(suffix)
    if kind is None:
        raise PublicPageError(
            "This ZIP container is not a document Envoy can read",
            ErrorCode.UNSUPPORTED_SOURCE,
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise PublicPageError(
            f"The document ZIP is unreadable ({type(exc).__name__})",
            ErrorCode.UNSUPPORTED_SOURCE,
        ) from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_OFFICE_ENTRIES:
            raise PublicPageError("The document has too many parts to read")
        if sum(info.file_size for info in infos) > _MAX_OFFICE_UNCOMPRESSED:
            raise PublicPageError("The document exceeds Envoy's unpacked size limit")
        names = {info.filename for info in infos}

        def read(name_in_zip: str, limit: int = MAX_TEXT_SOURCE_BYTES) -> str:
            with archive.open(name_in_zip) as handle:
                raw = handle.read(limit)
            return raw.decode("utf-8", errors="replace")

        def paragraphs(xml: str, tag: str, run_tag: str) -> str:
            lines: list[str] = []
            for match in re.finditer(
                rf"<{tag}\b[^>]*>(.*?)</{tag}>", xml, re.DOTALL | re.IGNORECASE
            ):
                text = _office_run_text(match.group(1), run_tag)
                if text.strip():
                    lines.append(text.strip())
            return "\n\n".join(lines)

        if kind == "xlsx":
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                for match in re.finditer(
                    r"<si\b[^>]*>(.*?)</si>",
                    read("xl/sharedStrings.xml"),
                    re.DOTALL | re.IGNORECASE,
                ):
                    shared.append(_office_run_text(match.group(1), "t"))
            rels: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in names:
                for match in re.finditer(
                    r"<Relationship\b[^>]*>", read("xl/_rels/workbook.xml.rels")
                ):
                    attrs = match.group(0)
                    rid = re.search(r'\bId="([^"]+)"', attrs)
                    target = re.search(r'\bTarget="([^"]+)"', attrs)
                    if not rid or not target:
                        continue
                    part = target.group(1).lstrip("/")
                    rels[rid.group(1)] = part if part.startswith("xl/") else f"xl/{part}"
            sheets: list[tuple[str, str]] = []
            if "xl/workbook.xml" in names:
                for match in re.finditer(r"<sheet\b[^>]*>", read("xl/workbook.xml")):
                    attrs = match.group(0)
                    label = re.search(r'\bname="([^"]*)"', attrs)
                    rid = re.search(r'\br:id="([^"]+)"', attrs) or re.search(
                        r'\brelationshipId="([^"]+)"', attrs
                    )
                    part = rels.get(rid.group(1)) if rid else None
                    if part and part in names:
                        sheets.append((label.group(1) if label else "", part))
            if not sheets:
                sheets = [
                    (f"Sheet {index}", sheet_name)
                    for index, sheet_name in enumerate(
                        sorted(
                            name_in_zip
                            for name_in_zip in names
                            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name_in_zip)
                        ),
                        start=1,
                    )
                ]
            if not sheets:
                raise PublicPageError("The spreadsheet has no worksheets")
            chunks: list[str] = []
            for label, part in sheets[:200]:
                rows: list[str] = []
                for row_match in re.finditer(
                    r"<row\b[^>]*>(.*?)</row>", read(part), re.DOTALL | re.IGNORECASE
                ):
                    cells: list[str] = []
                    for cell in re.finditer(
                        r"<c\b([^>]*?)(?:/>|>(.*?)</c>)",
                        row_match.group(1),
                        re.DOTALL | re.IGNORECASE,
                    ):
                        cell_attrs = cell.group(1) or ""
                        inner = cell.group(2) or ""
                        cell_type = re.search(r'\bt="([^"]+)"', cell_attrs)
                        value = re.search(r"<v\b[^>]*>(.*?)</v>", inner, re.DOTALL | re.IGNORECASE)
                        text_cell = ""
                        if cell_type and cell_type.group(1) == "s" and value:
                            try:
                                text_cell = shared[int(value.group(1))]
                            except (ValueError, IndexError):
                                text_cell = ""
                        elif cell_type and cell_type.group(1) == "inlineStr":
                            text_cell = _office_run_text(inner, "t")
                        elif value:
                            text_cell = _office_unescape(value.group(1))
                        cells.append(re.sub(r"\s+", " ", text_cell).strip())
                    line = " | ".join(cells).strip()
                    if line.strip(" |"):
                        rows.append(line)
                if rows:
                    chunks.append(f"{label}\n\n" + "\n".join(rows))
            if not chunks:
                raise PublicPageError("The spreadsheet has no readable cells")
            text = "\n\n".join(chunks)
        elif kind == "ods":
            if "content.xml" not in names:
                raise PublicPageError("The OpenDocument sheet has no content part")
            content = read("content.xml")
            chunks = []
            for table in re.finditer(
                r"<table:table\b([^>]*)>(.*?)</table:table>",
                content,
                re.DOTALL | re.IGNORECASE,
            ):
                attrs, body = table.group(1), table.group(2)
                label = re.search(r'table:name="([^"]*)"', attrs)
                rows = []
                for row in re.finditer(
                    r"<table:table-row\b[^>]*>(.*?)</table:table-row>",
                    body,
                    re.DOTALL | re.IGNORECASE,
                ):
                    cells = []
                    for cell in re.finditer(
                        r"<table:table-cell\b([^>]*?)(?:/>|>(.*?)</table:table-cell>)",
                        row.group(1),
                        re.DOTALL | re.IGNORECASE,
                    ):
                        cell_attrs = cell.group(1) or ""
                        cell_body = cell.group(2) or ""
                        paragraphs = re.findall(
                            r"<text:p\b[^>]*>(.*?)</text:p>",
                            cell_body,
                            re.DOTALL | re.IGNORECASE,
                        )
                        value = " ".join(
                            _office_unescape(re.sub(r"<[^>]+>", "", part)).strip()
                            for part in paragraphs
                            if part.strip()
                        )
                        if not value:
                            numeric = re.search(r'office:value="([^"]*)"', cell_attrs)
                            value = numeric.group(1) if numeric else ""
                        repeat = re.search(r'table:number-columns-repeated="(\d+)"', cell_attrs)
                        times = min(int(repeat.group(1)), 50) if repeat else 1
                        cells.extend([re.sub(r"\s+", " ", value).strip()] * times)
                    while cells and not cells[-1]:
                        cells.pop()
                    line = " | ".join(cells).strip()
                    if line.strip(" |"):
                        rows.append(line)
                if rows:
                    chunks.append(f"{label.group(1) if label else 'Sheet'}\n\n" + "\n".join(rows))
            if not chunks:
                raise PublicPageError("The spreadsheet has no readable cells")
            text = "\n\n".join(chunks)
        elif kind == "docx":
            if "word/document.xml" not in names:
                raise PublicPageError("The Word document has no body part")
            text = paragraphs(read("word/document.xml"), "w:p", "w:t")
        elif kind == "pptx":
            slides = sorted(
                (
                    name_in_zip
                    for name_in_zip in names
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name_in_zip)
                ),
                key=lambda value: int(re.search(r"(\d+)\.xml$", value).group(1)),
            )
            if not slides:
                raise PublicPageError("The presentation has no slides")
            parts: list[str] = []
            for index, slide in enumerate(slides, start=1):
                body = _office_run_text(read(slide), "a:t")
                if body.strip():
                    parts.append(f"Slide {index}\n\n{body.strip()}")
            text = "\n\n".join(parts)
        elif kind == "odt":
            if "content.xml" not in names:
                raise PublicPageError("The OpenDocument text has no content part")
            lines: list[str] = []
            content = read("content.xml")
            for match in re.finditer(
                r"<text:p\b[^>]*>(.*?)</text:p>", content, re.DOTALL | re.IGNORECASE
            ):
                inner = re.sub(r"<text:s/>", " ", match.group(1))
                inner = re.sub(r"<[^>]+>", "", inner)
                inner = re.sub(r"[ \t]+", " ", _office_unescape(inner)).strip()
                if inner:
                    lines.append(inner)
            text = "\n\n".join(lines)
        else:  # epub
            spine: list[str] = []
            if "META-INF/container.xml" in names:
                container = read("META-INF/container.xml")
                match = re.search(r'full-path="([^"]+)"', container)
                if match and match.group(1) in names:
                    package = read(match.group(1))
                    base = str(PurePosixPath(match.group(1)).parent)
                    ids = dict(
                        re.findall(
                            r'<item\b[^>]*id="([^"]+)"[^>]*href="([^"]+)"', package
                        )
                    )
                    ids.update(
                        {
                            item_id: href
                            for href, item_id in re.findall(
                                r'<item\b[^>]*href="([^"]+)"[^>]*id="([^"]+)"', package
                            )
                        }
                    )
                    for idref in re.findall(r'<itemref\b[^>]*idref="([^"]+)"', package):
                        href = ids.get(idref)
                        if not href:
                            continue
                        candidate = (
                            href if base in {"", "."} else f"{base}/{href}"
                        ).replace("./", "/")
                        if candidate in names:
                            spine.append(candidate)
            if not spine:
                spine = sorted(
                    name_in_zip
                    for name_in_zip in names
                    if name_in_zip.lower().endswith((".xhtml", ".html", ".htm"))
                )
            if not spine:
                raise PublicPageError("The EPUB has no readable chapters")
            chapters: list[str] = []
            for chapter in spine[:200]:
                raw = read(chapter)
                plain = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
                plain = re.sub(r"(?i)</(p|div|h[1-6]|li|br)\s*>", "\n\n", plain)
                plain = re.sub(r"<[^>]+>", " ", plain)
                plain = _office_unescape(plain)
                plain = re.sub(r"[ \t]+", " ", plain)
                plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
                if plain:
                    chapters.append(plain)
            text = "\n\n".join(chapters)
    text = text.strip()
    if not text:
        raise PublicPageError("The document has no extractable text", ErrorCode.EMPTY_CONTENT)
    return kind, text


def _with_source_url(source: dict, url: str) -> dict:
    """Report the link the user gave us, not the endpoint the reader hit.

    A share link is rewritten to an export URL before the fetch; the response
    should still name the material by the address that was handed in.
    """

    if str(source.get("url") or "") == url:
        return source
    return {**source, "url": url}


def _parse_office_source(request: ReadRequest, source: dict) -> ReadResponse:
    """Read a .docx/.pptx/.epub/.odt/.xlsx/.ods link as text."""

    url = str(source.get("url") or "")
    kind, text = _office_document_text(
        source["data"], urlparse(url).path, str(source.get("mime_type") or "")
    )
    response = _parse_text_source(
        request,
        {"url": url, "data": text.encode("utf-8"), "mime_type": "text/plain"},
    )
    coverage = response.coverage.model_copy(deep=True)
    for item in coverage.modalities.values():
        item.details = {**item.details, "reader": f"document:{kind}", "document_format": kind}
    return response.model_copy(update={"coverage": coverage})


def _sniff_plain_text(data: bytes) -> tuple[str, str] | None:
    """Decide whether bytes served as octet-stream are really plain text.

    Some hosts hand over text with `application/octet-stream` (a Drive download
    of a `.txt`, a pasted log, an old export). Refusing all of those loses pages
    that would read fine, so a short body is decoded strictly and only accepted
    when nearly every character is printable. Anything with a NUL byte is binary
    and stays refused.
    """

    if not data or b"\x00" in data or len(data) > _SNIFF_MAX_BYTES:
        return None
    for encoding in _PRINTABLE_TABLES:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not text.strip():
            return None
        control = sum(
            1
            for char in text
            if ord(char) < 32 and char not in "\n\r\t\f\v"
        )
        if control * 20 > len(text):
            return None
        return data, encoding
    return None


def _text_document_title(url: str) -> str:
    """Name a text block by its file name, or by the host when the link has none.

    A share link such as …/document/d/<id>/edit carries no file name, and calling
    the block "edit" would be noise, so those fall back to the host.
    """

    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    if "." in name:
        return name
    return (parsed.hostname or "").lower() or "Text document"


def _parse_text_source(request: ReadRequest, source: dict) -> ReadResponse:
    data = source["data"]
    source_url = str(source.get("url") or "")
    mime = str(source.get("mime_type") or "").lower()
    is_rtf = mime in RTF_CONTENT_TYPES or urlparse(source_url).path.lower().endswith(
        RTF_SUFFIXES
    )
    if is_rtf:
        text = _rtf_to_text(data)
    else:
        text = data.decode(str(source.get("encoding") or "utf-8"), errors="replace")
        text = re.sub(
            r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n")
        ).strip()
    feed_text = _render_feed_text(source["url"], text)
    if feed_text:
        text = feed_text
    if not text:
        raise PublicPageError("The text document is empty", ErrorCode.EMPTY_CONTENT)
    mime_type = str(source.get("mime_type") or "").lower()
    if mime_type.endswith("json") and not _json_payload_has_content(text):
        raise PublicPageError(
            "The endpoint returned a JSON status stub instead of readable content",
            ErrorCode.EMPTY_CONTENT,
        )
    if mime_type.endswith("json"):
        text = _pretty_json_text(text)
    source_truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    title = _text_document_title(source["url"])
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
                    **({"reader": "document:rtf", "document_format": "rtf"} if is_rtf else {}),
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


def _requested_ocr_language(request: ReadRequest) -> str:
    return (
        request.ocr_language
        or os.getenv("ENVOY_OCR_LANG", "").strip()
        or "auto"
    )


def _requested_ocr_backend(request: ReadRequest) -> str:
    return (
        getattr(request, "ocr_backend", None)
        or os.getenv("ENVOY_OCR_BACKEND", "").strip().lower()
        or DEFAULT_OCR_BACKEND
    )


def _read_image_source(request: ReadRequest, source_url: str, dns_cache: dict[str, str]) -> ReadResponse:
    can_handoff_image = request.host_capabilities.image_input == ImageInputCapability.URI
    if not can_handoff_image and not request.ocr_profile:
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
    block_id = "blk-image-0001"
    ocr_blocks = []
    ocr_unresolved = []
    ocr_spans = []
    returned_ranges = []
    image_sha256 = None
    ocr_text = ""
    returned_chars = 0
    returned_tokens = 0
    ocr_result = {}
    ocr_language = _requested_ocr_language(request)
    ocr_backend = _requested_ocr_backend(request)
    if request.ocr_profile:
        try:
            image_path, image_sha256 = _download_image_for_ocr(
                source_url,
                max_bytes=request.budget.max_download_bytes,
                dns_cache=dns_cache,
            )
            ocr_result = _read_cached_image_ocr(
                image_path,
                image_sha256,
                profile=request.ocr_profile,
                language=ocr_language,
                backend=ocr_backend,
            )
            ocr_language = str(ocr_result.get("language") or ocr_language)
            text_parts = []
            pending_spans = []
            cursor = 0
            for region in ocr_result.get("regions") or []:
                text_value = str(region.get("text") or "").strip()
                bbox = region.get("bbox")
                if not text_value or not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                try:
                    left, top, right, bottom = (float(value) for value in bbox)
                    confidence = max(0.0, min(1.0, float(region.get("confidence") or 0)))
                except (TypeError, ValueError):
                    continue
                if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                    continue
                start = cursor
                end = start + len(text_value)
                if text_parts:
                    cursor += 1
                    start += 1
                    end += 1
                text_parts.append(text_value)
                cursor = end
                span = {
                    "start": start,
                    "end": end,
                    "resource_id": resource_id,
                    "source_block_id": block_id,
                    "bbox": [left, top, right, bottom],
                    "confidence": confidence,
                    "engine": f"{ocr_result.get('backend', 'ocr')}-{request.ocr_profile}",
                }
                pending_spans.append(span)
            if not pending_spans:
                ocr_unresolved.append("OCR returned no valid text regions")
            ocr_text = "\n".join(text_parts)
            char_limits = [
                request.budget.max_response_chars,
                request.budget.max_total_response_chars,
            ]
            max_chars = min(char_limits)
            token_limits = [
                value
                for value in (
                    request.budget.max_response_tokens,
                    request.budget.max_total_response_tokens,
                )
                if value is not None
            ]
            max_tokens = min(token_limits) if token_limits else None
            for span in pending_spans:
                text_value = ocr_text[span["start"]:span["end"]]
                region_tokens = _estimated_tokens(text_value)
                if (
                    returned_chars + len(text_value) > max_chars
                    or (
                        max_tokens is not None
                        and returned_tokens + region_tokens > max_tokens
                    )
                ):
                    ocr_unresolved.append(
                        "OCR regions were limited by the response character or token budget"
                    )
                    continue
                ocr_blocks.append(MaterialBlock(
                    id=f"{block_id}-ocr-{len(ocr_blocks) + 1:03d}",
                    kind=MaterialKind.OCR,
                    content=TextContent(type="text", text=text_value),
                    origin=MaterialOrigin.OCR,
                    locator=ImageRegionLocator(
                        type="image_region",
                        resource_id=resource_id,
                        coordinate_system="normalized_top_left",
                        bbox=span["bbox"],
                    ),
                    parent_id=block_id,
                    order=len(ocr_blocks) + 1,
                    relations=[Relation(
                        type="derived_from",
                        target_id=block_id,
                        basis="extraction",
                    )],
                    quality=Quality(
                        engine=span["engine"],
                        engine_score=span["confidence"],
                        unresolved=[
                            "OCR is a text extraction hint; inspect the source image for layout or ambiguity"
                        ],
                    ),
                ))
                span["returned"] = True
                returned_ranges.append([span["start"], span["end"]])
                returned_chars += len(text_value)
                returned_tokens += region_tokens
            returned_span_keys = {
                (span["start"], span["end"])
                for span in pending_spans
                if span.get("returned")
            }
            ocr_spans = [
                {
                    **span,
                    "returned": (span["start"], span["end"]) in returned_span_keys,
                }
                for span in pending_spans
            ]
            zero_confidence_regions = _ocr_zero_confidence_regions(
                ocr_result.get("regions")
            )
            if zero_confidence_regions:
                ocr_unresolved.append(
                    f"{zero_confidence_regions} OCR regions have zero confidence and need visual review"
                )
        except (
            OSError,
            ValueError,
            PublicPageError,
            MediaBudgetExceeded,
            RuntimeError,
            httpx.HTTPError,
        ) as exc:
            ocr_unresolved.append(f"OCR skipped for {resource_id} ({type(exc).__name__})")
    source_revision = image_sha256 or hashlib.sha256(source["url"].encode()).hexdigest()
    material_id = _material_id(
        source["url"], source_revision, request.session_id, request.request_id
    )
    if request.retention.derived_ttl_seconds > 0:
        try:
            _store_material(material_id, {
                "source_kind": "image",
                "source_url": source["url"],
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": ocr_text,
                "title_text": request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image",
                "visual_resources": ([{
                    "resource_id": resource_id,
                    "source_block_id": block_id,
                    "source_url": source["url"],
                    **({"sha256": image_sha256} if image_sha256 else {}),
                }] if can_handoff_image else []),
                **({"ocr_spans": ocr_spans} if ocr_spans else {}),
                "budget_state": {
                    "max_total_response_chars": request.budget.max_total_response_chars,
                    "max_total_response_tokens": request.budget.max_total_response_tokens,
                    "max_expansion_rounds": 0,
                    "returned_chars": returned_chars,
                    "returned_tokens": returned_tokens,
                    "expansion_rounds": 0,
                    "returned_ranges": returned_ranges,
                },
                "expires_at": time.time() + min(
                    request.retention.derived_ttl_seconds,
                    MATERIAL_TTL_SECONDS,
                ),
            })
        except OSError:
            pass
    ocr_complete = bool(ocr_blocks) and not ocr_unresolved
    response_errors = []
    if not can_handoff_image and not ocr_blocks:
        response_errors.append(ReadError(
            code=ErrorCode.EMPTY_CONTENT,
            message="Local OCR did not produce readable text and the host cannot inspect the source image",
        ))
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=(
            ExecutionState.WAITING_FOR_HOST
            if can_handoff_image
            else ExecutionState.COMPLETED
        ),
        result=(
            ReadResult.NEEDS_HOST
            if can_handoff_image
            else ReadResult.READY
            if ocr_complete
            else ReadResult.PARTIAL
            if ocr_blocks
            else ReadResult.BLOCKED
        ),
        blocks=[
            MaterialBlock(
                id=block_id,
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
                    block_id=block_id,
                    quote=request.goal[:MAX_LOCATOR_QUOTE_CHARS] or "Source image",
                ),
                order=0,
            ),
            *ocr_blocks,
        ],
        resources=[ResourceRef(
            resource_id=resource_id,
            mime_type=source["mime_type"],
            delivery=HostDelivery(type="uri", uri=source["url"]),
            sha256=image_sha256,
            delivery_evidence="unverified",
        )],
        needs=[Need(
            action_id="inspect-image-0001",
            type="inspect_visual",
            purpose=request.goal or "Inspect this image",
            resource_ids=[resource_id],
            source_block_ids=[block_id],
            reason="The source contains visual information that should be inspected by the host model.",
            max_resources=1,
        )] if can_handoff_image else [],
        coverage=Coverage(
            modalities={
                "images": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=1,
                    delivered=1 if can_handoff_image else 0,
                    extraction="complete" if can_handoff_image else "partial",
                    unresolved=(
                        []
                        if can_handoff_image
                        else ["Original image was not delivered because the host lacks URI image input"]
                    ),
                ),
                **({
                    "ocr": CoverageItem(
                        discovered=len(ocr_spans),
                        extracted=len(ocr_spans),
                        returned=len(ocr_blocks),
                        extraction=(
                            "complete"
                            if not ocr_unresolved
                            and len(ocr_blocks) == len(ocr_spans)
                            else "partial"
                        ),
                        unresolved=ocr_unresolved,
                        details={
                            "profile": request.ocr_profile,
                            "language": ocr_language,
                            "region_count": len(ocr_spans),
                            "returned_regions": len(ocr_blocks),
                            "zero_confidence_regions": _ocr_zero_confidence_regions(
                                ocr_result.get("regions") if isinstance(ocr_result, dict) else []
                            ),
                            "cache_hit": bool(
                                isinstance(ocr_result, dict)
                                and ocr_result.get("cache_hit")
                            ),
                            **_token_budget_details(
                                request,
                                returned_tokens,
                                "\n".join(
                                    ocr_text[span["start"]:span["end"]]
                                    for span in ocr_spans
                                    if not span.get("returned")
                                ),
                            ),
                        },
                    )
                } if request.ocr_profile else {}),
            },
            unresolved=ocr_unresolved,
        ),
        errors=response_errors,
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


def _video_title_block(source_url: str, title: str) -> MaterialBlock:
    return MaterialBlock(
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


def _video_transcript_lines(request: ReadRequest, extracted: dict) -> dict:
    """Format cues into numbered transcript lines whose spans index the text.

    This is the single place that decides which cues the requested window
    contains; both the stored material and the browser-video coverage read the
    delivered text through it, so they cannot disagree about the same budget.
    """

    is_asr = str(extracted.get("source") or "") == "asr"
    transcript_source = "asr" if is_asr else (
        "automatic_subtitles" if extracted.get("automatic") else "manual_subtitles"
    )
    transcript_origin = MaterialOrigin.ASR if is_asr else MaterialOrigin.SUBTITLE
    transcript_processing_partial = (
        is_asr and extracted.get("transcription_status") == "partial"
    )
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
    return {
        "is_asr": is_asr,
        "transcript_source": transcript_source,
        "transcript_origin": transcript_origin,
        "transcript_processing_partial": transcript_processing_partial,
        "title": title,
        "language": language,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "parts": parts,
        "spans": spans,
        "full_text": full_text,
        "no_cues_in_requested_range": no_cues_in_requested_range,
        "source_truncated": source_truncated,
    }


def _video_transcript_budget(
    request: ReadRequest,
    lines: dict,
    title_block: MaterialBlock,
    *,
    consumed_chars: int = 0,
    consumed_tokens: int = 0,
) -> dict:
    """Take the delivered transcript text under the shared response budget.

    ``consumed_chars``/``consumed_tokens`` let a caller that already wrote page
    blocks into the same response charge them against the one budget, so a
    token limit applies to the whole answer rather than to each path alone.
    """

    full_text = lines["full_text"]
    spent_chars = max(0, int(consumed_chars))
    spent_tokens = max(0, int(consumed_tokens))
    available_chars = max(
        0,
        _response_char_budget(request) - len(title_block.model_dump_json()) - 256 - spent_chars,
    )
    response_token_limit = request.budget.max_response_tokens
    if request.budget.max_total_response_tokens is not None:
        response_token_limit = (
            min(response_token_limit, request.budget.max_total_response_tokens)
            if response_token_limit is not None
            else request.budget.max_total_response_tokens
        )
    title_tokens = _estimated_tokens(lines["title"])
    body_token_limit = (
        max(0, response_token_limit - title_tokens - spent_tokens)
        if response_token_limit is not None
        else None
    )
    returned_text, returned_end = _take_text_budgeted(
        full_text,
        0,
        available_chars,
        body_token_limit,
    )
    returned_tokens = title_tokens + spent_tokens + _estimated_tokens(returned_text)
    response_partial = (
        returned_end < len(full_text)
        or lines["source_truncated"]
        or lines["transcript_processing_partial"]
    )
    return {
        "available_chars": available_chars,
        "response_token_limit": response_token_limit,
        "title_tokens": title_tokens,
        "body_token_limit": body_token_limit,
        "returned_text": returned_text,
        "returned_end": returned_end,
        "returned_tokens": returned_tokens,
        "response_partial": response_partial,
    }


def _video_subtitle_response(
    request: ReadRequest,
    source_url: str,
    extracted: dict,
    *,
    consumed_chars: int = 0,
    consumed_tokens: int = 0,
) -> ReadResponse:
    lines = _video_transcript_lines(request, extracted)
    is_asr = lines["is_asr"]
    transcript_source = lines["transcript_source"]
    transcript_origin = lines["transcript_origin"]
    transcript_processing_partial = lines["transcript_processing_partial"]
    title = lines["title"]
    language = lines["language"]
    requested_start = lines["requested_start"]
    requested_end = lines["requested_end"]
    spans = lines["spans"]
    full_text = lines["full_text"]
    source_truncated = lines["source_truncated"]
    no_cues_in_requested_range = lines["no_cues_in_requested_range"]
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
    title_block = _video_title_block(source_url, title)
    budget = _video_transcript_budget(
        request,
        lines,
        title_block,
        consumed_chars=consumed_chars,
        consumed_tokens=consumed_tokens,
    )
    returned_text = budget["returned_text"]
    returned_end = budget["returned_end"]
    returned_tokens = budget["returned_tokens"]
    response_partial = budget["response_partial"]
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
    speech_confidence = "normal"
    speech_seconds = 0.0
    speech_ratio = 0.0
    processed_from = float(requested_start or 0.0)
    processed_until = extracted.get("processed_until_seconds")
    if is_asr:
        speech_confidence, speech_seconds, speech_ratio = _transcript_confidence(
            spans,
            extracted.get("duration"),
            processed_from=processed_from,
            processed_until=processed_until,
        )
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
    if is_asr and speech_confidence == "low":
        scored_span = max(
            float(processed_until or extracted.get("duration") or 0) - processed_from,
            0.0,
        )
        video_unresolved.append(
            "The audio is mostly music or noise: "
            f"{len(spans)} short speech segment(s) cover {speech_ratio:.1%} of the "
            f"{scored_span:.0f}s transcribed so far, so the transcript "
            "may be unreliable"
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
                        "speech_seconds": round(speech_seconds, 3),
                        "speech_coverage": round(speech_ratio, 4),
                        "transcript_confidence": speech_confidence,
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
        # The checkpoint must follow the resolved video, not the temp directory
        # the audio was downloaded into: a re-signed URL serving the same media
        # resumes, a different revision starts fresh.
        video_id = str(downloaded.get("video_id") or "")
        asr_source_revision = (
            f"video:{video_id}:{downloaded.get('duration')}:{downloaded.get('downloaded_bytes')}"
            if video_id
            else None
        )
        asr_result = transcribe_audio_file(
            downloaded["path"],
            model_name=status["asr_model"],
            download_root=asr_model_cache_dir(),
            max_work_seconds=max(int(remaining_work), 1),
            start_seconds=request.scope.start_seconds or 0,
            end_seconds=request.scope.end_seconds or downloaded.get("duration"),
            source_revision=asr_source_revision,
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
                "Visual frames were requested but the host did not declare video_input=frames and image_input=local_file; send host_capabilities {video_input: frames, image_input: local_file} to receive frames",
            ])),
            "details": {
                **video_coverage.details,
                "visual_requirement": {
                    "host_capabilities": {
                        "video_input": "frames",
                        "image_input": "local_file",
                    },
                    "retention": {"raw_media": "persistent"},
                },
            },
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
                duration_seconds=duration,
            )
            if not extracted_frames:
                raise MediaProcessingError("No video frames were extracted")

            blocks = list(response.blocks)
            resources = list(response.resources)
            visual_records = []
            ocr_blocks = []
            ocr_unresolved = []
            zero_confidence_regions = 0
            ocr_language = _requested_ocr_language(request)
            ocr_backend = _requested_ocr_backend(request)
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
                if request.ocr_profile:
                    try:
                        ocr_result = _read_cached_image_ocr(
                            destination,
                            str(frame["sha256"]),
                            profile=request.ocr_profile,
                            language=ocr_language,
                            backend=ocr_backend,
                        )
                        ocr_language = str(ocr_result.get("language") or ocr_language)
                        zero_confidence_regions += _ocr_zero_confidence_regions(
                            ocr_result.get("regions")
                        )
                        for region_index, region in enumerate(
                            ocr_result.get("regions") or [], start=1
                        ):
                            text_value = str(region.get("text") or "").strip()
                            bbox = region.get("bbox")
                            if not text_value or not isinstance(bbox, list) or len(bbox) != 4:
                                continue
                            ocr_blocks.append(MaterialBlock(
                                id=f"{block_id}-ocr-{region_index:03d}",
                                kind=MaterialKind.OCR,
                                content=TextContent(type="text", text=text_value),
                                origin=MaterialOrigin.OCR,
                                locator=ImageRegionLocator(
                                    type="image_region",
                                    resource_id=resource_id,
                                    coordinate_system="normalized_top_left",
                                    bbox=[float(value) for value in bbox],
                                ),
                                parent_id=block_id,
                                order=len(blocks) + len(ocr_blocks),
                                relations=[Relation(
                                    type="derived_from",
                                    target_id=block_id,
                                    basis="extraction",
                                )],
                                quality=Quality(
                                    engine=f"{ocr_result.get('backend', 'ocr')}-{request.ocr_profile}",
                                    engine_score=float(region.get("confidence") or 0),
                                    unresolved=[
                                        f"OCR region belongs to video frame at {timestamp:.3f}s"
                                    ],
                                ),
                            ))
                        if not any(
                            block.parent_id == block_id
                            and block.kind == MaterialKind.OCR
                            for block in ocr_blocks
                        ):
                            ocr_unresolved.append(
                                f"OCR returned no valid text regions for frame {frame_name}"
                            )
                    except (OSError, ValueError, PublicPageError, MediaBudgetExceeded, RuntimeError) as exc:
                        ocr_unresolved.append(
                            f"OCR skipped for frame {frame_name} ({type(exc).__name__})"
                        )
                visual_records.append({
                    "resource_id": resource_id,
                    "source_block_id": block_id,
                    "source_url": _redact_url(source_url),
                    "frame_path": str(destination),
                    "timestamp": timestamp,
                })
            if zero_confidence_regions:
                ocr_unresolved.append(
                    f"{zero_confidence_regions} frame OCR regions have zero confidence and need visual review"
                )
            blocks.extend(ocr_blocks)
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
    updated_response = response.model_copy(update={
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
    if request.ocr_profile:
        updated_response = updated_response.model_copy(update={
            "coverage": updated_response.coverage.model_copy(update={
                "modalities": {
                    **updated_response.coverage.modalities,
                    "ocr": CoverageItem(
                        discovered=len(visual_records),
                        extracted=len(ocr_blocks),
                        returned=len(ocr_blocks),
                        extraction="complete" if not ocr_unresolved else "partial",
                        unresolved=ocr_unresolved,
                        details={
                            "profile": request.ocr_profile,
                            "language": ocr_language,
                            "timestamp_source": "parent_video_frame_locator",
                            "frame_timestamps": [item["timestamp"] for item in visual_records],
                        },
                    ),
                },
            }),
        })
    return updated_response


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
        return _no_speech_partial(
            request,
            media_url,
            str(exc),
            title=str(media_source.get("title") or ""),
        )
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


_CHALLENGE_RETRY_DELAY_SECONDS = 1.0


def _fallback_min_chars(fallback: dict) -> int:
    """How short a public-endpoint answer may be before rendering beats it.

    A generic endpoint is only worth taking when it clearly beats the page, so
    the floor is high. A backend that owns the whole article (a package readme, a
    post that is genuinely short) declares its own floor instead.
    """

    try:
        declared = int(fallback.get("min_chars") or 0)
    except (TypeError, ValueError):
        declared = 0
    return declared if declared > 0 else _PUBLIC_API_FALLBACK_MIN_CHARS


_FEED_ALTERNATE_PAGE_CHARS = 300

# Douyin renders its caption from a signed JSON call after the shell loads, so the
# same URL answers with the caption one run and with the bare shell the next. When a
# video page comes back with only shell text, the page itself is still readable; one
# more browser pass is the difference between a caption and an empty answer.
_SHORT_VIDEO_RETRY_HOSTS = frozenset({"douyin.com", "www.douyin.com", "m.douyin.com"})
_SHORT_VIDEO_RETRY_CHARS = 400
_ALTERNATE_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ALTERNATE_ATTR = re.compile(r"""([a-zA-Z:-]+)\s*=\s*["']([^"']*)["']""")


def _read_feed_alternate(
    request: ReadRequest, source_url: str, final_url: str, html: str
) -> ReadResponse | None:
    """Follow a page's declared RSS/Atom alternate when the page itself is thin.

    Many blogs and news sites ship a script-rendered index but also declare a
    feed in the head. Reading that feed recovers real items when the rendered
    page carries a menu and nothing else.
    """

    if not html:
        return None
    alternates: list[str] = []
    for tag in _ALTERNATE_LINK.findall(html[:200_000]):
        attrs = {name.lower(): value for name, value in _ALTERNATE_ATTR.findall(tag)}
        rel = attrs.get("rel", "").lower()
        kind = attrs.get("type", "").lower()
        href = attrs.get("href", "").strip()
        if "alternate" not in rel or not href:
            continue
        if not any(marker in kind for marker in ("rss", "atom", "xml")):
            continue
        alternates.append(urljoin(final_url, href))
    for alternate in alternates[:2]:
        if not _is_public_http_url(alternate, {}):
            continue
        try:
            feed = fetch_feed(
                alternate,
                max_work_seconds=min(
                    _remaining_work_seconds(request), _PUBLIC_API_FALLBACK_SECONDS
                ),
            )
        except Exception:  # noqa: BLE001 - an alternate must never fail a read
            continue
        if feed is None:
            continue
        return _public_api_fallback_response(
            request,
            source_url,
            {**feed, "backend": "feed_alternate"},
        )
    return None


def _public_api_fallback_response(
    request: ReadRequest, source_url: str, fallback: dict
) -> ReadResponse:
    """Answer from a site's own public endpoint instead of its rendered page.

    A few sites serve a browser shell and keep the readable text behind a public
    JSON API (a question body, a package readme). Reading that endpoint is both
    faster and more complete than parsing the shell, and the reply says which
    reader produced it so the evidence stays traceable.
    """

    title = _clean_text([str(fallback.get("title") or "")]) or source_url
    backend = str(fallback.get("backend") or "public_api")
    text = str(fallback.get("text") or "").strip()
    char_budget = _response_char_budget(request)
    truncated = len(text) > char_budget
    if truncated:
        text = text[:char_budget]
    source_revision = hashlib.sha256(
        f"{source_url}\n{backend}\n{text}".encode("utf-8", errors="replace")
    ).hexdigest()
    material_id = _material_id(
        source_url, source_revision, request.session_id, request.request_id
    )
    blocks = [
        MaterialBlock(
            id="blk-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=title),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id="blk-title",
                quote=title[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=0,
        )
    ]
    if text:
        blocks.append(
            MaterialBlock(
                id="blk-body-0001",
                kind=MaterialKind.PARAGRAPH,
                content=TextContent(type="text", text=text),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=WebLocator(
                    type="web",
                    source_url=source_url,
                    block_id="blk-body-0001",
                    quote=text[:MAX_LOCATOR_QUOTE_CHARS],
                ),
                parent_id="blk-title",
                order=1,
            )
        )
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=material_id,
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL if truncated else ReadResult.READY,
        blocks=blocks,
        coverage=Coverage(
            modalities={
                "text": CoverageItem(
                    discovered=1,
                    extracted=1,
                    returned=1 if text else 0,
                    extraction="partial" if truncated else ("complete" if text else "failed"),
                    unresolved=(
                        ["The endpoint returned more text than the response budget allowed"]
                        if truncated
                        else []
                    ),
                    details={
                        "reader": f"public_api:{backend}",
                        "backend": backend,
                        "total_chars": len(str(fallback.get("text") or "")),
                        "returned_chars": len(text),
                        "continuation_available": False,
                    },
                )
            }
        ),
    )


def _read_video_page_text(request: ReadRequest, source_url: str) -> ReadResponse | None:
    """Read a video page as plain text when no transcript path is available.

    A video page still carries the title, the description, chapters and links;
    blocking the whole read hides material the page itself published. This
    retries the page with the media route disabled and only keeps the result
    when it actually produced body text.
    """

    if request.scope.include_visual:
        return None
    if _SKIP_MEDIA_FIRST.get():
        return None
    token = _SKIP_MEDIA_FIRST.set(True)
    try:
        fallback = _read_public_page_once(request)
    except Exception:  # noqa: BLE001 - a failed fallback must not mask the block
        return None
    finally:
        _SKIP_MEDIA_FIRST.reset(token)
    if fallback.result not in {ReadResult.READY, ReadResult.PARTIAL}:
        return None
    body_chars = sum(
        len(block.content.text or "")
        for block in fallback.blocks
        if getattr(block.content, "text", None)
    )
    if body_chars < 200:
        return None
    fallback = fallback.model_copy(update={
        "errors": [
            *fallback.errors,
            ReadError(
                code=ErrorCode.MISSING_CAPABILITY,
                message=(
                    "No transcript is available for this video; the text below is the page itself"
                ),
                retryable=False,
                action_required=False,
            ),
        ],
    })
    return fallback


def read_public_page(request: ReadRequest) -> ReadResponse:
    token = _SHORT_PUBLIC_FALLBACK.set(None)
    try:
        return _read_public_page_with_retries(request)
    finally:
        _SHORT_PUBLIC_FALLBACK.reset(token)


def _read_public_page_with_retries(request: ReadRequest) -> ReadResponse:
    response = _read_public_page_once(request)
    for attempt in range(1, 2):
        if response.result != ReadResult.BLOCKED:
            break
        if not any(
            error.code
            in {ErrorCode.CHALLENGE_REQUIRED, ErrorCode.AUTH_REQUIRED}
            and error.action_required
            for error in response.errors
        ):
            break
        try:
            if _remaining_work_seconds(request) < 5:
                break
        except MediaBudgetExceeded:
            break
        time.sleep(_CHALLENGE_RETRY_DELAY_SECONDS)
        retried = _read_public_page_once(request)
        response = retried.model_copy(update={
            "errors": [
                error.model_copy(update={
                    "retryable": True,
                    "details": {
                        **(error.details or {}),
                        "internal_retry_attempts": attempt,
                    },
                })
                for error in retried.errors
            ],
        })
    short_fallback = _SHORT_PUBLIC_FALLBACK.get()
    if (
        short_fallback is not None
        and response.result == ReadResult.BLOCKED
        and any(
            error.code in {ErrorCode.NETWORK_ERROR, ErrorCode.SOURCE_UNAVAILABLE}
            for error in response.errors
        )
    ):
        rescued = _public_api_fallback_response(
            request, str(request.source.url), short_fallback
        )
        unreachable = next(
            (
                error
                for error in response.errors
                if error.code in {ErrorCode.NETWORK_ERROR, ErrorCode.SOURCE_UNAVAILABLE}
            ),
            None,
        )
        coverage = rescued.coverage.model_copy(deep=True)
        for item in coverage.modalities.values():
            item.details = {
                **item.details,
                "page_unreachable": True,
                "page_unreachable_cause": unreachable.code.value if unreachable else None,
                "note": (
                    "The page itself could not be reached; this text is what the "
                    "site's public endpoint returned, and it is short"
                ),
            }
        return rescued.model_copy(update={"coverage": coverage})
    return response


@_serialize_read_request
def _read_public_page_once(request: ReadRequest) -> ReadResponse:
    source_url = request.source.url
    dns_cache: dict[str, str] = {}
    browser_snapshot = None
    browser_snapshot_for_video = False
    if not _is_public_http_url(source_url, dns_cache):
        # The guard's verdict is cached by host, so this call reuses the same
        # lookup instead of resolving the name a second time.
        if _public_url_rejection(source_url, dns_cache) == DNS_FAILURE:
            return _blocked(
                request,
                ErrorCode.NETWORK_ERROR,
                "The host name does not resolve from this machine, so the source cannot be reached (DNS lookup failed)",
                details={"reader": "url_guard"},
            )
        return _blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "Only public HTTP(S) URLs are allowed")
    source_path = urlparse(source_url).path.lower()
    if is_collection_url(source_url):
        return _blocked(
            request,
            ErrorCode.UNSUPPORTED_SOURCE,
            "This link selects a set of videos, not one. "
            "Pass one item link (a watch?v=... or /video/BV... URL), or enumerate the "
            "set with envoy_discover on the creator space or the collection link",
            details={"reader": "collection_guard"},
        )
    direct_video_path = source_path.endswith(DIRECT_VIDEO_SUFFIXES)
    direct_douyin_note = (
        urlparse(source_url).hostname in {"douyin.com", "www.douyin.com"}
        and bool(re.match(r"^/note/[^/]+", source_path))
    )
    direct_xhs_note = classify_public_url(source_url) in {
        "xiaohongshu_note", "xiaohongshu_share",
    }
    media_first = _should_read_media_first(source_url, bool(request.scope.include_visual))
    if not request.scope.include_visual and looks_like_feed_url(source_url):
        try:
            feed = fetch_feed(
                source_url,
                max_work_seconds=min(
                    _remaining_work_seconds(request), _PUBLIC_API_FALLBACK_SECONDS
                ),
            )
        except Exception:  # noqa: BLE001 - a feed probe must never fail a read
            feed = None
        if feed is not None:
            return _public_api_fallback_response(request, source_url, feed)

    if not request.scope.include_visual:
        try:
            fallback = public_api_fallback(
                source_url,
                goal=request.goal,
                max_work_seconds=min(
                    _remaining_work_seconds(request), _PUBLIC_API_FALLBACK_SECONDS
                ),
            )
        except Exception:  # noqa: BLE001 - a fallback must never fail a read
            fallback = None
        if fallback is not None:
            if len(str(fallback.get("text") or "")) >= _fallback_min_chars(fallback):
                return _public_api_fallback_response(request, source_url, fallback)
            _SHORT_PUBLIC_FALLBACK.set(fallback)

    if (
        not direct_video_path
        and not direct_douyin_note
        and not direct_xhs_note
        and has_specialized_extractor(source_url)
        and media_first
    ):
        subtitle_result = extract_public_subtitles(
            source_url,
            preferred_language=request.video_subtitle_language,
            max_download_bytes=request.budget.max_download_bytes,
            # Subtitle discovery is the cheap leg of a video read. A site whose
            # extractor answers slowly (signed APIs, anti-bot handshakes) must
            # not consume the whole budget before the page fallback runs; the
            # deep media budget belongs to ASR, which the caller opts into.
            max_work_seconds=min(_remaining_work_seconds(request), _SUBTITLE_PHASE_SECONDS),
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
                        details={
                            "needs_user_action": _login_user_action(
                                source_url,
                                kind="cookies_required",
                            )
                        },
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
                    return _no_speech_partial(
                        request,
                        source_url,
                        str(exc),
                        title=str(subtitle_result.get("title") or ""),
                        duration=subtitle_result.get("duration"),
                    )
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
                    page_text = _read_video_page_text(request, source_url)
                    if page_text is not None:
                        return page_text
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
        platform_retry_used = False
        browser_used = browser_snapshot is not None
        if browser_snapshot is not None:
            final_url, html = browser_snapshot.url, browser_snapshot.html
        else:
            try:
                final_url, html = _fetch_html(
                    share_export_url(source_url),
                    min(MAX_HTML_BYTES, request.budget.max_download_bytes or MAX_HTML_BYTES),
                    dns_cache,
                )
            except DetectedPDF as detected_pdf:
                return _parse_pdf_source(
                    request, _with_source_url(detected_pdf.source, source_url)
                )
            except DetectedOffice as detected_office:
                return _parse_office_source(
                    request, _with_source_url(detected_office.source, source_url)
                )
            except DetectedText as detected_text:
                return _parse_text_source(
                    request, _with_source_url(detected_text.source, source_url)
                )
            except DetectedImage:
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
            and (
                request.source.kind.value == "video"
                or _url_is_video_page(final_url)
                or is_platform_page(final_url)
            )
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
        if _looks_like_missing_page(title, text):
            return _blocked(
                request,
                ErrorCode.SOURCE_UNAVAILABLE,
                "The source page no longer exists or is not publicly viewable; "
                "the platform returned its own error page instead of content.",
                details={"diagnosis": "source_not_found"},
            )
        if (
            _looks_like_login_wall(title, text)
            and not browser_attempted
            and not _looks_like_access_challenge(title, text)
            and not _looks_like_machine_error_payload(text)
        ):
            # Booting the isolated browser here only ever shows the same wall,
            # so fail fast and hand the sign-in back to the user (85.9s -> ~2s
            # on https://mp.weixin.qq.com/, whose landing page is a login form).
            return _blocked(
                request,
                ErrorCode.AUTH_REQUIRED,
                "The source returned a sign-in wall instead of its content",
                action_required=True,
                details={"reader": "static_http"},
            )
        if _looks_like_access_challenge(title, text) or _looks_like_machine_error_payload(text):
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
        if _looks_like_access_challenge(
            title, text
        ) and _challenge_suggests_egress_issue(title, text):
            fallback_proxy = explicit_proxy() or detected_proxy()
            if fallback_proxy:
                print(
                    "Envoy retried the source through a proxy after the network "
                    "exit was blocked.",
                    file=sys.stderr,
                    flush=True,
                )
                proxy_page, _proxy_error = _read_with_isolated_browser(
                    request,
                    source_url,
                    dns_cache,
                    only_proxy=True,
                )
                if proxy_page is not None:
                    final_url, html = proxy_page.url, proxy_page.html
                    browser_snapshot = proxy_page
                    browser_used = True
                    browser_snapshot_for_video = _browser_page_has_video(
                        proxy_page,
                        request.source.kind.value,
                    )
                    parser = _PageParser()
                    parser.feed(html)
                    title = _clean_text(parser.title_parts) or final_url
                    source_text_truncated = _source_text_was_truncated(
                        parser.text_parts
                    )
                    text = _clean_text(parser.text_parts)
                    browser_error = None
        if _looks_like_access_challenge(title, text) or _looks_like_machine_error_payload(text):
            return _blocked(
                request,
                _blocked_challenge_code(browser_error),
                "The page still requires login or security verification. "
                f"{browser_error or 'Complete the action in Envoy’s isolated browser and retry.'}",
                action_required=True,
            )
        if not request.scope.include_visual and len(text.strip()) < _FEED_ALTERNATE_PAGE_CHARS:
            feed_page = _read_feed_alternate(request, source_url, final_url, html)
            if feed_page is not None:
                return feed_page

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
            if _looks_like_access_challenge(title, text) or _looks_like_machine_error_payload(text):
                return _blocked(
                    request,
                    _blocked_challenge_code(browser_error),
                    "The page still requires login or security verification. "
                    f"{browser_error or 'Complete the action in Envoy’s isolated browser and retry.'}",
                    action_required=True,
                )
            if isinstance(browser_error, Exception):
                failure_code = _browser_read_failure_code(browser_error)
                return _blocked(
                    request,
                    failure_code,
                    str(browser_error),
                    action_required=failure_code in {
                        ErrorCode.CHALLENGE_REQUIRED,
                        ErrorCode.AUTH_REQUIRED,
                    },
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
        short_video_host = (urlparse(final_url).hostname or "").lower()
        if (
            platform_retry_used is False
            and short_video_host in _SHORT_VIDEO_RETRY_HOSTS
            and not request.scope.include_visual
            and len(text.strip()) < _SHORT_VIDEO_RETRY_CHARS
            and not parser.images
        ):
            platform_retry_used = True
            retry_page, retry_error = _read_with_isolated_browser(
                request,
                final_url,
                dns_cache,
            )
            if retry_page is not None:
                browser_used = True
                final_url, html = retry_page.url, retry_page.html
                browser_snapshot = retry_page
                browser_snapshot_for_video = _browser_page_has_video(
                    retry_page,
                    request.source.kind.value,
                )
                parser = _PageParser()
                parser.feed(html)
                title = _clean_text(parser.title_parts) or final_url
                source_text_truncated = _source_text_was_truncated(parser.text_parts)
                text = _clean_text(parser.text_parts)
            elif retry_error is not None and browser_error is None:
                browser_error = retry_error
        safe_source_url = _redact_url(final_url)
        image_note_page = (
            urlparse(final_url).hostname in {"douyin.com", "www.douyin.com"}
            and bool(re.match(
                r"^/note/[^/]+",
                urlparse(final_url).path,
                re.IGNORECASE,
            ))
        ) or classify_public_url(final_url) in {
            "xiaohongshu_note", "xiaohongshu_share",
        }
        image_note_total: int | None = None
        unbound_note_video = False
        if image_note_page:
            if classify_public_url(final_url) in {
                "xiaohongshu_note", "xiaohongshu_share",
            }:
                parser.images, image_note_total = extract_xhs_note_images(
                    html,
                    final_url,
                )
                unbound_note_video = bool(
                    parser.video_urls
                    or (browser_snapshot and browser_snapshot.page_video_urls)
                )
                # XHS recommendation media is not evidence for the requested note.
                parser.video_urls = []
                browser_snapshot_for_video = False
            else:
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
        raw_image_candidates = [
            {
                **image,
                "score": _goal_score(f"{image.get('alt', '')} {image.get('url', '')}", goal_terms),
                "source_index": index,
            }
            for index, image in enumerate(parser.images)
        ]
        ui_images_excluded = sum(
            _is_probable_ui_image(item) and int(item["score"]) == 0
            for item in raw_image_candidates
        )
        image_candidates = [
            item for item in raw_image_candidates
            if not _is_probable_ui_image(item) or int(item["score"]) > 0
        ]
        if goal_terms and not image_note_page:
            image_candidates.sort(key=lambda item: (
                -int(item["score"]),
                _is_probable_ui_image(item),
                int(item["source_index"]),
            ))
        elif not image_note_page:
            image_candidates.sort(key=lambda item: (
                _is_probable_ui_image(item),
                int(item["source_index"]),
            ))
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
            _media_origin(urljoin(final_url, item))
            for item in parser.video_urls
            if _is_public_http_url(urljoin(final_url, item), dns_cache)
        ]
        video_urls = list(dict.fromkeys(url for url in video_urls if url))
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
                ["Page video detected but not attributed to this note; video-level evidence was not extracted"]
                if unbound_note_video else []
            ),
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
        ocr_blocks = []
        ocr_unresolved = []
        zero_confidence_regions = 0
        ocr_profile = request.ocr_profile
        ocr_language = _requested_ocr_language(request)
        ocr_backend = _requested_ocr_backend(request)
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
        if ocr_profile:
            for item in safe_images:
                try:
                    image_path, image_sha256 = _download_image_for_ocr(
                        item["image_url"],
                        max_bytes=request.budget.max_download_bytes,
                        dns_cache=dns_cache,
                    )
                    ocr_result = _read_cached_image_ocr(
                        image_path,
                        image_sha256,
                        profile=ocr_profile,
                        language=ocr_language,
                        backend=ocr_backend,
                    )
                    ocr_language = str(ocr_result.get("language") or ocr_language)
                    zero_confidence_regions += _ocr_zero_confidence_regions(
                        ocr_result.get("regions")
                    )
                    for region_index, region in enumerate(ocr_result.get("regions") or [], start=1):
                        ocr_text = str(region.get("text") or "").strip()
                        bbox = region.get("bbox")
                        if not ocr_text or not isinstance(bbox, list) or len(bbox) != 4:
                            continue
                        ocr_blocks.append(MaterialBlock(
                            id=f"{item['block_id']}-ocr-{region_index:03d}",
                            kind=MaterialKind.OCR,
                            content=TextContent(type="text", text=ocr_text),
                            origin=MaterialOrigin.OCR,
                            locator=ImageRegionLocator(
                                type="image_region",
                                resource_id=item["resource_id"],
                                coordinate_system="normalized_top_left",
                                bbox=[float(value) for value in bbox],
                            ),
                            parent_id=item["block_id"],
                            order=len(blocks) + len(ocr_blocks),
                            relations=[Relation(
                                type="derived_from",
                                target_id=item["block_id"],
                                basis="extraction",
                            )],
                            quality=Quality(
                                engine=f"{ocr_result.get('backend', 'ocr')}-{ocr_profile}",
                                engine_score=float(region.get("confidence") or 0),
                                unresolved=[
                                    "OCR is a text extraction hint; inspect the source image for layout or ambiguity"
                                ],
                            ),
                        ))
                    if not any(
                        block.parent_id == item["block_id"]
                        and block.kind == MaterialKind.OCR
                        for block in ocr_blocks
                    ):
                        ocr_unresolved.append(
                            f"OCR returned no valid text regions for {item['resource_id']}"
                        )
                except (OSError, ValueError, PublicPageError, MediaBudgetExceeded, RuntimeError) as exc:
                    ocr_unresolved.append(
                        f"OCR skipped for {item['resource_id']} ({type(exc).__name__})"
                    )
            if zero_confidence_regions:
                ocr_unresolved.append(
                    f"{zero_confidence_regions} OCR regions have zero confidence and need visual review"
                )
            blocks.extend(ocr_blocks)
        ocr_visual_resources = [
            item for item in safe_images
            if any(resource.resource_id == item["resource_id"] for resource in resources)
        ]
        ocr_visual_need = (
            [Need(
                action_id="inspect-image-ocr-fallback",
                type="inspect_visual",
                purpose=request.goal or "Inspect the source images because local OCR is incomplete",
                resource_ids=[item["resource_id"] for item in ocr_visual_resources],
                source_block_ids=[item["block_id"] for item in ocr_visual_resources],
                reason="Local OCR returned no readable regions or unresolved low-confidence regions; inspect the delivered source images directly.",
                max_resources=len(ocr_visual_resources),
            )]
            if (
                ocr_profile
                and ocr_unresolved
                and ocr_visual_resources
                and request.host_capabilities.image_input == ImageInputCapability.URI
            )
            else []
        )
        if _looks_like_login_wall(title, text):
            return _blocked(
                request,
                ErrorCode.AUTH_REQUIRED,
                "The source returned a sign-in wall instead of its content",
                action_required=True,
                details={"reader": "isolated_chromium" if browser_snapshot else "static_http"},
            )
        image_note_unknown_total = image_note_page and image_note_total is None
        thin_platform_page = (
            len(text.strip()) < _THIN_PLATFORM_PAGE_CHARS
            and is_platform_page(str(request.source.url or ""))
        )
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
            result=ReadResult.PARTIAL if text_is_partial or source_text_truncated or image_partial or image_only or video_urls or browser_snapshot_for_video or unbound_note_video or ocr_unresolved or thin_platform_page else ReadResult.READY,
            blocks=blocks,
            resources=resources,
            needs=ocr_visual_need,
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
                                *(
                                    [
                                        "The platform page returned almost no text; it is rendered by scripts and needs cookies or a host browser"
                                    ]
                                    if thin_platform_page
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
                            **({"thin_platform_page": True} if thin_platform_page else {}),
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
                                if len(selected_images) < len(image_candidates)
                                else []
                            ),
                            *(
                                [
                                    f"{ui_images_excluded} obvious interface decoration images were excluded"
                                ]
                                if ui_images_excluded
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
                            **(
                                {"ui_images_excluded": ui_images_excluded}
                                if ui_images_excluded
                                else {}
                            ),
                        },
                    ),
                    **({"ocr": CoverageItem(
                        discovered=len(safe_images),
                        extracted=len(ocr_blocks),
                        returned=len(ocr_blocks),
                        extraction="complete" if not ocr_unresolved else "partial",
                        unresolved=ocr_unresolved,
                        details={
                            "profile": ocr_profile,
                            "language": ocr_language,
                            "source_image_count": len(safe_images),
                            "region_count": len(ocr_blocks),
                            "zero_confidence_regions": zero_confidence_regions,
                            "host_visual_fallback": True,
                        },
                    )} if ocr_profile else {}),
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
        return _blocked(request, exc.code, str(exc), details=exc.details)
    except httpx.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 404:
            final_url = _redact_url(str(exc.response.url))
            return _blocked(
                request,
                ErrorCode.SOURCE_UNAVAILABLE,
                "The source returned HTTP 404. This may be a removed or private item, an invalid share link, or an upstream proxy response; Envoy cannot distinguish these from the response alone.",
                details={
                    "http_status": 404,
                    "final_url": final_url,
                    "diagnosis": "source_or_route_not_found",
                    "user_check_recommended": True,
                    "proxy_configured": _http_proxy_configured(),
                    "suggested_next_action": "Ask the user to open the same link in their regular browser. If it opens there, ask whether to use an already configured or user-approved trusted proxy.",
                },
            )
        return _blocked(
            request,
            ErrorCode.NETWORK_ERROR,
            f"Public page request failed ({type(exc).__name__}) for {_redact_url(source_url)}",
            details={
                "diagnosis": "transport_or_network_error",
                "proxy_configured": _http_proxy_configured(),
                "user_check_recommended": True,
            },
        )


def _persisted_auth_action(source_url: str, purpose: str) -> dict:
    """Remember the pause so a later run can find it again."""

    try:
        from . import authflow

        return authflow.create_auth_action(
            str(source_url or ""),
            purpose=purpose,
            reason=f"{urlparse(str(source_url or '')).hostname or ''} needs a signed-in session",
        )
    except (OSError, ValueError):
        return {}


def _login_user_action(
    source_url: str,
    kind: str = "login_or_verification",
    *,
    purpose: str = "read_source",
) -> dict:
    """The choices to hand a human when a page needs their identity.

    Envoy never opens a foreground window on its own: a model that copied a
    runnable command from here would only hit the non-interactive refusal.
    Each option is therefore a human action, with the command kept out of any
    string a model could paste and execute unattended.
    """

    host = urlparse(str(source_url or "")).hostname or ""
    question = (
        "This page needs a signed-in session or a verification step before "
        "its player exposes the real source. Ask the user to import cookies "
        "from a local browser, or to sign in once from a terminal."
    )
    if kind == "cookies_required":
        question = (
            f"{host or 'This site'} asked for fresh cookies before serving this "
            "source, and a signed-in account is not necessarily what it wants. "
            "Ask the user to copy the cookies of that browser for this site, or "
            "to open one visible window from their terminal so the site sets "
            "them itself."
        )

    from . import features

    handoff_enabled = features.credential_handoff_enabled()
    action = _persisted_auth_action(source_url, purpose) if handoff_enabled else {}
    payload = {
        "kind": kind,
        "host": host,
        "question": question,
        "requires_human": True,
        "credential_handoff": {
            "enabled": handoff_enabled,
            "scope": "this machine only",
        },
        "options": [
            {
                "action": "in_chat_sign_in",
                "requires_human": True,
                "instruction": (
                    "Call envoy_auth with action open and this source_url; it "
                    "returns a picture of the site's sign-in page (usually the "
                    "QR code) to show the user. They scan once, the browser "
                    "stays in the background, and this link reads normally once "
                    "envoy_auth status reports signed_in."
                ),
                "effect": (
                    "The user signs in once from the chat, without a terminal, "
                    "and Envoy's isolated profile keeps the session"
                ),
            },
            {
                "action": "import_cookies",
                "requires_human": True,
                "instruction": (
                    "In a terminal, run Envoy's cookie import for this site "
                    "and pick the browser that holds the session; the cookies "
                    "then live in Envoy's isolated profile."
                ),
                "sources": ["chrome", "edge", "safari"],
                "effect": (
                    "That browser's cookies for this site are copied into "
                    "Envoy's isolated profile"
                ),
            },
            {
                "action": "human_sign_in",
                "requires_human": True,
                "instruction": (
                    "From a terminal, read this page once with visible browser "
                    "mode enabled and complete the normal sign-in there; the "
                    "session is kept in ~/.envoy/browser-profile and reused by "
                    "later headless reads."
                ),
                "effect": (
                    "One sign-in at the user's terminal keeps the local browser "
                    "profile usable for later headless reads"
                ),
            },
        ],
    }
    if not handoff_enabled:
        # The switch is off (the shipped default). Say so once, in a form the
        # model can relay but cannot act on itself: enabling is the user's step.
        payload["credential_handoff"]["how_to_enable"] = (
            "Credential handoff is off. The user can enable it once with "
            "`envoy auth handoff on` (or ENVOY_CREDENTIAL_HANDOFF=1 for one "
            "shell); only then can Envoy open a sign-in page in the chat or "
            "reuse a browser session."
        )
        payload["options"] = [
            option for option in payload["options"] if option.get("action") == "human_sign_in"
        ]
    if action.get("action_id"):
        action_id = str(action["action_id"])
        payload["action_id"] = action_id
        payload["resume_token"] = action.get("resume_token")
        payload["status"] = action.get("status")
        payload["open_command"] = f"envoy auth open --action-id {action_id}"
        payload["verify_command"] = f"envoy auth verify --action-id {action_id}"
    return payload


def _transcript_confidence(
    segments: list[dict],
    duration_seconds: float | None,
    *,
    processed_from: float | None = None,
    processed_until: float | None = None,
) -> tuple[str, float, float]:
    """Judge whether a transcript covers enough of what was transcribed.

    Whisper will emit a stray line for a music track, and presenting that as the
    transcript of an official music video is worse than saying the audio is
    music. Coverage is the only signal available consistently across models:
    real speech fills a usable share of a clip, a hallucinated line does not.

    The denominator is the audio span the ASR pass actually reached
    (``processed_from``..``processed_until``), not the whole file: a partial
    pass that stopped after 40s of a 1-hour video must not look like a 1%
    coverage failure. Without a processed span the full duration is used, so
    callers that only know the clip length keep the old behaviour.
    """

    speech_seconds = 0.0
    for segment in segments or []:
        try:
            start = float(segment.get("start", segment.get("start_seconds")))
            end = float(segment.get("end", segment.get("end_seconds")))
        except (TypeError, ValueError):
            continue
        if end > start:
            speech_seconds += end - start
    try:
        duration = float(duration_seconds or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        span_start = float(processed_from or 0.0)
    except (TypeError, ValueError):
        span_start = 0.0
    try:
        span_end = float(processed_until) if processed_until is not None else duration
    except (TypeError, ValueError):
        span_end = duration
    span = span_end - span_start
    if span <= 0:
        # No usable processed span (unknown range or a single point): fall back
        # to the whole clip rather than dividing by zero.
        span = duration
    if span <= 30:
        return "normal", speech_seconds, speech_seconds / span if span else 0.0
    ratio = speech_seconds / span
    if ratio < 0.02 or (len(segments) <= 2 and speech_seconds < 8):
        return "low", speech_seconds, ratio
    return "normal", speech_seconds, ratio


def _no_speech_partial(
    request: ReadRequest,
    source_url: str,
    message: str,
    *,
    title: str = "",
    duration: float | None = None,
) -> ReadResponse:
    """A reached video whose audio holds no speech is a gap, not a failure.

    The download and the speech pass both succeeded; refusing the whole read
    would hide that the source was reached and hand the model an empty answer
    for a music clip or a silent post. Return what is known, name the gap, and
    let the caller decide whether frames or another source are worth asking for.
    """

    host = urlparse(str(source_url or "")).hostname or ""
    heading = (title or request.source.label or "").strip() or "Video source"
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    details: dict = {"page_player_bound": True, "host": host}
    unresolved = [message]
    if duration_seconds:
        details["duration_seconds"] = duration_seconds
        unresolved.append(
            f"The audio track of this {duration_seconds:.0f}s video holds no detectable speech"
        )
    else:
        unresolved.append("The audio track holds no detectable speech")
    unresolved.append("Frames or a different source would be needed to cover the visual content")
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.PARTIAL,
        blocks=[MaterialBlock(
            id="blk-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=heading),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=str(source_url),
                block_id="blk-title",
                quote=heading[:MAX_LOCATOR_QUOTE_CHARS],
            ),
            order=0,
        )],
        coverage=Coverage(modalities={
            "video": CoverageItem(
                discovered=1,
                extracted=1,
                returned=1,
                extraction="partial",
                unresolved=unresolved,
                details={**details, "no_speech": True},
            )
        }),
    )


def _blocked(
    request: ReadRequest,
    code: ErrorCode,
    message: str,
    action_required: bool = False,
    details: dict | None = None,
) -> ReadResponse:
    payload = dict(details or {})
    needs: list[Need] = []
    if action_required and code in {ErrorCode.CHALLENGE_REQUIRED, ErrorCode.AUTH_REQUIRED}:
        action = _login_user_action(str(request.source.url))
        payload.setdefault("needs_user_action", action)
        if action.get("action_id"):
            needs.append(
                Need(
                    action_id=str(action["action_id"]),
                    type="authenticate",
                    purpose="read_source",
                    reason=message,
                    resume_token=action.get("resume_token"),
                )
            )
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[
            ReadError(
                code=code,
                message=message,
                retryable=code in {
                    ErrorCode.NETWORK_ERROR,
                    ErrorCode.CHALLENGE_REQUIRED,
                    ErrorCode.AUTH_REQUIRED,
                },
                action_required=action_required,
                details=payload,
            )
        ],
        needs=needs,
    )
