from __future__ import annotations

import html
import json
import re
import time
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from .media import (
    MediaUnsafeAddress,
    guard_yt_dlp_client,
    public_network_guard,
    run_bounded_worker,
)


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
        if start >= 0 and end > start and caption:
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


def _format_candidates(formats: list[dict]) -> list[dict]:
    usable = [
        item for item in formats
        if isinstance(item, dict)
        and str(item.get("ext") or "").lower() in SUPPORTED_SUBTITLE_FORMATS
        and isinstance(item.get("url"), str)
    ]
    return [
        item
        for extension in SUPPORTED_SUBTITLE_FORMATS
        for item in usable
        if str(item.get("ext") or "").lower() == extension
    ]


def _ordered_languages(
    manual: dict,
    automatic: dict,
    preferred: str | None,
    source: str | None,
) -> list[str]:
    all_languages = sorted(set(manual) | set(automatic))
    ordered = []
    for candidate in (preferred, source):
        if not candidate:
            continue
        normalized = candidate.lower().replace("_", "-")
        exact = next(
            (item for item in all_languages if item.lower().replace("_", "-") == normalized),
            None,
        )
        base = normalized.split("-", 1)[0]
        prefix = next(
            (item for item in all_languages if item.lower().replace("_", "-").split("-", 1)[0] == base),
            None,
        )
        for item in (exact, prefix):
            if item and item not in ordered:
                ordered.append(item)
    for item in (*manual.keys(), *automatic.keys()):
        if item not in ordered:
            ordered.append(item)
    return ordered


def _download_caption(
    ydl,
    track: dict,
    max_bytes: int,
    deadline: float,
) -> bytes:
    response = ydl.urlopen(track["url"])
    try:
        data = response.read(max_bytes + 1)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if time.monotonic() > deadline:
        raise TimeoutError("Subtitle download exceeded max_work_seconds")
    if len(data) > max_bytes:
        raise ValueError(f"Subtitle exceeds {max_bytes} bytes")
    return data


def _safe_subtitle_url(value: str) -> bool:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return False
    return True


def extract_public_subtitles(
    source_url: str,
    preferred_language: str | None = None,
    max_download_bytes: int = MAX_SUBTITLE_BYTES,
    max_work_seconds: int = 30,
    public_url_validator=None,
) -> dict:
    work_limit = max(int(max_work_seconds), 1)
    try:
        return run_bounded_worker(
            _extract_public_subtitles_worker,
            source_url,
            preferred_language,
            max_download_bytes,
            work_limit,
            None,
            max_work_seconds=work_limit,
        )
    except MediaUnsafeAddress:
        return {
            "status": "unsafe_target",
            "cues": [],
            "error": "Media extractor resolved a non-public network address",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "cues": [],
            "error": _safe_error(exc),
        }


def _extract_public_subtitles_worker(
    source_url: str,
    preferred_language: str | None,
    max_download_bytes: int,
    max_work_seconds: int,
    public_url_validator=None,
) -> dict:
    download_limit = min(MAX_SUBTITLE_BYTES, max(int(max_download_bytes), 0))
    if download_limit <= 0:
        return {"status": "budget_exceeded", "cues": [], "error": "Subtitle download budget is zero"}
    youtube_dl, _extractors = _load_yt_dlp()
    if youtube_dl is None:
        return {"status": "extractor_unavailable", "cues": [], "error": "yt-dlp is not installed"}

    work_limit = max(int(max_work_seconds), 1)
    started = time.monotonic()
    deadline = started + work_limit
    options = {
        "ignoreconfig": True,
        "compat_opts": ["prefer-legacy-http-handler"],
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": min(work_limit, 5),
        "retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
    }
    try:
        with public_network_guard():
            with youtube_dl(options) as ydl:
                guard_yt_dlp_client(ydl)
                info = ydl.extract_info(source_url, download=False)
                if time.monotonic() >= deadline:
                    return {
                        "status": "budget_exceeded",
                        "cues": [],
                        "error": "Subtitle extraction exceeded max_work_seconds",
                    }
                if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                    return {"status": "not_single_video", "cues": []}
                manual = info.get("subtitles") if isinstance(info.get("subtitles"), dict) else {}
                automatic = info.get("automatic_captions") if isinstance(info.get("automatic_captions"), dict) else {}
                available = sorted(set(manual) | set(automatic))
                candidates = []
                for language in _ordered_languages(
                    manual,
                    automatic,
                    preferred_language,
                    str(info.get("language") or ""),
                ):
                    for source_name, tracks in (("manual", manual), ("automatic", automatic)):
                        for track in _format_candidates(tracks.get(language, [])):
                            candidates.append((language, source_name, track))
                if not candidates:
                    status = "unsupported_subtitle_format" if available else "no_subtitles"
                    return {
                        "status": status,
                        "cues": [],
                        "title": str(info.get("title") or ""),
                        "duration": info.get("duration"),
                        "available_languages": available[:64],
                        "error": "Subtitle tracks exist, but none use VTT, SRT, or JSON3" if available else "",
                    }
                selected_language = None
                selected_source = None
                track = None
                cues = []
                track_errors = []
                for candidate_language, candidate_source, candidate_track in candidates:
                    candidate_url = str(candidate_track["url"])
                    if not _safe_subtitle_url(candidate_url):
                        track_errors.append("unsafe subtitle URL")
                        continue
                    if public_url_validator and not public_url_validator(candidate_url):
                        track_errors.append("subtitle URL is not public HTTP(S)")
                        continue
                    if time.monotonic() >= deadline:
                        return {
                            "status": "budget_exceeded",
                            "cues": [],
                            "error": "Subtitle extraction exceeded max_work_seconds",
                        }
                    try:
                        data = _download_caption(ydl, candidate_track, download_limit, deadline)
                        parsed = parse_subtitle_document(data, str(candidate_track.get("ext") or ""))
                    except MediaUnsafeAddress:
                        raise
                    except Exception as exc:
                        track_errors.append(_safe_error(exc))
                        continue
                    if not parsed:
                        track_errors.append("subtitle track parsed without cues")
                        continue
                    selected_language = candidate_language
                    selected_source = candidate_source
                    track = candidate_track
                    cues = parsed
                    break
                if track is None:
                    return {
                        "status": "subtitle_tracks_unavailable",
                        "cues": [],
                        "title": str(info.get("title") or ""),
                        "duration": info.get("duration"),
                        "available_languages": available[:64],
                        "error": "; ".join(track_errors[:3]) or "No usable public subtitle track",
                    }
                return {
                    "status": "ready",
                    "video_id": str(info.get("id") or ""),
                    "title": str(info.get("title") or ""),
                    "duration": info.get("duration"),
                    "language": selected_language,
                    "automatic": selected_source == "automatic",
                    "available_languages": available[:64],
                    "cues": cues,
                }
    except MediaUnsafeAddress:
        return {
            "status": "unsafe_target",
            "cues": [],
            "error": "Media extractor resolved a non-public network address",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "cues": [],
            "error": _safe_error(exc),
        }
