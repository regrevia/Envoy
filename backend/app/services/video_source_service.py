from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import httpx
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse

class VideoSourceError(RuntimeError):
    pass


SUPPORTED_BROWSERS = {"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}
BROWSER_TRY_ORDER = ("chrome", "edge", "firefox", "brave", "chromium", "vivaldi", "opera", "whale", "safari")
DISCOVERY_TIMEOUT_SECONDS = 90
DISCOVERY_BROWSER_ATTEMPT_SECONDS = 15


def export_browser_session_cookies(session_id: str) -> str:
    from app.services.browser_session_service import browser_session_manager

    return browser_session_manager.export_cookies(session_id)


def download_browser_session_video(session_id: str, source_url: str, output_dir: str | Path) -> dict:
    from app.services.browser_session_service import browser_session_manager

    return browser_session_manager.download_media(session_id, source_url, str(output_dir))

PLATFORM_HOSTS = {
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "bilibili": {"bilibili.com", "www.bilibili.com", "space.bilibili.com", "b23.tv"},
    "douyin": {"douyin.com", "www.douyin.com", "iesdouyin.com", "v.douyin.com"},
    "vimeo": {"vimeo.com", "www.vimeo.com"},
    "twitch": {"twitch.tv", "www.twitch.tv"},
    "instagram": {"instagram.com", "www.instagram.com"},
    "facebook": {"facebook.com", "www.facebook.com", "fb.watch"},
    "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
    "kuaishou": {"kuaishou.com", "www.kuaishou.com", "gifshow.com", "v.kuaishou.com"},
    "ixigua": {"ixigua.com", "www.ixigua.com"},
    "youku": {"youku.com", "www.youku.com"},
    "tencent_video": {"v.qq.com"},
    "iqiyi": {"iqiyi.com", "www.iqiyi.com"},
    "xiaohongshu": {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com"},
    "dailymotion": {"dailymotion.com", "www.dailymotion.com", "geo.dailymotion.com"},
}


def normalize_video_auth(auth: dict | None) -> dict:
    if not auth:
        return {}
    if not isinstance(auth, dict):
        raise VideoSourceError("Video auth must be an object")
    mode = str(auth.get("mode") or "").strip().lower()
    if mode in {"", "none", "public"}:
        return {}
    if mode == "auto":
        return {"mode": "auto"}
    if mode in {"browser", "browser_auto"}:
        if mode == "browser_auto":
            return {"mode": "browser_auto"}
        browser = str(auth.get("browser") or "").strip().lower()
        if browser not in SUPPORTED_BROWSERS:
            raise VideoSourceError(
                f"Unsupported browser for cookies; use one of: {', '.join(sorted(SUPPORTED_BROWSERS))}"
            )
        return {"mode": "browser", "browser": browser}
    if mode == "browser_session":
        session_id = str(auth.get("session_id") or "").strip()
        if not session_id:
            raise VideoSourceError("Browser session id is required")
        return {"mode": "browser_session", "session_id": session_id}
    raise VideoSourceError("Video auth mode must be auto, public, browser, or browser_session")


def _auth_args(auth: dict | None) -> list[str]:
    normalized = normalize_video_auth(auth)
    if normalized.get("mode") == "browser":
        return ["--cookies-from-browser", normalized["browser"]]
    if normalized.get("mode") == "browser_session":
        return ["--cookies", export_browser_session_cookies(normalized["session_id"])]
    return []


def _browser_cookie_store_exists(browser: str) -> bool:
    home = Path.home()
    cookie_patterns = {
        "chrome": (
            home / "Library/Application Support/Google/Chrome/*/Cookies",
            home / ".config/google-chrome/*/Cookies",
            home / "AppData/Local/Google/Chrome/User Data/*/Cookies",
        ),
        "edge": (
            home / "Library/Application Support/Microsoft Edge/*/Cookies",
            home / ".config/microsoft-edge/*/Cookies",
            home / "AppData/Local/Microsoft/Edge/User Data/*/Cookies",
        ),
        "brave": (
            home / "Library/Application Support/BraveSoftware/Brave-Browser/*/Cookies",
            home / ".config/BraveSoftware/Brave-Browser/*/Cookies",
            home / "AppData/Local/BraveSoftware/Brave-Browser/User Data/*/Cookies",
        ),
        "chromium": (
            home / "Library/Application Support/Chromium/*/Cookies",
            home / ".config/chromium/*/Cookies",
            home / "AppData/Local/Chromium/User Data/*/Cookies",
        ),
        "firefox": (
            home / "Library/Application Support/Firefox/Profiles/*/cookies.sqlite",
            home / ".mozilla/firefox/*/cookies.sqlite",
            home / "AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite",
        ),
        "opera": (
            home / "Library/Application Support/com.operasoftware.Opera/*/Cookies",
            home / ".config/opera/*/Cookies",
        ),
        "vivaldi": (
            home / "Library/Application Support/Vivaldi/*/Cookies",
            home / ".config/vivaldi/*/Cookies",
        ),
        "whale": (
            home / "Library/Application Support/Naver/Whale/*/Cookies",
            home / ".config/naver-whale/*/Cookies",
        ),
    }
    if browser == "safari":
        if os.getenv("ENVOY_ALLOW_SAFARI_COOKIES", "").strip().lower() not in {"1", "true", "yes"}:
            return False
        return (home / "Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies").is_file()
    return any(
        Path(candidate).is_file()
        for pattern in cookie_patterns.get(browser, ())
        for candidate in glob.glob(str(pattern))
    )


def _available_browser_order() -> list[str]:
    return [browser for browser in BROWSER_TRY_ORDER if _browser_cookie_store_exists(browser)]


def _auth_candidates(auth: dict | None) -> list[dict]:
    normalized = normalize_video_auth(auth)
    mode = normalized.get("mode")
    if mode == "auto":
        return [{}, *({"mode": "browser", "browser": browser} for browser in _available_browser_order())]
    if mode == "browser_auto":
        browsers = _available_browser_order()
        if not browsers:
            return []
        return [{"mode": "browser", "browser": browser} for browser in browsers]
    return [normalized]


def _clean_ytdlp_error(message: str) -> str:
    cleaned = []
    for line in str(message or "").splitlines():
        lowered = line.lower()
        if "notopensslwarning" in lowered or "warnings.warn(" in lowered:
            continue
        if "deprecated feature: support for python version" in lowered:
            continue
        if "cookies.binarycookies" in lowered and "operation not permitted" in lowered:
            continue
        if "could not find" in lowered and "cookies database" in lowered:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _combined_source_errors(errors: list[str], fallback: str) -> str:
    unique = []
    seen = set()
    for error in errors:
        cleaned = _clean_ytdlp_error(error)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return "\n\n".join(unique[-3:]) or fallback


def _is_auth_error(error: Exception | str) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "http error 401",
        "http error 403",
        "sign in",
        "log in",
        "login required",
        "confirm you",
        "cookies",
        "cookie",
    ))


def _is_browser_recoverable_error(error: Exception | str) -> bool:
    message = str(error).lower()
    return _is_auth_error(message) or any(marker in message for marker in (
        "unsupported url",
        "no video entries",
        "generic information extractor",
        "http error 412",
        "precondition failed",
        "eof occurred in violation of protocol",
        "ssl_error",
        "unable to download webpage",
    ))


def _is_permanent_source_error(error: Exception | str) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in (
        "http error 404",
        "http error 410",
        "not found",
        "video does not exist",
        "no video could be found",
    ))


def classify_discovery_error(error: Exception | str) -> dict:
    message = str(error or "").strip()
    lowered = message.lower()
    if _is_permanent_source_error(message):
        return {
            "reason_code": "source_not_found",
            "requires_user_action": False,
            "recommended_action": "check_url",
            "raw_error": message,
        }
    if any(marker in lowered for marker in (
        "eof occurred in violation of protocol",
        "connection timed out",
        "connection refused",
        "err_connection_closed",
        "max retries exceeded",
        "ssl_error",
    )):
        return {
            "reason_code": "source_network_error",
            "requires_user_action": False,
            "recommended_action": "retry_or_check_network",
            "raw_error": message,
        }
    if any(marker in lowered for marker in (
        "captcha",
        "challenge",
        "verify you are human",
        "verification required",
        "验证码",
        "验证后",
        "request is rejected",
        "access denied",
        "anti-bot",
        "http error 352",
        "risk control",
        "风控",
    )):
        return {
            "reason_code": "challenge_required",
            "requires_user_action": True,
            "recommended_action": "open_in_browser_and_retry",
            "raw_error": message,
        }
    if _is_auth_error(message):
        return {
            "reason_code": "authentication_required",
            "requires_user_action": True,
            "recommended_action": "open_in_browser_and_retry",
            "raw_error": message,
        }
    if "unsupported url" in lowered or "no video entries" in lowered:
        return {
            "reason_code": "unsupported_url",
            "requires_user_action": False,
            "recommended_action": "paste_video_links_or_upload",
            "raw_error": message,
        }
    if "timed out" in lowered or "timeout" in lowered:
        return {
            "reason_code": "source_timeout",
            "requires_user_action": False,
            "recommended_action": "retry",
            "raw_error": message,
        }
    return {
        "reason_code": "discovery_failed",
        "requires_user_action": False,
        "recommended_action": "retry_or_paste_links",
        "raw_error": message,
    }


def _ytdlp_command() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    return [sys.executable, "-m", "yt_dlp"]


def _platform_download_args(source_url: str) -> list[str]:
    platform = _platform_for_host((urlparse(str(source_url)).hostname or "").lower().rstrip("."))
    if platform == "youtube":
        return ["--extractor-args", "youtube:player_client=android", "--force-ipv4"]
    return []


def validate_video_url(source_url: str) -> str:
    value = str(source_url or "").strip()
    if re.fullmatch(r"BV[0-9A-Za-z]{8,}", value):
        return f"https://www.bilibili.com/video/{value}"
    if re.fullmatch(r"av\d{1,}", value, flags=re.IGNORECASE):
        return f"https://www.bilibili.com/video/{value.lower()}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VideoSourceError("Video URL must start with http:// or https://")
    if parsed.username or parsed.password:
        raise VideoSourceError("Video URL must not contain embedded credentials")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"douyin.com", "www.douyin.com"}:
        modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0].strip()
        if re.fullmatch(r"\d{8,}", modal_id):
            return urlunparse(("https", "www.douyin.com", f"/video/{modal_id}", "", "", ""))
    return value


def resolve_video_url(source_url: str) -> str:
    """Follow lightweight share-link redirects before handing the URL to extractors."""
    value = validate_video_url(source_url)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"v.douyin.com", "iesdouyin.com", "b23.tv"}:
        return value
    try:
        response = httpx.get(
            value,
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=12,
        )
        response.raise_for_status()
        final_url = str(response.url or value)
        return validate_video_url(final_url)
    except Exception:
        # The browser fallback can still resolve a short link when the
        # redirect endpoint requires JavaScript or blocks non-browser clients.
        return value


def _host_matches(host: str, candidate: str) -> bool:
    return host == candidate or host.endswith(f".{candidate}")


def _platform_for_host(host: str) -> str:
    for platform, hosts in PLATFORM_HOSTS.items():
        if any(_host_matches(host, candidate) for candidate in hosts):
            return platform
    return "unknown"


def _source_kind_for_path(platform: str, host: str, path: str) -> str:
    normalized_path = path.rstrip("/").lower()
    if platform == "douyin" and (host == "v.douyin.com" or host == "iesdouyin.com"):
        return "share"
    if platform == "youtube" and host == "youtu.be":
        return "video"
    if platform == "bilibili" and host == "b23.tv":
        return "share"
    if platform == "kuaishou" and host in {"v.kuaishou.com", "gifshow.com"}:
        return "share"
    if platform == "xiaohongshu" and host == "xhslink.com":
        return "share"
    if platform == "ixigua" and normalized_path and normalized_path.count("/") == 1:
        return "video"
    if platform == "vimeo" and normalized_path.count("/") == 1 and normalized_path[1:].isdigit():
        return "video"

    video_markers = {
        "youtube": ("/watch", "/shorts/", "/live/"),
        "bilibili": ("/video/", "/bangumi/play/"),
        "douyin": ("/video/",),
        "vimeo": (),
        "twitch": ("/videos/",),
        "instagram": ("/reel/", "/reels/", "/p/", "/tv/"),
        "facebook": ("/reel/", "/watch", "/videos/"),
        "x": ("/status/",),
        "dailymotion": ("/video/",),
        "kuaishou": ("/short-video/", "/video/"),
        "ixigua": ("/video/",),
        "youku": ("/v_show/",),
        "tencent_video": ("/x/page/",),
        "iqiyi": ("/v_",),
        "xiaohongshu": ("/explore/", "/discovery/item/"),
    }
    if any(marker in normalized_path for marker in video_markers.get(platform, ())):
        return "video"

    collection_markers = {
        "youtube": ("/playlist",),
        "bilibili": ("/medialist/", "/lists/"),
        "vimeo": ("/showcase/", "/album/", "/channels/"),
        "twitch": ("/directory",),
    }
    if any(marker in normalized_path for marker in collection_markers.get(platform, ())):
        return "collection"

    creator_markers = {
        "youtube": ("/@", "/channel/", "/user/", "/c/"),
        "bilibili": ("space.bilibili.com",),
        "douyin": ("/user/",),
        "vimeo": ("/user/",),
        "twitch": ("/videos",),
        "instagram": ("/reels",),
        "facebook": ("/profile.php",),
        "kuaishou": ("/profile/",),
        "ixigua": ("/home/"),
        "youku": ("/u/"),
    }
    if platform == "bilibili" and host == "space.bilibili.com":
        return "creator"
    if any(marker in normalized_path or marker in host for marker in creator_markers.get(platform, ())):
        return "creator"
    return "unknown"


def classify_video_source(source_url: str) -> dict:
    url = validate_video_url(source_url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    platform = _platform_for_host(host)
    source_kind = _source_kind_for_path(platform, host, parsed.path)
    return {
        "source_url": url,
        "platform": platform,
        "source_kind": source_kind,
        "host": host,
        "supports_ytdlp": platform != "unknown",
        "supports_browser_discovery": platform != "unknown",
    }


def _run_ytdlp(
    args: list[str],
    auth: dict | None = None,
    timeout: int = 3600,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [*_ytdlp_command(), "--ignore-config", *_auth_args(auth), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise VideoSourceError("yt-dlp executable not found; install yt-dlp and retry") from exc
    except subprocess.CalledProcessError as exc:
        detail = _clean_ytdlp_error((exc.stderr or exc.stdout or str(exc)).strip())
        if not detail:
            detail = "yt-dlp failed without a useful source error; check the media runtime and retry"
        raise VideoSourceError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip() if isinstance(exc.stderr, bytes) else str(exc.stderr or "").strip()
        detail = _clean_ytdlp_error(detail)
        message = f"yt-dlp timed out after {timeout} seconds"
        raise VideoSourceError(f"{message}: {detail}" if detail else message) from exc


def download_video(source_url: str, output_dir: str | Path, auth: dict | None = None) -> dict:
    url = resolve_video_url(source_url)
    normalized_auth = normalize_video_auth(auth)
    if normalized_auth.get("mode") == "browser_session":
        return download_browser_session_video(normalized_auth["session_id"], url, output_dir)
    errors = []
    for candidate in _auth_candidates(auth):
        try:
            root = Path(output_dir).resolve()
            root.mkdir(parents=True, exist_ok=True)
            platform_args = _platform_download_args(url)
            info_result = _run_ytdlp([*platform_args, "--dump-single-json", "--skip-download", "--no-playlist", url], candidate)
            try:
                info = json.loads(info_result.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise VideoSourceError(f"yt-dlp returned invalid metadata JSON: {info_result.stdout}") from exc

            video_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(info.get("id") or "video")).strip("._") or "video"
            output_template = str(root / f"{video_id}.%(ext)s")
            download_result = _run_ytdlp([
                *platform_args,
                "--no-playlist",
                "--newline",
                "--format", "bestvideo*+bestaudio/best",
                "--merge-output-format", "mp4",
                "--output", output_template,
                "--print", "after_move:filepath",
                url,
            ], candidate)
            downloaded_path = next(
                (Path(line.strip()).resolve() for line in reversed(download_result.stdout.splitlines()) if line.strip()),
                None,
            )
            if downloaded_path is None or not downloaded_path.is_file():
                candidates = sorted(root.glob(f"{video_id}.*"))
                downloaded_path = candidates[-1].resolve() if candidates else None
            if downloaded_path is None or not downloaded_path.is_file():
                raise VideoSourceError(
                    f"yt-dlp completed without a downloaded file; stdout: {download_result.stdout.strip()}"
                )
            return {
                "path": str(downloaded_path),
                "video_id": str(info.get("id") or video_id),
                "title": str(info.get("title") or info.get("fulltitle") or video_id),
                "duration_sec": float(info["duration"]) if info.get("duration") is not None else None,
                "extractor": str(info.get("extractor_key") or info.get("extractor") or "unknown"),
                "webpage_url": str(info.get("webpage_url") or url),
                "thumbnail": info.get("thumbnail"),
                "description": str(info.get("description") or ""),
            }
        except VideoSourceError as exc:
            errors.append(str(exc))
    if (
        normalized_auth.get("mode") == "auto"
        and not any(_is_permanent_source_error(error) for error in errors)
        and (
            classify_video_source(url).get("supports_browser_discovery")
            or url.startswith(("http://", "https://"))
        )
        and any(_is_browser_recoverable_error(error) for error in errors)
    ):
        session_id = None
        browser_downloaded = False
        try:
            from app.services.browser_session_service import browser_session_manager

            session = browser_session_manager.create(url, limit=1)
            session_id = str(session.get("session_id") or "").strip()
            if not session_id:
                raise VideoSourceError("Automatic browser session did not return a session id")
            result = browser_session_manager.download_media(session_id, url, str(output_dir))
            browser_downloaded = True
            return result
        except Exception as exc:
            errors.append(f"{exc} (browser session retained: {session_id})")
        finally:
            if session_id and browser_downloaded:
                try:
                    browser_session_manager.close(session_id)
                except Exception:
                    pass
    raise VideoSourceError(_combined_source_errors(errors, "yt-dlp could not download the video"))


def _playlist_video_url(entry: dict, playlist: dict) -> str:
    for field in ("webpage_url", "original_url", "url"):
        candidate = str(entry.get(field) or "").strip()
        if candidate.startswith(("https://", "http://")):
            return candidate
    video_id = str(entry.get("id") or entry.get("url") or "").strip()
    extractor = str(entry.get("extractor_key") or playlist.get("extractor_key") or "").lower()
    if not video_id:
        return ""
    if "bili" in extractor:
        return f"https://www.bilibili.com/video/{video_id}"
    if "douyin" in extractor:
        return f"https://www.douyin.com/video/{video_id}"
    if "youtube" in extractor:
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def discover_video_items(source_url: str, limit: int = 100, auth: dict | None = None) -> dict:
    url = resolve_video_url(source_url)
    source_metadata = classify_video_source(url)
    bounded_limit = min(max(int(limit or 1), 1), 100)
    errors = []
    candidates = _auth_candidates(auth)
    started_at = time.monotonic()
    for index, candidate in enumerate(candidates):
        elapsed = time.monotonic() - started_at
        remaining = DISCOVERY_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            break
        try:
            result = _run_ytdlp(
                [
                    "--dump-single-json",
                    "--flat-playlist",
                    "--playlist-end", str(bounded_limit),
                    "--no-warnings",
                    url,
                ],
                auth=candidate,
                timeout=min(
                    DISCOVERY_BROWSER_ATTEMPT_SECONDS if index else DISCOVERY_TIMEOUT_SECONDS,
                    max(int(remaining), 1),
                ),
            )
            playlist = json.loads(result.stdout)
            if not isinstance(playlist, dict):
                raise VideoSourceError("yt-dlp returned invalid playlist metadata")
            entries = playlist.get("entries")
            if entries is None:
                entries = [playlist]
            if not isinstance(entries, list):
                raise VideoSourceError("yt-dlp returned invalid playlist entries")
            items = []
            seen_urls = set()
            for entry in entries[:bounded_limit]:
                if not isinstance(entry, dict):
                    continue
                item_url = _playlist_video_url(entry, playlist)
                if not item_url or item_url in seen_urls:
                    continue
                seen_urls.add(item_url)
                items.append({
                    "id": str(entry.get("id") or ""),
                    "source_url": item_url,
                    "title": str(entry.get("title") or entry.get("id") or "Untitled video"),
                    "duration_sec": entry.get("duration"),
                    "thumbnail": entry.get("thumbnail"),
                    "published_at": entry.get("timestamp") or entry.get("upload_date"),
                    "extractor": str(entry.get("extractor_key") or playlist.get("extractor_key") or ""),
                })
            if not items:
                raise VideoSourceError("No video entries were found at this URL")
            return {
                "source_url": url,
                "platform": source_metadata["platform"],
                "source_kind": source_metadata["source_kind"],
                "discovery_method": "ytdlp",
                "creator_title": str(playlist.get("title") or playlist.get("playlist_title") or ""),
                "extractor": str(playlist.get("extractor_key") or playlist.get("extractor") or ""),
                "items": items,
                "item_count": len(items),
                "limit": bounded_limit,
            }
        except (VideoSourceError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
            if index == 0 and len(candidates) > 1 and not _is_auth_error(exc):
                break
            if "unsupported url" in str(exc).lower():
                break
    raise VideoSourceError(_combined_source_errors(errors, "Unable to discover videos at this URL"))
