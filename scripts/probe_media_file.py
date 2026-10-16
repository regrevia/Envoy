#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


SCHEMA_VERSION = "envoy.media.probe.v1"


def _configured_binary(tool: str):
    env_name = {
        "ffmpeg": "FFMPEG_BIN",
        "ffprobe": "FFPROBE_BIN",
    }.get(tool)
    return os.getenv(env_name or "") or tool


def _resolve_binary(command: str):
    if os.path.sep in command:
        return command if Path(command).is_file() and os.access(command, os.X_OK) else None
    return shutil.which(command)


def _compact_error(value, max_chars: int = 600):
    text = str(value or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    compact = []
    previous = None
    repeated = 0
    for line in lines:
        if line == previous:
            repeated += 1
            continue
        if repeated:
            compact.append(f"... repeated previous line {repeated} time(s)")
            repeated = 0
        compact.append(line)
        previous = line
    if repeated:
        compact.append(f"... repeated previous line {repeated} time(s)")
    result = "\n".join(compact) if compact else text
    if len(result) <= max_chars:
        return result
    suffix = f"\n... omitted {len(result) - max_chars} chars"
    return result[: max(0, max_chars - len(suffix))].rstrip() + suffix


def _run(command: list[str], timeout: int):
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, "", f"command timed out after {timeout} sec\n{stderr}".strip()
    except Exception as exc:
        return 1, "", str(exc)


def _probe_metadata(video_path: Path, ffprobe_bin: str):
    code, stdout, stderr = _run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name",
            "-of",
            "json",
            str(video_path),
        ],
        timeout=15,
    )
    if code != 0:
        return None, _compact_error(stderr)
    try:
        return json.loads(stdout or "{}"), None
    except json.JSONDecodeError as exc:
        return None, _compact_error(f"invalid ffprobe json: {exc}")


def _decode_check(name: str, command: list[str], output_path: Path, min_bytes: int):
    code, _stdout, stderr = _run(command, timeout=20)
    ok = code == 0 and output_path.exists() and output_path.stat().st_size >= min_bytes
    return {
        "name": name,
        "ok": ok,
        "returncode": code,
        "output_bytes": output_path.stat().st_size if output_path.exists() else 0,
        "error": "" if ok else _compact_error(stderr),
    }


def probe_media_file(video_path: Path):
    if not video_path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "missing_file",
            "path": str(video_path),
        }

    ffmpeg_bin = _resolve_binary(_configured_binary("ffmpeg"))
    ffprobe_bin = _resolve_binary(_configured_binary("ffprobe"))
    missing = [name for name, path in (("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)) if not path]
    if missing:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "missing_media_dependency",
            "path": str(video_path),
            "missing_dependencies": missing,
        }

    metadata, metadata_error = _probe_metadata(video_path, ffprobe_bin)
    if metadata is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "metadata_probe_failed",
            "path": str(video_path),
            "error": metadata_error,
        }

    streams = metadata.get("streams") if isinstance(metadata.get("streams"), list) else []
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    try:
        duration_seconds = float((metadata.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration_seconds = 0.0

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        audio_output = tmp / "probe-audio.wav"
        frame_output = tmp / "probe-frame.jpg"
        audio_check = _decode_check(
            "audio_decode",
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(video_path),
                "-t",
                "5",
                "-vn",
                "-acodec",
                "pcm_s16le",
                str(audio_output),
            ],
            audio_output,
            min_bytes=4096,
        )
        frame_check = _decode_check(
            "frame_decode",
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-ss",
                "5",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_output),
            ],
            frame_output,
            min_bytes=1,
        )

    can_extract_text = bool(audio_check["ok"] or frame_check["ok"])
    failed_checks = [name for name, check in (("audio_decode", audio_check), ("frame_decode", frame_check)) if not check["ok"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if can_extract_text else "failed",
        "reason": None if can_extract_text else "media_text_unavailable",
        "path": str(video_path),
        "duration_seconds": duration_seconds,
        "format_name": (metadata.get("format") or {}).get("format_name"),
        "streams": {
            "audio": audio_streams,
            "video": video_streams,
        },
        "checks": {
            "audio_decode": audio_check,
            "frame_decode": frame_check,
        },
        "can_extract_text": can_extract_text,
        "failed_checks": failed_checks,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Probe whether a media file can produce transcript or OCR inputs.")
    parser.add_argument("media", help="Media file path.")
    args = parser.parse_args(argv)
    result = probe_media_file(Path(args.media))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
