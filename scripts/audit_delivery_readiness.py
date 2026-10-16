#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_delivery_runtime import check_runtime
from probe_media_collection import probe_media_collection


def _run_json(command: list[str]):
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"status": "failed", "reason": "invalid_json", "stdout": completed.stdout, "stderr": completed.stderr}
    return completed.returncode, payload


def _build_readiness_report(runtime: dict, delivery_validator: dict, media_collection: Optional[dict], evidence: dict):
    runtime_ok = runtime.get("status") == "ok" and bool((runtime.get("capabilities") or {}).get("long_video_delivery_e2e"))
    package_ok = (
        delivery_validator.get("status") == "ok"
        and int(delivery_validator.get("target_profile_count") or 0) >= 4
        and int(delivery_validator.get("chunk_count") or 0) > 0
    )
    media_summary = (media_collection or {}).get("summary") if isinstance(media_collection, dict) else {}
    real_video_ok = media_collection is None or int((media_summary or {}).get("usable_files") or 0) > 0
    requirements = {
        "runtime": {
            "ok": runtime_ok,
            "evidence": runtime,
        },
        "importable_delivery_package": {
            "ok": package_ok,
            "evidence": delivery_validator,
        },
        "real_video_acceptance": {
            "ok": real_video_ok,
            "evidence": media_collection,
        },
    }
    blockers = []
    if not runtime_ok:
        blockers.append({"code": "runtime_not_ready", "message": "Required media runtime capabilities are not available."})
    if not package_ok:
        blockers.append({"code": "delivery_package_invalid", "message": "Generated delivery package did not validate as importable."})
    if media_collection is not None and not real_video_ok:
        blockers.append({
            "code": "no_usable_real_video",
            "message": "The supplied media directory has no ffmpeg-decodable video that can produce transcript or OCR inputs.",
        })
    return {
        "schema_version": "envoy.delivery.readiness.v1",
        "status": "ok" if not blockers else "partial",
        "requirements": requirements,
        "blockers": blockers,
        "evidence": evidence,
    }


def main(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="Audit delivery-package readiness for commercial long-file workflows.")
    parser.add_argument("--media-dir", help="Optional real media directory to probe for long-video acceptance.")
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "readiness-delivery-e2e"), help="Output directory for smoke delivery package.")
    args = parser.parse_args(argv)

    runtime = check_runtime()
    delivery_code, delivery_payload = _run_json([
        sys.executable,
        str(SCRIPTS / "run_delivery_package_e2e.py"),
        "--out-dir",
        args.out_dir,
        "--task-id",
        "delivery-readiness-audit",
    ])
    validator = delivery_payload.get("validator") if isinstance(delivery_payload.get("validator"), dict) else {"status": "failed", "returncode": delivery_code}
    media_collection = probe_media_collection(Path(args.media_dir)) if args.media_dir else None
    report = _build_readiness_report(
        runtime=runtime,
        delivery_validator=validator,
        media_collection=media_collection,
        evidence={
            "delivery_package": delivery_payload.get("package_root"),
            "delivery_e2e_status": delivery_payload.get("status"),
            "media_dir": args.media_dir,
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
