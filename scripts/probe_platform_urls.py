#!/usr/bin/env python3
"""Probe public video URLs without cookies or browser state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "envoy.platform-video-probe.v1"
AUTH_MARKERS = (
    "sign in",
    "login",
    "fresh cookies",
    "authentication",
    "not a bot",
    "captcha",
)
UNSUPPORTED_MARKERS = ("unsupported url", "no suitable extractor")
NETWORK_MARKERS = (
    "ssl",
    "certificate",
    "http error 403",
    "http error 429",
    "timeout",
    "eof occurred",
    "connection",
)


def _run_probe(command: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(int(timeout), 1),
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return 124, "", f"probe timed out after {timeout} seconds\n{stderr}".strip()
    except OSError as exc:
        return 1, "", str(exc)


def _compact_error(value: str, limit: int = 800) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] or "yt-dlp returned no diagnostic"


def _classify_error(message: str) -> str:
    lowered = message.lower()
    if any(marker in lowered for marker in AUTH_MARKERS):
        return "auth_required"
    if any(marker in lowered for marker in UNSUPPORTED_MARKERS):
        return "unsupported"
    if any(marker in lowered for marker in NETWORK_MARKERS):
        return "network_error"
    return "extractor_error"


def _languages(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value if str(key).strip())


def probe_url(url: str, timeout_seconds: int = 30) -> dict:
    command = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
        "--quiet",
        url,
    ]
    returncode, stdout, stderr = _run_probe(command, timeout_seconds)
    if returncode != 0:
        message = _compact_error(stderr or stdout)
        return {
            "url": url,
            "status": "blocked",
            "classification": _classify_error(message),
            "error": message,
            "timeout_seconds": timeout_seconds,
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {
            "url": url,
            "status": "blocked",
            "classification": "extractor_error",
            "error": _compact_error(f"yt-dlp returned invalid JSON: {exc}"),
            "timeout_seconds": timeout_seconds,
        }
    if not isinstance(payload, dict) or payload.get("_type") in {"playlist", "multi_video"}:
        return {
            "url": url,
            "status": "blocked",
            "classification": "unsupported",
            "error": "URL did not resolve to one video",
            "timeout_seconds": timeout_seconds,
        }
    subtitles = _languages(payload.get("subtitles"))
    automatic = _languages(payload.get("automatic_captions"))
    return {
        "url": url,
        "status": "accepted",
        "classification": "metadata",
        "extractor": str(payload.get("extractor_key") or payload.get("extractor") or ""),
        "video_id": str(payload.get("id") or ""),
        "title": str(payload.get("title") or ""),
        "duration_seconds": payload.get("duration"),
        "subtitle_languages": subtitles,
        "automatic_subtitle_languages": automatic,
        "has_subtitles": bool(subtitles or automatic),
        "timeout_seconds": timeout_seconds,
    }


def probe_urls(urls: Iterable[str], timeout_seconds: int = 30) -> dict:
    results = [probe_url(url, timeout_seconds=timeout_seconds) for url in urls]
    accepted = sum(item["status"] == "accepted" for item in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "cookies": False,
            "browser_profiles": False,
            "certificate_bypass": False,
            "anti_bot_bypass": False,
        },
        "summary": {
            "total": len(results),
            "accepted": accepted,
            "blocked": len(results) - accepted,
        },
        "results": results,
    }


def _read_urls(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--file", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    urls = list(args.url)
    if args.file:
        urls.extend(_read_urls(args.file))
    if not urls:
        parser.error("provide --url or --file")
    report = probe_urls(urls, timeout_seconds=args.timeout_seconds)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["blocked"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
