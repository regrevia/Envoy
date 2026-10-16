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


def _run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [*_ytdlp_command(), "--ignore-config", *args],
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


def download_video(source_url: str, output_dir: str | Path) -> dict:
    url = validate_video_url(source_url)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    info_result = _run_ytdlp(["--dump-single-json", "--skip-download", "--no-playlist", url])
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
    ])
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
