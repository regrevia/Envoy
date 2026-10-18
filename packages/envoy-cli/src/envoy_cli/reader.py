from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ErrorCode,
    ExpandRequest,
    ExecutionState,
    HostDelivery,
    ImageContent,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadError,
    ReadRequest,
    ReadResponse,
    ReadResult,
    ResourceRef,
    TextContent,
    WebLocator,
)


MAX_HTML_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_IMAGES = 40
MAX_FETCH_ATTEMPTS = 3
MIN_EXPANSION_CHARS = 128
MATERIAL_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHED_MATERIALS = 100
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


class _PageParser(HTMLParser):
    _ignored_tags = {"script", "style", "noscript", "template", "svg", "nav", "footer", "aside", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.images: list[dict[str, str]] = []
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


def _is_public_http_url(value: str) -> bool:
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
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                )
            }
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


def _source_text_was_truncated(values: list[str]) -> bool:
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(values)).strip()
    return len(text) > MAX_TEXT_CHARS


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
    total_text = str(stored.get("text") or "")
    offset = request.offset
    max_chars = request.max_chars
    max_total_chars = request.max_total_response_chars
    if offset > len(total_text):
        raise ValueError("offset must be between zero and the material length")
    end = min(len(total_text), offset + max_chars, max_total_chars)
    if end <= offset:
        raise PublicPageError(
            "The cumulative response character budget has been reached",
            ErrorCode.BUDGET_EXCEEDED,
        )
    content, end = _take_text_range(total_text, offset, end - offset)
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
            quote=content[:500],
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
                    },
                )
            }
        ),
    )


def _fetch_html(source_url: str, max_bytes: int = MAX_HTML_BYTES) -> tuple[str, str]:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url):
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


def read_public_page(request: ReadRequest) -> ReadResponse:
    source_url = request.source.url
    if request.source.kind.value not in {"auto", "article"}:
        return _blocked(request, ErrorCode.UNSUPPORTED_SOURCE, "The independent Skill currently reads public HTML pages")
    if not _is_public_http_url(source_url):
        return _blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "Only public HTTP(S) URLs are allowed")
    try:
        final_url, html = _fetch_html(
            source_url,
            min(MAX_HTML_BYTES, request.budget.max_download_bytes or MAX_HTML_BYTES),
        )
        parser = _PageParser()
        parser.feed(html)
        title = _clean_text(parser.title_parts) or final_url
        source_text_truncated = _source_text_was_truncated(parser.text_parts)
        text = _clean_text(parser.text_parts)
        if len(text) < 20:
            return _blocked(request, ErrorCode.EMPTY_CONTENT, "No readable public HTML text was found")
        safe_source_url = _redact_url(final_url)
        title_block = MaterialBlock(
            id="blk-title",
            kind=MaterialKind.HEADING,
            content=TextContent(type="text", text=title),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(type="web", source_url=safe_source_url, block_id="blk-title", quote=title),
            order=0,
        )
        visual_budget = max(0, request.budget.max_visual_inputs)
        selected_images = parser.images[:visual_budget]
        available_chars = max(
            0,
            request.budget.max_response_chars
            - len(title_block.model_dump_json())
            - sum(
                len(image.get("alt", ""))
                + len(urljoin(final_url, image["url"]))
                + 256
                for image in selected_images
            ),
        )
        returned_text, returned_end = _take_text_range(text, 0, available_chars)
        text_is_partial = returned_end < len(text)
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
                locator=WebLocator(type="web", source_url=safe_source_url, block_id="blk-body-0001", quote=returned_text[:500]),
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
            if image_url in seen_images or not _is_public_http_url(image_url):
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
            result=ReadResult.PARTIAL if text_is_partial or source_text_truncated or image_partial else ReadResult.READY,
            blocks=blocks,
            resources=resources,
            coverage=Coverage(
                modalities={
                    "text": CoverageItem(
                        discovered=1,
                        extracted=1,
                        returned=1,
                        extraction="partial" if text_is_partial or source_text_truncated else "complete",
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
                            "source_truncated": source_text_truncated,
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
                    ),
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


def _blocked(request: ReadRequest, code: ErrorCode, message: str) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[ReadError(code=code, message=message, retryable=code == ErrorCode.NETWORK_ERROR)],
    )
