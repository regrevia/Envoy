from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

class VideoSourceError(RuntimeError):
    pass


SUPPORTED_BROWSERS = {"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}
BROWSER_TRY_ORDER = ("chrome", "edge", "firefox", "safari", "brave", "chromium")


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
    raise VideoSourceError("Video auth mode must be auto, public, or browser")


def _auth_args(auth: dict | None) -> list[str]:
    normalized = normalize_video_auth(auth)
    if normalized.get("mode") == "browser":
        return ["--cookies-from-browser", normalized["browser"]]
    return []


def _auth_candidates(auth: dict | None) -> list[dict]:
    normalized = normalize_video_auth(auth)
    mode = normalized.get("mode")
    if mode == "auto":
        return [{}, *({"mode": "browser", "browser": browser} for browser in BROWSER_TRY_ORDER)]
    if mode == "browser_auto":
        return [{"mode": "browser", "browser": browser} for browser in BROWSER_TRY_ORDER]
    return [normalized]


def _ytdlp_command() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    return [sys.executable, "-m", "yt_dlp"]


def validate_video_url(source_url: str) -> str:
    value = str(source_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VideoSourceError("Video URL must start with http:// or https://")
    if parsed.username or parsed.password:
        raise VideoSourceError("Video URL must not contain embedded credentials")
    return value


def _run_ytdlp(args: list[str], auth: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [*_ytdlp_command(), "--ignore-config", *_auth_args(auth), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except FileNotFoundError as exc:
        raise VideoSourceError("yt-dlp executable not found; install yt-dlp and retry") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise VideoSourceError(detail) from exc
    except subprocess.TimeoutExpired as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip() if isinstance(exc.stderr, bytes) else str(exc.stderr or "").strip()
        message = f"yt-dlp timed out after 3600 seconds"
        raise VideoSourceError(f"{message}: {detail}" if detail else message) from exc


def download_video(source_url: str, output_dir: str | Path, auth: dict | None = None) -> dict:
    url = validate_video_url(source_url)
    errors = []
    for candidate in _auth_candidates(auth):
        try:
            root = Path(output_dir).resolve()
            root.mkdir(parents=True, exist_ok=True)
            info_result = _run_ytdlp(["--dump-single-json", "--skip-download", "--no-playlist", url], candidate)
            try:
                info = json.loads(info_result.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise VideoSourceError(f"yt-dlp returned invalid metadata JSON: {info_result.stdout}") from exc

            video_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(info.get("id") or "video")).strip("._") or "video"
            output_template = str(root / f"{video_id}.%(ext)s")
            download_result = _run_ytdlp([
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
    raise VideoSourceError("\n\n".join(errors[-3:]) or "yt-dlp could not download the video")
