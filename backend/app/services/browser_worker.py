from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path


def _write(payload: dict):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _executable_path() -> str | None:
    configured = os.getenv("ENVOY_BROWSER_EXECUTABLE", "").strip()
    candidates = [
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _extract_records(page):
    return page.eval_on_selector_all(
        "a[href]",
        """anchors => anchors.map(anchor => {
          const rect = anchor.getBoundingClientRect();
          const style = window.getComputedStyle(anchor);
          let text = (anchor.innerText || anchor.getAttribute('aria-label') || anchor.getAttribute('title') || '').trim();
          if (!text && anchor.parentElement) text = (anchor.parentElement.innerText || '').trim();
          return {
            href: anchor.href,
            text: text.slice(0, 240),
            aria_label: anchor.getAttribute('aria-label') || '',
            title: anchor.getAttribute('title') || '',
            visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
          };
        })""",
    )


def _video_urls_from_payload(payload):
    urls = []
    preferred_keys = {
        "play_addr", "download_addr", "url_list",
        "contenturl", "content_url", "videourl", "video_url",
        "playurl", "play_url", "src", "file",
    }

    def visit(value, key=""):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
        elif isinstance(value, list):
            for child_value in value:
                visit(child_value, key)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            lowered = value.lower()
            if key.lower() in preferred_keys and (
                any(host in lowered for host in ("douyinvod", "bytev", "bytedance"))
                or re.search(r"\.(mp4|m3u8|mpd|webm)(?:[?#]|$)", lowered)
            ):
                urls.append(value)

    visit(payload)
    return list(dict.fromkeys(urls))


def _media_url_candidates(page, captured_urls, captured_responses, payload_urls=None):
    urls = []
    urls.extend((110, url, True) for url in payload_urls or [])
    for item in captured_responses:
        if isinstance(item, dict):
            content_type = str(item.get("content_type") or "")
            resource_type = str(item.get("resource_type") or "")
            score = 100 if "mpegurl" in content_type or "dash" in content_type else 80
            is_media_response = (
                content_type.startswith(("video/", "audio/"))
                or "mpegurl" in content_type
                or "dash" in content_type
            )
            if is_media_response:
                urls.append((score, item.get("url"), True))
            elif resource_type == "document":
                urls.append((40, item.get("url"), False))
    urls.extend((60, item, False) for item in captured_urls)
    try:
        urls.extend((130, url, True) for url in page.locator("video").evaluate_all(
            """videos => videos.flatMap(video => [video.currentSrc, video.src]).filter(Boolean)"""
        ))
    except Exception:
        pass
    for selector in ("video source", 'meta[property="og:video"]', 'meta[property="og:video:url"]', 'meta[name="twitter:player:stream"]'):
        try:
            urls.extend((70, url, True) for url in page.locator(selector).evaluate_all(
                """nodes => nodes.map(node => node.src || node.content).filter(Boolean)"""
            ))
        except Exception:
            pass
    try:
        urls.extend((70, url, True) for url in page.evaluate(
            """() => {
              const values = [];
              for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
                try {
                  const parsed = JSON.parse(script.textContent || '');
                  const stack = Array.isArray(parsed) ? [...parsed] : [parsed];
                  while (stack.length) {
                    const item = stack.pop();
                    if (!item || typeof item !== 'object') continue;
                    for (const [key, value] of Object.entries(item)) {
                      if (['contentUrl', 'content_url', 'videoUrl', 'video_url', 'playUrl', 'play_url'].includes(key) && typeof value === 'string') values.push(value);
                      if (value && typeof value === 'object') stack.push(value);
                    }
                  }
                } catch (_) {}
              }
              return values;
            }"""
        ))
    except Exception:
        pass
    try:
        urls.extend((50, url, False) for url in page.evaluate(
            """() => performance.getEntriesByType('resource').map(entry => entry.name)"""
        ))
    except Exception:
        pass
    result = []
    seen = set()
    for _score, url, is_media_response in sorted(urls, key=lambda item: item[0], reverse=True):
        value = str(url or "").strip()
        if not value.startswith(("http://", "https://")) or value in seen:
            continue
        lowered = value.lower()
        if not is_media_response and not (
            re.search(r"\.(mp4|m3u8|mpd|m4s|ts)(?:[?#]|$)", lowered)
            or any(host in lowered for host in ("douyinvod", "aweme", "bytev", "bytedance"))
        ):
            continue
        seen.add(value)
        result.append(value)
    return list(dict.fromkeys(result))


def _dismiss_login_overlays(page):
    selectors = [
        '[aria-label="关闭"]',
        '[aria-label="Close"]',
        '[aria-label="关闭弹窗"]',
        '[data-testid*="close" i]',
        '[data-e2e*="close" i]',
        'button:has-text("关闭")',
        'button:has-text("暂不登录")',
        'button:has-text("稍后再说")',
        'button:has-text("以后再说")',
        'button:has-text("跳过")',
        'button:has-text("Not now")',
        'button:has-text("Maybe later")',
        'button:has-text("×")',
        '[class*="close"]',
        '[class*="Close"]',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                item = locator.nth(index)
                if item.is_visible():
                    item.click(timeout=1000)
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _start_video_playback(page):
    try:
        page.locator("video").evaluate_all(
            """videos => videos.map(video => {
              video.muted = true;
              video.play().catch(() => {});
              return {paused: video.paused, currentSrc: video.currentSrc};
            })"""
        )
    except Exception:
        pass
    for selector in (
        'button[aria-label*="play" i]',
        '[role="button"][aria-label*="play" i]',
        'button:has-text("播放")',
        '[class*="play" i]',
    ):
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                item = locator.nth(index)
                if item.is_visible():
                    item.click(timeout=1000)
        except Exception:
            continue


def _media_diagnostics(page, captured_media_urls, captured_media_responses, captured_payloads):
    try:
        video_sources = page.locator("video").evaluate_all(
            """videos => videos.flatMap(video => [{
              src: video.src || "",
              currentSrc: video.currentSrc || "",
              paused: video.paused,
              readyState: video.readyState,
              duration: Number.isFinite(video.duration) ? video.duration : null
            }])"""
        )
    except Exception:
        video_sources = []
    payload_urls = []
    for payload in captured_payloads:
        payload_urls.extend(_video_urls_from_payload(payload))
    return {
        "request_count": len(captured_media_urls),
        "response_count": len(captured_media_responses),
        "response_types": sorted(set(str(item.get("content_type") or "") for item in captured_media_responses)),
        "video_sources": video_sources,
        "payload_url_count": len(payload_urls),
        "candidate_count": len(_media_url_candidates(
            page, captured_media_urls, captured_media_responses, payload_urls
        )),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--profile-dir", required=True)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _write({"status": "failed", "error": f"Playwright is not installed: {exc}"})
        return 1

    with sync_playwright() as playwright:
        executable = _executable_path()
        launch_kwargs = {
            "headless": False,
            "user_data_dir": args.profile_dir,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if executable:
            launch_kwargs["executable_path"] = executable
        try:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            captured_media_urls = []
            captured_media_responses = []
            captured_payloads = []
            page.on(
                "request",
                lambda request: captured_media_urls.append(request.url)
                if request.resource_type in {"media", "xhr", "fetch"} and (
                    re.search(r"\.(mp4|m3u8|mpd)(?:[?#]|$)", request.url, re.I)
                    or "douyinvod.com" in request.url
                )
                else None,
            )
            def capture_response(response):
                resource_type = response.request.resource_type
                if resource_type not in {"media", "xhr", "fetch"}:
                    return
                content_type = str(response.headers.get("content-type") or "").lower()
                is_media = (
                    content_type.startswith(("video/", "audio/"))
                    or "mpegurl" in content_type
                    or "dash" in content_type
                    or "octet-stream" in content_type
                    or "mp2t" in content_type
                    or "mp4" in content_type
                    or "webm" in content_type
                )
                if is_media:
                    captured_media_responses.append({
                        "url": response.url,
                        "content_type": content_type,
                        "resource_type": resource_type,
                    })
                if "json" in content_type:
                    try:
                        captured_payloads.append(response.json())
                    except Exception:
                        pass

            page.on("response", capture_response)
            navigation_error = ""
            navigation_urls = [args.url]
            if args.platform == "vimeo":
                match = re.search(r"vimeo\.com/(?:video/)?(\d+)", args.url)
                if match:
                    navigation_urls.append(f"https://player.vimeo.com/video/{match.group(1)}")
            for navigation_url in navigation_urls:
                try:
                    page.goto(navigation_url, wait_until="domcontentloaded", timeout=60000)
                    navigation_error = ""
                    break
                except Exception as exc:
                    # Keep the visible session alive so the user can refresh,
                    # solve a challenge, or navigate manually.
                    navigation_error = str(exc)[:1200]
            page.wait_for_timeout(5000)
            _dismiss_login_overlays(page)
            _start_video_playback(page)
            _write({
                "status": "ready",
                "title": page.title(),
                "url": page.url,
                "navigation_error": navigation_error or None,
            })
        except Exception as exc:
            _write({"status": "failed", "error": str(exc)[:1200]})
            return 1

        try:
            for line in sys.stdin:
                command = json.loads(line).get("command")
                if command == "status":
                    _write({
                        "status": "page",
                        "title": page.title(),
                        "url": page.url,
                        "body_text": page.locator("body").inner_text(timeout=5000)[:4000],
                        "media": _media_diagnostics(
                            page, captured_media_urls, captured_media_responses, captured_payloads
                        ),
                    })
                elif command == "extract":
                    for _ in range(6):
                        page.mouse.wheel(0, 900)
                        page.wait_for_timeout(700)
                    _write({
                        "status": "extracted",
                        "title": page.title(),
                        "url": page.url,
                        "body_text": page.locator("body").inner_text(timeout=5000)[:4000],
                        "records": _extract_records(page),
                    })
                elif command == "cookies":
                    _write({"status": "cookies", "cookies": context.cookies()})
                elif command == "download_media":
                    request_payload = json.loads(line)
                    page.wait_for_timeout(4000)
                    _dismiss_login_overlays(page)
                    _start_video_playback(page)
                    page.wait_for_timeout(5000)
                    output_path = str(request_payload.get("output_path") or "")
                    payload_urls = []
                    for payload in captured_payloads:
                        payload_urls.extend(_video_urls_from_payload(payload))
                    candidates = _media_url_candidates(
                        page,
                        captured_media_urls,
                        captured_media_responses,
                        payload_urls,
                    )
                    if not output_path or not candidates:
                        _write({
                            "status": "failed",
                            "error": (
                                "No playable media URL was found in the browser page; "
                                f"captured {len(captured_media_urls) + len(captured_media_responses)} media request(s)"
                            ),
                        })
                        continue
                    try:
                        import requests

                        user_agent = page.evaluate("navigator.userAgent")
                        cookie_header = "; ".join(
                            f"{cookie.get('name')}={cookie.get('value')}"
                            for cookie in context.cookies()
                            if cookie.get("name")
                        )
                        media_url = candidates[0]
                        if ".m3u8" in media_url.lower() or "mpegurl" in media_url.lower():
                            ffmpeg = shutil.which(os.getenv("FFMPEG_BIN", "ffmpeg"))
                            if not ffmpeg:
                                raise RuntimeError("ffmpeg is required to download the browser media playlist")
                            header = (
                                f"Referer: {page.url}\r\n"
                                f"User-Agent: {user_agent}\r\n"
                                f"Cookie: {cookie_header}\r\n"
                            )
                            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                            subprocess.run(
                                [
                                    ffmpeg,
                                    "-y",
                                    "-headers",
                                    header,
                                    "-i",
                                    media_url,
                                    "-c",
                                    "copy",
                                    output_path,
                                ],
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=600,
                            )
                            _write({
                                "status": "downloaded",
                                "path": output_path,
                                "url": media_url,
                                "title": page.title(),
                            })
                            continue
                        with requests.Session() as request_session:
                            for cookie in context.cookies():
                                request_session.cookies.set(
                                    cookie.get("name"),
                                    cookie.get("value"),
                                    domain=cookie.get("domain"),
                                    path=cookie.get("path", "/"),
                                )
                            response = request_session.get(
                                candidates[0],
                                headers={"Referer": page.url, "User-Agent": user_agent},
                                stream=True,
                                timeout=(20, 300),
                            )
                            response.raise_for_status()
                            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                            temp_path = f"{output_path}.part"
                            with open(temp_path, "wb") as output:
                                for chunk in response.iter_content(chunk_size=1024 * 1024):
                                    if chunk:
                                        output.write(chunk)
                            os.replace(temp_path, output_path)
                        _write({
                            "status": "downloaded",
                            "path": output_path,
                            "url": candidates[0],
                            "title": page.title(),
                        })
                    except Exception as exc:
                        _write({"status": "failed", "error": str(exc)[:1200]})
                elif command == "close":
                    _write({"status": "closed"})
                    break
                else:
                    _write({"status": "failed", "error": f"Unknown browser command: {command}"})
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
