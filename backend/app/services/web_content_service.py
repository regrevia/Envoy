from __future__ import annotations

import ipaddress
import hashlib
import mimetypes
import os
import re
import shutil
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx
from envoy_core.schemas.read_v1 import (
    Coverage,
    CoverageItem,
    ExecutionState,
    HostDelivery,
    ImageContent,
    MaterialBlock,
    MaterialKind,
    MaterialOrigin,
    ReadResponse,
    ReadResult,
    ResourceRef,
    TextContent,
    WebLocator,
)


MAX_PAGE_IMAGES = 40
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PAGE_TEXT_CHARS = 500_000
MAX_PAGE_VIDEO_URLS = 5


class PublicPageError(RuntimeError):
    pass


def public_page_material_response(
    page: dict,
    source_url: str,
    image_assets: list[dict],
    ocr_blocks: list[dict] | None = None,
    request_id: str = "web-page-read",
) -> ReadResponse:
    """Translate extracted page content into the shared, versioned material protocol."""
    blocks = []
    resources = []
    title = str(page.get("title") or "Web page").strip()
    source_revision_input = [source_url, title, str(page.get("text") or "")]
    blocks.append(MaterialBlock(
        id="blk-title",
        kind=MaterialKind.HEADING,
        content=TextContent(type="text", text=title),
        origin=MaterialOrigin.SOURCE_TEXT,
        locator=WebLocator(type="web", source_url=source_url, block_id="blk-title", quote=title),
        order=0,
    ))
    text = str(page.get("text") or "").strip()
    if text:
        blocks.append(MaterialBlock(
            id="blk-body-0001",
            kind=MaterialKind.PARAGRAPH,
            content=TextContent(type="text", text=text),
            origin=MaterialOrigin.SOURCE_TEXT,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id="blk-body-0001",
                quote=text[:500],
            ),
            order=1,
            relations=[{
                "type": "derived_from",
                "target_id": "blk-title",
                "basis": "layout",
            }],
        ))
    image_block_ids = {}
    for order, asset in enumerate(image_assets, start=2):
        asset_id = str(asset.get("id") or "")
        file_name = str(asset.get("file_name") or f"image-{order - 1:02d}")
        resource_id = f"resource-{asset_id}"
        block_id = f"blk-image-{order - 1:04d}"
        image_block_ids[asset_id] = block_id
        source_revision_input.extend([asset_id, str(asset.get("sha256") or "")])
        blocks.append(MaterialBlock(
            id=block_id,
            kind=MaterialKind.IMAGE,
            content=ImageContent(
                type="image",
                resource_id=resource_id,
                alt_text=str(asset.get("alt") or ""),
                mime_type=str(asset.get("mime_type") or "application/octet-stream"),
            ),
            origin=MaterialOrigin.SOURCE_IMAGE,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id=block_id,
                quote=str(asset.get("alt") or file_name),
            ),
            parent_id="blk-body-0001" if text else "blk-title",
            order=order,
            relations=[{
                "type": "adjacent_to",
                "target_id": "blk-body-0001" if text else "blk-title",
                "basis": "layout",
            }],
        ))
        resources.append(ResourceRef(
            resource_id=resource_id,
            mime_type=str(asset.get("mime_type") or "application/octet-stream"),
            delivery=HostDelivery(type="uri", uri=f"assets/images/{file_name}"),
            width_px=int(asset["width_px"]) if asset.get("width_px") else None,
            height_px=int(asset["height_px"]) if asset.get("height_px") else None,
            sha256=str(asset["sha256"]) if asset.get("sha256") else None,
            delivery_evidence="generated_only",
        ))

    for index, ocr in enumerate(ocr_blocks or []):
        asset_id = str(ocr.get("asset_id") or "")
        content = str(ocr.get("content") or "").strip()
        if not content:
            continue
        block_id = f"blk-ocr-{index + 1:04d}"
        image_block_id = image_block_ids.get(asset_id)
        blocks.append(MaterialBlock(
            id=block_id,
            kind=MaterialKind.OCR,
            content=TextContent(
                type="text",
                text=content,
                language=str(ocr.get("language") or "und"),
            ),
            origin=MaterialOrigin.OCR,
            locator=WebLocator(
                type="web",
                source_url=source_url,
                block_id=block_id,
                quote=content[:500],
            ),
            parent_id=image_block_id,
            order=len(blocks),
            relations=[{
                "type": "derived_from",
                "target_id": image_block_id,
                "basis": "extraction",
            }] if image_block_id else [],
            quality={
                "engine": str(ocr.get("engine") or "unknown"),
                "engine_score": ocr.get("confidence"),
                "unresolved": [],
            },
        ))

    source_revision = hashlib.sha256(
        "\0".join(source_revision_input).encode("utf-8")
    ).hexdigest()
    image_count = len(image_assets)
    ocr_count = sum(1 for block in blocks if block.origin == MaterialOrigin.OCR)
    return ReadResponse(
        request_id=request_id,
        material_id=f"mat-{source_revision[:24]}",
        source_revision=source_revision,
        execution_state=ExecutionState.COMPLETED,
        result=ReadResult.READY if text or image_count else ReadResult.EMPTY,
        blocks=blocks,
        resources=resources,
        coverage=Coverage(modalities={
            "text": CoverageItem(
                discovered=1 if text else 0,
                extracted=1 if text else 0,
                returned=1 if text else 0,
                extraction="complete" if text else "unknown",
            ),
            "images": CoverageItem(
                discovered=image_count,
                extracted=image_count,
                returned=image_count,
                processed=ocr_count,
                delivered=image_count,
                extraction="complete" if image_count == len(page.get("images") or []) else "partial",
            ),
        }),
    )


def _is_public_http_url(value: str, dns_cache: dict[str, bool] | None = None) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global
        except ValueError:
            if dns_cache is not None and host in dns_cache:
                return dns_cache[host]
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            }
            result = bool(addresses) and all(address.is_global for address in addresses)
            if dns_cache is not None:
                dns_cache[host] = result
            return result
    except (OSError, ValueError, TypeError):
        return False


def normalize_page_data(payload: dict, source_url: str) -> dict:
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
    canonical_url = str(payload.get("canonical_url") or source_url).strip()
    if not _is_public_http_url(canonical_url):
        canonical_url = source_url

    text = str(payload.get("text") or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_PAGE_TEXT_CHARS:
        raise PublicPageError(f"Page text exceeds the {MAX_PAGE_TEXT_CHARS} character limit")

    images = []
    seen = set()
    for item in payload.get("images") if isinstance(payload.get("images"), list) else []:
        image_url = str(item.get("url") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if not image_url or image_url in seen or not _is_public_http_url(image_url):
            continue
        seen.add(image_url)
        images.append({
            "url": image_url,
            "alt": str(item.get("alt") or "").strip() if isinstance(item, dict) else "",
        })
        if len(images) >= MAX_PAGE_IMAGES:
            break

    if not text and not images:
        raise PublicPageError("No readable article text or images were found on the public page")
    return {
        "title": title or "Web page",
        "source_url": canonical_url,
        "content_type": str(payload.get("content_type") or "article"),
        "author": str(payload.get("author") or "").strip(),
        "published_at": str(payload.get("published_at") or "").strip(),
        "text": text,
        "images": images,
        "video_urls": [
            url for url in dict.fromkeys(
                str(value).strip()
                for value in (payload.get("video_urls") or [])
                if str(value).strip() and _is_public_http_url(str(value).strip())
            )
        ][:MAX_PAGE_VIDEO_URLS],
    }


def image_extension(content_type: str) -> str:
    extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }
    extension = extensions.get(content_type.lower())
    if not extension:
        raise PublicPageError(f"Unsupported image format: {content_type or 'unknown'}")
    return extension


def _browser_executable() -> str | None:
    configured = os.getenv("ENVOY_BROWSER_EXECUTABLE", "").strip()
    candidates = [
        configured,
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((candidate for candidate in candidates if candidate and os.path.isfile(candidate)), None)


def _page_script() -> str:
    return r"""
    () => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 &&
          style.display !== "none" && style.visibility !== "hidden";
      };
      const ignored = (element) => {
        const token = `${element.id || ""} ${element.className || ""}`.toLowerCase();
        return /recommend|related|comment|sidebar|navigation|footer|header|login|modal|qrcode/.test(token);
      };
      const clean = (element) => {
        if (!element) return "";
        const clone = element.cloneNode(true);
        clone.querySelectorAll("nav,footer,aside,button,[role=dialog],[class*=recommend i],[class*=related i],[class*=comment i],[class*=login i]").forEach(node => node.remove());
        return (clone.innerText || clone.textContent || "").trim();
      };
      const path = location.pathname.toLowerCase();
      const isDouyinNote = location.hostname.endsWith("douyin.com") && path.startsWith("/note/");
      const isWeChatArticle = location.hostname === "mp.weixin.qq.com" && path.startsWith("/s/");
      const selectors = isWeChatArticle
        ? ["#js_content", "article", "main"]
        : isDouyinNote
          ? ["[data-e2e*=note-desc i]", "[data-e2e*=note-content i]", "[class*=note-desc i]", "[class*=note-content i]", "article", "main"]
          : ["article", "main", "[role=main]", "[itemprop=articleBody]", "[class*=article-content i]", "[class*=rich_media_content i]", "#js_content"];
      const candidates = [];
      for (const selector of selectors) {
        for (const node of document.querySelectorAll(selector)) {
          if (!visible(node) || ignored(node)) continue;
          const text = clean(node);
          if (text.length >= 60) candidates.push({node, text, priority: selector === "#js_content" ? 3 : selector === "article" ? 2 : 1});
        }
      }
      candidates.sort((a, b) => b.priority - a.priority || b.text.length - a.text.length);

      let title = document.querySelector('meta[property="og:title"]')?.content ||
        document.querySelector("h1")?.innerText || document.title || "";
      title = title.replace(/\s+-\s+抖音$/, "").trim();
      let root = candidates[0]?.node || null;
      let text = candidates[0]?.text || "";
      let author = "";
      if (isDouyinNote) {
        const bodyText = document.body?.innerText || "";
        let start = bodyText.indexOf(title);
        if (start < 0) start = bodyText.indexOf(title.split("#")[0].trim());
        if (start >= 0) {
          const end = bodyText.indexOf("发布时间", start);
          text = bodyText.slice(start, end >= 0 ? end : undefined);
        }
        const authorLink = [...document.querySelectorAll('a[href*="/user/"]')]
          .find(anchor => (anchor.innerText || "").trim().length > 0 &&
            !/\/user\/self\/?$/.test(anchor.href) &&
            !["我的", "关注", "朋友"].includes((anchor.innerText || "").trim()));
        author = (authorLink?.innerText || "").trim();
        text = text.split("\n").map(line => line.trim())
          .filter(line => line && line !== author && !["展开", "关注", "粉丝"].includes(line) && !/^粉丝\s*\d+/.test(line))
          .join("\n");
      } else if (!text) {
        text = clean(document.querySelector("main") || document.body);
      }

      const imageRoot = root || document.querySelector("main") || document.body;
      const images = [...imageRoot.querySelectorAll("img")].map(image => ({
        url: image.currentSrc || image.src || image.dataset.src || image.dataset.original || "",
        alt: image.alt || "",
        width: image.naturalWidth || image.width || 0,
        height: image.naturalHeight || image.height || 0
      })).filter(image => {
        if (!image.url || image.width < 120 || image.height < 120) return false;
        if (isDouyinNote) return /aweme_images/i.test(image.url);
        return true;
      });
      const fallbackImages = isDouyinNote && images.length === 0
        ? [...document.images].map(image => ({
            url: image.currentSrc || image.src || image.dataset.src || "",
            alt: image.alt || "",
            width: image.naturalWidth || image.width || 0,
            height: image.naturalHeight || image.height || 0
          })).filter(image => image.url && image.width >= 120 && image.height >= 120 && /aweme_images/i.test(image.url))
        : [];
      const videos = isDouyinNote ? [] : [
        ...(root || document.querySelector("main") || document.body)
          .querySelectorAll("video,video source")
      ].map(node => node.currentSrc || node.src || "").filter(Boolean);
      if (!isDouyinNote) {
        videos.push(...[
          ...document.querySelectorAll('meta[property="og:video"],meta[property="og:video:url"],meta[name="twitter:player:stream"]')
        ].map(node => node.content || "").filter(Boolean));
      }
      return {
        title,
        canonical_url: document.querySelector('link[rel="canonical"]')?.href || location.href,
        content_type: isDouyinNote ? "image_text" : isWeChatArticle ? "article" : videos.length ? "video" : "article",
        author,
        published_at: (document.body?.innerText || "").match(/发布时间[：:]\s*([^\n]+)/)?.[1] || "",
        text,
        images: [...images, ...fallbackImages],
        video_urls: videos
      };
    }
    """


def extract_public_page(source_url: str, timeout_seconds: int = 45) -> dict:
    if not _is_public_http_url(source_url):
        raise PublicPageError("Only public HTTP or HTTPS pages can be processed")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise PublicPageError(f"Public page extraction requires Playwright: {exc}") from exc

    with sync_playwright() as playwright:
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = _browser_executable()
        if executable:
            launch_args["executable_path"] = executable
        try:
            browser = playwright.chromium.launch(**launch_args)
        except Exception as exc:
            raise PublicPageError(f"Could not start the public page browser: {exc}") from exc
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                locale="zh-CN",
            )
            dns_cache: dict[str, bool] = {}

            def guard_public_request(route):
                request_url = route.request.url
                scheme = urlparse(request_url).scheme.lower()
                if scheme in {"data", "blob", "about"}:
                    route.continue_()
                elif _is_public_http_url(request_url, dns_cache):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            context.route("**/*", guard_public_request)
            page = context.new_page()
            page.set_default_timeout(8000)
            navigation_error = None
            raw_payload = None
            for attempt in range(2):
                try:
                    page.goto(
                        source_url,
                        wait_until="commit",
                        timeout=min(timeout_seconds * 1000, 20000),
                    )
                except Exception as exc:
                    navigation_error = exc
                try:
                    page.wait_for_function(
                        "() => Boolean(document.body && document.body.innerText.trim().length)",
                        timeout=min(timeout_seconds * 1000, 12000),
                    )
                    page.wait_for_timeout(1200)
                    raw_payload = page.evaluate(_page_script())
                    if raw_payload.get("text") or raw_payload.get("images"):
                        break
                except Exception as exc:
                    navigation_error = exc
                if attempt == 0 and not (raw_payload and (raw_payload.get("text") or raw_payload.get("images"))):
                    continue
            if not raw_payload or not (raw_payload.get("text") or raw_payload.get("images")):
                if navigation_error:
                    raise PublicPageError(f"Could not load the public page: {navigation_error}") from navigation_error
                raise PublicPageError("The public page remained empty after two load attempts")
            for _ in range(12):
                page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.8, 400))")
                page.wait_for_timeout(120)
            page.evaluate("window.scrollTo(0, 0)")
            raw_payload = page.evaluate(_page_script())
            normalized = normalize_page_data(raw_payload, page.url or source_url)
            if len(normalized["text"]) < 20 and not normalized["images"]:
                body = str(raw_payload.get("text") or "").lower()
                if any(marker in body for marker in ("验证码", "安全验证", "请登录", "登录后")):
                    raise PublicPageError("The source page requires login or security verification")
            return normalized
        except PublicPageError:
            raise
        except Exception as exc:
            raise PublicPageError(f"Could not read the public page: {exc}") from exc
        finally:
            try:
                context.close()
            finally:
                browser.close()


def download_public_image(
    image_url: str,
    referer: str,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> tuple[bytes, str]:
    current_url = image_url
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    with httpx.Client(timeout=httpx.Timeout(20, read=45), follow_redirects=False) as client:
        for _ in range(5):
            if not _is_public_http_url(current_url):
                raise PublicPageError("Image URL redirected to a non-public network address")
            redirected = False
            for attempt in range(3):
                try:
                    with client.stream("GET", current_url, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise PublicPageError(f"Image server returned an invalid redirect: HTTP {response.status_code}")
                            current_url = urljoin(current_url, location)
                            redirected = True
                            break
                        if response.status_code in {408, 425, 429} or response.status_code >= 500:
                            if attempt < 2:
                                time.sleep(0.35 * (attempt + 1))
                                continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if not content_type.startswith("image/"):
                            raise PublicPageError(f"Image URL returned an unsupported content type: {content_type or 'unknown'}")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise PublicPageError(f"Image exceeds the {max_bytes // (1024 * 1024)} MB size limit")
                        if not body:
                            raise PublicPageError("Image download returned an empty file")
                        return bytes(body), content_type
                except httpx.TransportError as exc:
                    if attempt >= 2:
                        raise PublicPageError(f"Image download failed after three attempts: {exc}") from exc
                    time.sleep(0.35 * (attempt + 1))
            if redirected:
                continue
    raise PublicPageError("Image server exceeded the redirect limit")
