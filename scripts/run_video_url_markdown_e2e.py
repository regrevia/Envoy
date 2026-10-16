#!/usr/bin/env python3
"""Submit a video URL to a running Envoy API and save the resulting Markdown."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(base_url: str, path: str, payload: dict | None = None, timeout: int = 30):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw.decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    return json.loads(raw.decode("utf-8")) if raw else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public video URL supported by yt-dlp")
    parser.add_argument("--project-id", help="Existing Envoy project ID; creates a test project if omitted")
    parser.add_argument("--api", default=os.getenv("ENVOY_API_BASE", "http://127.0.0.1:8140"))
    parser.add_argument("--timeout", type=int, default=21600, help="Maximum wait in seconds (default: 6 hours)")
    parser.add_argument("--output", type=Path, default=Path("video-output.md"))
    args = parser.parse_args()

    try:
        project_id = args.project_id
        if not project_id:
            project = request_json(args.api, "/api/v1/projects", {
                "name": f"video acceptance {int(time.time())}",
                "description": "URL-to-Markdown acceptance run",
                "domain_type": "video",
                "status": "active",
            })
            project_id = project["id"]

        task = request_json(args.api, "/api/v1/tasks/video-to-markdown", {
            "project_id": project_id,
            "source_url": args.url,
        })
        task_id = task["id"]
        print(f"task_id={task_id}", flush=True)
        started = time.monotonic()
        while True:
            task = request_json(args.api, f"/api/v1/tasks/{task_id}")
            summary = task.get("result_summary") or ""
            print(f"status={task.get('status')} progress={task.get('progress')} {summary}", flush=True)
            if task.get("status") in {"success", "failed", "canceled", "dispatch_failed"}:
                break
            if time.monotonic() - started > args.timeout:
                raise RuntimeError(f"Timed out waiting for task {task_id}; last task payload: {task}")
            time.sleep(3)

        if task.get("status") != "success":
            raise RuntimeError(task.get("error_message") or task.get("result_summary") or f"Task ended with status {task.get('status')}")

        checkpoints = (task.get("input_config") or {}).get("checkpoints") or {}
        artifact_id = (checkpoints.get("export") or {}).get("artifact_id")
        if not artifact_id:
            raise RuntimeError(f"Task succeeded without an export artifact checkpoint: {checkpoints}")
        request = Request(f"{args.api.rstrip('/')}/api/v1/artifacts/{artifact_id}/download")
        with urlopen(request, timeout=120) as response:
            markdown = response.read()
        if not markdown.strip():
            raise RuntimeError(f"Exported Markdown artifact is empty: artifact_id={artifact_id}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(markdown)
        print(f"artifact_id={artifact_id}")
        print(f"markdown={args.output.resolve()}")
        print(f"bytes={len(markdown)}")
        return 0
    except Exception as exc:
        print(f"ACCEPTANCE_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
