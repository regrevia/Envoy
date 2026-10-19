from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit


MAX_SUBTITLE_BYTES = 16 * 1024 * 1024
SUPPORTED_SUBTITLE_FORMATS = ("vtt", "json3", "srt")
_TIMESTAMP = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{1,3})\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{1,3})"
)
_TAG = re.compile(r"<[^>]*>|{\\an\d+}")


@lru_cache(maxsize=1)
def _load_yt_dlp():
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError:
        return None, []
    return YoutubeDL, gen_extractor_classes()


def has_specialized_extractor(source_url: str) -> bool:
    _youtube_dl, extractor_classes = _load_yt_dlp()
    for extractor in extractor_classes:
        if str(getattr(extractor, "IE_NAME", "")).lower() == "generic":
            continue
        try:
            if extractor.suitable(source_url):
                return True
        except (TypeError, ValueError, re.error):
            continue
    return False


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    sanitized = []
    for token in message.split():
        if token.startswith(("http://", "https://")):
            parts = urlsplit(token.rstrip(".,);"))
            clean = urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
            token = clean + (" [query redacted]" if parts.query else "")
        sanitized.append(token)
    return " ".join(sanitized)[:1200] or type(exc).__name__


def _seconds(value: str) -> float:
    pieces = value.replace(",", ".").split(":")
    try:
        parts = [float(item) for item in pieces]
    except ValueError:
        return -1
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return -1


def _clean_caption(value: str) -> str:
    text = html.unescape(_TAG.sub("", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_vtt_or_srt(text: str) -> list[dict]:
    lines = text.lstrip("\ufeff").splitlines()
    cues = []
    index = 0
    while index < len(lines):
        match = _TIMESTAMP.search(lines[index])
        if not match:
            index += 1
            continue
        start = _seconds(match.group("start"))
        end = _seconds(match.group("end"))
        index += 1
        caption_lines = []
        while index < len(lines) and lines[index].strip():
            caption_lines.append(_clean_caption(lines[index]))
            index += 1
        caption = _clean_caption(" ".join(caption_lines))
        if start >= 0 and end > start and caption and not caption.startswith("["):
            if cues and caption == cues[-1]["text"] and start <= cues[-1]["end"] + 0.15:
                cues[-1]["end"] = max(cues[-1]["end"], end)
            else:
                cues.append({"start": start, "end": end, "text": caption})
    return cues


def _parse_json3(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return []
    cues = []
    for event in payload.get("events", []) if isinstance(payload, dict) else []:
        if not isinstance(event, dict) or event.get("dDurationMs") is None:
            continue
        try:
            start = float(event.get("tStartMs") or 0) / 1000
            end = start + float(event["dDurationMs"]) / 1000
        except (TypeError, ValueError):
            continue
        pieces = [
            _clean_caption(str(segment.get("utf8") or ""))
            for segment in event.get("segs", [])
            if isinstance(segment, dict)
        ]
        caption = _clean_caption(" ".join(pieces))
        if end > start and caption:
            cues.append({"start": start, "end": end, "text": caption})
    return cues


def parse_subtitle_document(data: bytes, extension: str) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    extension = str(extension or "").lower().lstrip(".")
    if extension == "json3":
        return _parse_json3(text)
    if extension in {"vtt", "srt"}:
        return _parse_vtt_or_srt(text)
    return []


def _matching_language(
    manual: dict,
    automatic: dict,
    preferred: str | None,
    source: str | None,
) -> str | None:
    languages = sorted(set(manual) | set(automatic))
    if not languages:
        return None
    for candidate in (preferred, source):
        if not candidate:
            continue
        normalized = candidate.lower().replace("_", "-")
        exact = next((item for item in languages if item.lower().replace("_", "-") == normalized), None)
        if exact:
            return exact
        base = normalized.split("-", 1)[0]
        prefix = next((item for item in languages if item.lower().split("-", 1)[0] == base), None)
        if prefix:
            return prefix
    return next(iter(manual), None) or next(iter(automatic), None)


def _choose_format(formats: list[dict]) -> dict | None:
    usable = [
        item for item in formats
        if isinstance(item, dict) and str(item.get("ext") or "").lower() in SUPPORTED_SUBTITLE_FORMATS
        and isinstance(item.get("url"), str)
    ]
    return next(
        (
            item for extension in SUPPORTED_SUBTITLE_FORMATS
            for item in usable if str(item.get("ext") or "").lower() == extension
        ),
        None,
    )


def _download_caption(ydl, track: dict) -> bytes:
    response = ydl.urlopen(track["url"])
    try:
        data = response.read(MAX_SUBTITLE_BYTES + 1)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if len(data) > MAX_SUBTITLE_BYTES:
        raise ValueError(f"Subtitle exceeds {MAX_SUBTITLE_BYTES} bytes")
    return data


def _safe_subtitle_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return False
    return True


def extract_public_subtitles(
    source_url: str,
    preferred_language: str | None = None,
) -> dict:
    youtube_dl, _extractors = _load_yt_dlp()
    if youtube_dl is None:
        return {"status": "extractor_unavailable", "cues": [], "error": "yt-dlp is not installed"}

    options = {
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }
    try:
        with youtube_dl(options) as ydl:
            info = ydl.extract_info(source_url, download=False)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                return {"status": "not_single_video", "cues": []}
            manual = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
            automatic = info.get("automatic_captions") if isinstance(info.get("automatic_captions"), dict) else {}
            available = sorted(set(manual) | set(automatic))
            selected_language = _matching_language(
                manual,
                automatic,
                preferred_language,
                str(info.get("language") or ""),
            )
            track_source = manual if selected_language in manual else automatic
            track = _choose_format(track_source.get(selected_language, [])) if selected_language else None
            if track is None:
                selected_language = _matching_language(
                    manual,
                    automatic,
                    preferred_language,
                    str(info.get("language") or ""),
                )
                track_source = manual if selected_language in manual else automatic
                track = _choose_format(track_source.get(selected_language, [])) if selected_language else None
            if track is None:
                return {
                    "status": "no_subtitles",
                    "cues": [],
                    "title": str(info.get("title") or ""),
                    "duration": info.get("duration"),
                    "available_languages": available[:64],
                }
            if not _safe_subtitle_url(str(track["url"])):
                return {
                    "status": "unsafe_subtitle_url",
                    "cues": [],
                    "title": str(info.get("title") or ""),
                    "language": selected_language,
                    "error": "Subtitle URL contains credentials or query parameters",
                }
            data = _download_caption(ydl, track)
            cues = parse_subtitle_document(data, str(track.get("ext") or ""))
            if not cues:
                return {
                    "status": "subtitle_parse_failed",
                    "cues": [],
                    "title": str(info.get("title") or ""),
                    "duration": info.get("duration"),
                    "language": selected_language,
                }
            return {
                "status": "ready",
                "video_id": str(info.get("id") or ""),
                "title": str(info.get("title") or ""),
                "duration": info.get("duration"),
                "language": selected_language,
                "automatic": selected_language not in manual,
                "available_languages": available[:64],
                "cues": cues,
            }
    except Exception as exc:
        return {
            "status": "failed",
            "cues": [],
            "error": _safe_error(exc),
        }
