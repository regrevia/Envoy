from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ErrorCode,
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

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attrs_map = dict(attrs)
        if tag in self._ignored_tags:
            self._ignored_depth += 1
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


def _clean_text(values: list[str]) -> str:
    text = "\n".join(values)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def _fetch_html(source_url: str) -> tuple[str, str]:
    current_url = source_url
    headers = {"User-Agent": "Envoy/0.1 (+https://github.com/regrevia/Envoy)"}
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url):
                raise PublicPageError("The source URL or redirect is not a public HTTP(S) address")
            response = client.get(current_url, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise PublicPageError("The source returned an invalid redirect")
                current_url = urljoin(current_url, location)
                continue
            if response.status_code in {401, 403}:
                raise PublicPageError("The source requires access that Envoy cannot provide", ErrorCode.AUTH_REQUIRED)
            response.raise_for_status()
            if len(response.content) > MAX_HTML_BYTES:
                raise PublicPageError("The public page exceeds Envoy's size limit")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                raise PublicPageError(
                    f"Static reader only accepts HTML pages, received {content_type}",
                    ErrorCode.UNSUPPORTED_SOURCE,
                )
            return current_url, response.text
    raise PublicPageError("The source exceeded the redirect limit")


def read_public_page(request: ReadRequest) -> ReadResponse:
    source_url = request.source.url
    if request.source.kind.value not in {"auto", "article"}:
        return _blocked(request, ErrorCode.UNSUPPORTED_SOURCE, "The independent Skill currently reads public HTML pages")
    if not _is_public_http_url(source_url):
        return _blocked(request, ErrorCode.SOURCE_UNAVAILABLE, "Only public HTTP(S) URLs are allowed")
    try:
        final_url, html = _fetch_html(source_url)
        parser = _PageParser()
        parser.feed(html)
        title = _clean_text(parser.title_parts) or final_url
        text = _clean_text(parser.text_parts)
        if len(text) < 20:
            return _blocked(request, ErrorCode.EMPTY_CONTENT, "No readable public HTML text was found")
        blocks = [
            MaterialBlock(
                id="blk-title",
                kind=MaterialKind.HEADING,
                content=TextContent(type="text", text=title),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=WebLocator(type="web", source_url=final_url, block_id="blk-title", quote=title),
                order=0,
            ),
            MaterialBlock(
                id="blk-body-0001",
                kind=MaterialKind.PARAGRAPH,
                content=TextContent(type="text", text=text),
                origin=MaterialOrigin.SOURCE_TEXT,
                locator=WebLocator(type="web", source_url=final_url, block_id="blk-body-0001", quote=text[:500]),
                order=1,
            ),
        ]
        resources = []
        seen_images = set()
        for index, image in enumerate(parser.images, start=1):
            image_url = urljoin(final_url, image["url"])
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
                    locator=WebLocator(type="web", source_url=final_url, block_id=block_id, quote=image["alt"] or image_url),
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
            if len(resources) >= MAX_IMAGES:
                break
        return ReadResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            material_id=f"mat-{hashlib.sha256((final_url + text).encode()).hexdigest()[:24]}",
            source_revision=hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest(),
            execution_state=ExecutionState.COMPLETED,
            result=ReadResult.READY,
            blocks=blocks,
            resources=resources,
            coverage=Coverage(
                modalities={
                    "text": CoverageItem(discovered=1, extracted=1, returned=1, extraction="complete"),
                    "images": CoverageItem(
                        discovered=len(parser.images),
                        extracted=len(resources),
                        returned=len(resources),
                        delivered=len(resources),
                        extraction="complete" if len(resources) == len(parser.images) else "partial",
                    ),
                }
            ),
        )
    except PublicPageError as exc:
        return _blocked(request, exc.code, str(exc))
    except httpx.HTTPError as exc:
        return _blocked(request, ErrorCode.NETWORK_ERROR, f"Public page request failed: {exc}")


def _blocked(request: ReadRequest, code: ErrorCode, message: str) -> ReadResponse:
    return ReadResponse(
        request_id=request.request_id,
        session_id=request.session_id,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.BLOCKED,
        errors=[ReadError(code=code, message=message, retryable=code == ErrorCode.NETWORK_ERROR)],
    )
