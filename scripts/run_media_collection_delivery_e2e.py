#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from probe_media_collection import probe_media_collection


def _build_batch_plan(collection_probe: dict, max_videos: int):
    usable = collection_probe.get("usable_inputs") if isinstance(collection_probe.get("usable_inputs"), list) else []
    rejected = collection_probe.get("rejected_inputs") if isinstance(collection_probe.get("rejected_inputs"), list) else []
    limit = max(int(max_videos or 1), 1)
    planned = usable[:limit]
    return {
        "schema_version": "envoy.media.collection_delivery_plan.v1",
        "status": "ready" if planned else "failed",
        "summary": {
            "usable_videos": len(usable),
            "planned_videos": len(planned),
            "skipped_usable_videos": max(len(usable) - len(planned), 0),
            "rejected_videos": len(rejected),
        },
        "planned_inputs": planned,
        "rejected_inputs": rejected,
    }


def _run_video_delivery(video_path: str, out_dir: Path, database_url: str, min_duration_seconds: float):
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "run_large_video_delivery_e2e.py"),
            "--video",
            video_path,
            "--out-dir",
            str(out_dir),
            "--database-url",
            database_url,
            "--min-duration-seconds",
            str(min_duration_seconds),
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"status": "failed", "reason": "invalid_child_json", "stdout": completed.stdout, "stderr": completed.stderr}
    return {
        "video": video_path,
        "returncode": completed.returncode,
        "result": payload,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Probe a media folder and run delivery e2e for usable videos.")
    parser.add_argument("media_dir", help="Directory containing media files.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "media-collection-delivery"), help="Output directory for generated packages.")
    parser.add_argument("--database-dir", default=str(ROOT / "tmp"), help="Directory for per-video sqlite databases.")
    parser.add_argument("--max-videos", type=int, default=1, help="Maximum usable videos to process in this acceptance run.")
    parser.add_argument("--min-duration-seconds", type=float, default=float(os.getenv("VIDEO_MIN_DURATION_SECONDS", "60")))
    parser.add_argument("--plan-only", action="store_true", help="Only write the batch plan; do not run video delivery.")
    args = parser.parse_args(argv)

    media_dir = Path(args.media_dir)
    collection_probe = probe_media_collection(media_dir)
    plan = _build_batch_plan(collection_probe, args.max_videos)
    if plan["status"] != "ready":
        print(json.dumps({
            "status": "failed",
            "reason": "no_usable_media",
            "collection_probe": collection_probe,
            "plan": plan,
        }, ensure_ascii=False, indent=2))
        return 1
    if args.plan_only:
        print(json.dumps({
            "status": "planned",
            "collection_probe": collection_probe,
            "plan": plan,
        }, ensure_ascii=False, indent=2))
        return 0

    out_dir = Path(args.out_dir)
    database_dir = Path(args.database_dir)
    database_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, item in enumerate(plan["planned_inputs"], start=1):
        db_path = database_dir / f"media-collection-delivery-{index}.db"
        if db_path.exists():
            db_path.unlink()
        results.append(_run_video_delivery(
            str(item["path"]),
            out_dir / f"video-{index}",
            f"sqlite:///{db_path}",
            args.min_duration_seconds,
        ))

    failed_results = [item for item in results if item["returncode"] != 0 or (item.get("result") or {}).get("status") != "ok"]
    print(json.dumps({
        "status": "ok" if not failed_results else "failed",
        "collection_probe": collection_probe,
        "plan": plan,
        "results": results,
        "failed_count": len(failed_results),
    }, ensure_ascii=False, indent=2))
    return 0 if not failed_results else 1


if __name__ == "__main__":
    raise SystemExit(main())
