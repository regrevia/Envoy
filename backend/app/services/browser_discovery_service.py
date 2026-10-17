from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.services.video_source_service import classify_video_source


VIDEO_PATH_MARKERS = (
    "/video/",
    "/watch",
    "/shorts/",
    "/reel/",
    "/reels/",
    "/p/",
    "/tv/",
    "/status/",
    "/v_show/",
    "/x/page/",
    "/explore/",
)


def _title_from_record(record: dict, fallback: str) -> str:
    for key in ("text", "aria_label", "title"):
        value = str(record.get(key) or "").strip()
        if value:
            return " ".join(value.split())[:240]
    return fallback


def _item_id(url: str) -> str:
    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id
    return parsed.path.rstrip("/").split("/")[-1] or url


def _looks_like_video_url(url: str, platform: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if platform == "youtube" and host == "youtu.be":
        return True
    if platform == "bilibili" and host == "b23.tv":
        return True
    if platform == "vimeo" and path.count("/") == 1 and path[1:].isdigit():
        return True
    return any(marker in path for marker in VIDEO_PATH_MARKERS)


def extract_visible_video_items(records: list[dict], platform: str, limit: int = 100) -> list[dict]:
    items = []
    seen = set()
    bounded_limit = min(max(int(limit or 1), 1), 100)
    for record in records:
        if not isinstance(record, dict) or not record.get("visible"):
            continue
        url = str(record.get("href") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        if not _looks_like_video_url(url, platform):
            continue
        seen.add(url)
        items.append({
            "id": _item_id(url),
            "source_url": url,
            "title": _title_from_record(record, _item_id(url)),
            "duration_sec": record.get("duration_sec"),
            "thumbnail": record.get("thumbnail"),
            "published_at": record.get("published_at"),
            "platform": platform,
            "discovery_method": "browser_visible",
        })
        if len(items) >= bounded_limit:
            break
    return items


def classify_browser_page_state(title: str, body_text: str, item_count: int) -> dict:
    lowered = f"{title}\n{body_text}".lower()
    user_action_markers = (
        "captcha",
        "challenge",
        "sign in",
        "log in",
        "login",
        "verify you are human",
        "验证码",
        "登录后",
        "安全验证",
    )
    if item_count > 0:
        status = "ready"
        requires_user_action = False
    elif any(marker in lowered for marker in user_action_markers):
        status = "needs_user_action"
        requires_user_action = True
    else:
        status = "empty"
        requires_user_action = False
    return {
        "status": status,
        "requires_user_action": requires_user_action,
        "title": str(title or "").strip(),
        "visible_item_count": int(item_count or 0),
    }


def browser_source_metadata(source_url: str) -> dict:
    metadata = classify_video_source(source_url)
    return {
        "source_url": source_url,
        "platform": metadata["platform"],
        "source_kind": metadata["source_kind"],
        "discovery_method": "browser_visible",
    }
