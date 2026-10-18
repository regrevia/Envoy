from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from pypdf import PdfReader
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
    PdfLocator,
    TextContent,
    WebLocator,
)


MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_TEXT_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_TEXT_CHARS = 2_000_000
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
    return sum(haystack.count(term) for term in terms)


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
    low = offset
    while low < high:
        middle = (low + high + 1) // 2
        if _estimated_tokens(text[offset:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    content, end = _take_text_range(text, offset, low - offset)
    while content and _estimated_tokens(content) > max_tokens:
        content = content[:-1]
        end -= 1
    return content, end


def _response_char_budget(request) -> int:
    return int(request.budget.max_response_chars)


def _source_text_was_truncated(values: list[str]) -> bool:
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(values)).strip()
    return len(text) > MAX_TEXT_CHARS


def _looks_like_access_challenge(title: str, text: str) -> bool:
    haystack = f"{title}\n{text}".lower()
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


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
    return root


def _store_material(material_id: str, data: dict) -> None:
    root = _material_root()
    target = root / f"{material_id}.json"
    expiry_target = root / f"{material_id}.expires"
    data = {
        **data,
        "expires_at": float(data.get("expires_at") or time.time() + MATERIAL_TTL_SECONDS),
    }
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


def _expand_blocked(request: ExpandRequest, message: str) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        material_id=request.material_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[ReadError(code=ErrorCode.BUDGET_EXCEEDED, message=message)],
    )


def _material_budget_state(stored: dict, request: ExpandRequest) -> dict:
    state = stored.get("budget_state")
    if not isinstance(state, dict):
        state = {}
    ranges = []
    for item in state.get("returned_ranges") or []:
        if isinstance(item, list) and len(item) == 2:
            try:
                start, end = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if 0 <= start < end:
                ranges.append([start, end])
    if state.get("returned_tokens") is not None:
        returned_tokens = int(state["returned_tokens"])
    else:
        source_text = str(stored.get("text") or "")
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


def _record_material_range(stored: dict, state: dict, start: int, end: int, content: str) -> None:
    state["returned_ranges"].append([start, end])
    state["returned_chars"] += end - start
    state["returned_tokens"] += _estimated_tokens(content)
    state["expansion_rounds"] += 1
    stored["budget_state"] = state


def record_visual_observation(
    material_id: str | None,
    source_url: str,
    resource_id: str,
    source_block_id: str,
    observation: str,
    locator_quote: str | None = None,
) -> dict:
    """Persist a bounded host observation without exposing raw image bytes."""
    if not material_id:
        return {"persisted": False}
    root = _material_root()
    source_path = root / f"{material_id}.json"
    expiry_path = root / f"{material_id}.expires"
    if source_path.is_symlink() or expiry_path.is_symlink():
        raise PublicPageError("Material is unavailable", ErrorCode.EXPIRED_RESOURCE)
    try:
        stored = json.loads(source_path.read_text(encoding="utf-8"))
        expires_at = float(expiry_path.read_text(encoding="ascii").strip())
    except FileNotFoundError as exc:
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE) from exc
    if expires_at <= time.time():
        raise PublicPageError("Material is missing or expired", ErrorCode.EXPIRED_RESOURCE)
    observations = stored.get("visual_observations")
    if not isinstance(observations, list):
        observations = []
    record = {
        "source_url": _redact_url(source_url),
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
    _store_material(material_id, stored)
    return {"persisted": True, "observation_count": len(stored["visual_observations"])}


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
    budget_state = _material_budget_state(stored, request)
    if (
        budget_state["max_expansion_rounds"] is not None
        and budget_state["expansion_rounds"] >= budget_state["max_expansion_rounds"]
    ):
        return _expand_blocked(request, "The material expansion round budget has been exhausted")
    if budget_state["returned_chars"] >= budget_state["max_total_response_chars"]:
        return _expand_blocked(request, "The cumulative response character budget has been exhausted")
    if (
        budget_state["max_total_response_tokens"] is not None
        and budget_state["returned_tokens"] >= budget_state["max_total_response_tokens"]
    ):
        return _expand_blocked(request, "The cumulative response token budget has been exhausted")
    total_text = str(stored.get("text") or "")
    offset = request.offset
    max_chars = min(
        request.max_chars,
        int(request.max_response_tokens) * 4
        if request.max_response_tokens is not None
        else request.max_chars,
    )
    max_total_chars = request.max_total_response_chars
    if offset > len(total_text):
        raise ValueError("offset must be between zero and the material length")
    end = min(
        len(total_text),
        offset + max_chars,
        offset + max(0, budget_state["max_total_response_chars"] - budget_state["returned_chars"]),
        max_total_chars,
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
    if any(
        (start <= offset < finish and offset < finish - 8)
        or offset < start < end
        for start, finish in budget_state["returned_ranges"]
    ):
        return _expand_blocked(request, "The requested material range overlaps an earlier response")
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
            return _expand_blocked(request, "The material budget ledger could not be persisted")
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
            result=ReadResult.PARTIAL if has_more else ReadResult.READY,
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
            }),
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
        return _expand_blocked(request, "The material budget ledger could not be persisted")
    has_more = end < len(total_text)
    block_id = "blk-body-0001"
    block = MaterialBlock(
        id=f"{block_id}-range-{offset}",
        kind=MaterialKind.PARAGRAPH,
        content=TextContent(type="text", text=content),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(
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
        result=ReadResult.PARTIAL if has_more else ReadResult.READY,
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
        ),
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

    available_chars = max(0, _response_char_budget(request) - len(title_block.model_dump_json()))
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
    material_id = f"mat-{hashlib.sha256((source['url'] + source_revision).encode()).hexdigest()[:24]}"
    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_budget_exhausted and not retention_disabled
    continuation_storage_failed = False
    if can_continue:
        try:
            _store_material(material_id, {
                "source_kind": "pdf",
                "source_url": source["url"],
                "source_revision": source_revision,
                "session_id": request.session_id,
                "text": full_text,
                "title_text": title,
                "page_spans": page_spans,
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

    unresolved = []
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
        }),
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
    available_chars = max(0, _response_char_budget(request) - len(title_block.model_dump_json()) - 200)
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
    material_id = f"mat-{hashlib.sha256((source['url'] + source_revision).encode()).hexdigest()[:24]}"
    retention_disabled = request.retention.derived_ttl_seconds <= 0
    can_continue = response_partial and not source_truncated and not retention_disabled
    continuation_storage_failed = False
    if can_continue:
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
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        material_id=f"mat-{hashlib.sha256(source['url'].encode()).hexdigest()[:24]}",
        source_revision=hashlib.sha256(source["url"].encode()).hexdigest(),
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


def read_public_page(request: ReadRequest) -> ReadResponse:
    source_url = request.source.url
    dns_cache: dict[str, bool] = {}
    if not _is_public_http_url(source_url, dns_cache):
        return _blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "Only public HTTP(S) URLs are allowed")
    source_path = urlparse(source_url).path.lower()
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
    if request.source.kind.value not in {"auto", "article"}:
        return _blocked(request, ErrorCode.UNSUPPORTED_SOURCE, "The independent Skill currently reads public HTML pages")
    try:
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
            return _blocked(
                request,
                ErrorCode.MISSING_CAPABILITY,
                f"Standalone media extraction is unavailable for {detected_media.source['mime_type']}",
            )
        parser = _PageParser()
        parser.feed(html)
        title = _clean_text(parser.title_parts) or final_url
        source_text_truncated = _source_text_was_truncated(parser.text_parts)
        text = _clean_text(parser.text_parts)
        if _looks_like_access_challenge(title, text):
            return _blocked(
                request,
                ErrorCode.CHALLENGE_REQUIRED,
                "The public page appears to be a login, CAPTCHA, or security verification page",
                action_required=True,
            )
        if len(text) < 20 and not parser.images and not parser.video_urls:
            return _blocked(request, ErrorCode.EMPTY_CONTENT, "No readable public HTML text was found")
        safe_source_url = _redact_url(final_url)
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
        if goal_terms:
            image_candidates.sort(key=lambda item: (-int(item["score"]), int(item["source_index"])))
        selected_images = image_candidates[:visual_budget]
        available_chars = max(
            0,
            _response_char_budget(request)
            - len(title_block.model_dump_json())
            - sum(
                len(image.get("alt", ""))
                + len(urljoin(final_url, image["url"]))
                + 256
                for image in selected_images
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
        material_id = f"mat-{hashlib.sha256((final_url + source_revision).encode()).hexdigest()[:24]}"
        retention_disabled = request.retention.derived_ttl_seconds <= 0
        can_continue = text_is_partial and not source_text_truncated and not retention_disabled
        continuation_storage_failed = False
        if can_continue:
            try:
                _store_material(material_id, {
                    "source_url": safe_source_url,
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
        blocks = [
            title_block,
            MaterialBlock(
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
            ),
        ]
        resources = []
        seen_images = set()
        skipped_sensitive_images = 0
        for index, image in enumerate(selected_images, start=1):
            image_url = urljoin(final_url, image["url"])
            if any(_is_sensitive_query_key(key) for key, _value in parse_qsl(urlparse(image_url).query, keep_blank_values=True)):
                skipped_sensitive_images += 1
                continue
            if image_url in seen_images or not _is_public_http_url(image_url, dns_cache):
                continue
            seen_images.add(image_url)
            block_id = f"blk-image-{index:04d}"
            resource_id = f"resource-{hashlib.sha256(image_url.encode()).hexdigest()[:16]}"
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
        image_partial = len(resources) < len(parser.images)
        return ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            material_id=material_id,
            source_revision=source_revision,
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.PARTIAL if text_is_partial or source_text_truncated or image_partial or image_only or video_urls else ReadResult.READY,
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
                                if image_partial
                                and not skipped_sensitive_images
                                and len(selected_images) == len(parser.images)
                                else []
                            ),
                        ],
                        details=(
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
                            if goal_terms
                            else {}
                        ),
                    ),
                    **({"video": CoverageItem(
                        discovered=len(video_urls),
                        extracted=0,
                        returned=0,
                        extraction="failed" if video_urls else "complete",
                        unresolved=(
                            ["Embedded video was discovered but standalone video extraction is unavailable"]
                            if video_urls
                            else []
                        ),
                        details={"urls": video_urls},
                    )} if video_urls else {}),
                }
            ),
        )
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
