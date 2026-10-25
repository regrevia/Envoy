#!/usr/bin/env python3
"""Turn the A/B run logs plus the server ledger into the strict NDJSON that
`usage_ledger.py` validates.

Two record types are expected by the ledger checker:
  * `request_usage` -- one per upstream request, server-side token counts only;
  * `turn`          -- one per (package, arm, role), listing the server request
                       ids that belong to it and its outcome.

Keeping this as a separate step means the checker stays strict: the export used
for `ab_report.py` carries extra diagnostics (`status`, `error_class`,
`local_request_id`) that the ledger schema deliberately refuses, so those fields
are dropped here instead of loosening the checker.

Turn selection mirrors `ab_report.py`: a turn interrupted by an infrastructure
error may have several attempts, and the best attempt wins (completed > capped >
failed). Every attempt shares one turn_correlation_id, so the ledger rows of all
attempts stay attached to that turn; only the winning attempt defines the
outcome and the wall time.

Usage:
    python3 ab_usage_records.py --runlog /tmp/AB3/s0 ... --ledger led.ndjson \
        --out usage-records.ndjson
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CAP_SECONDS = 900
RANK = {0: 0, 124: 1}

REQUEST_FIELDS = (
    "server_request_id",
    "run_id",
    "package_id",
    "arm",
    "session_id",
    "turn_correlation_id",
    "provider",
    "model",
    "started_at",
    "completed_at",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "salt_sha256",
)


def rank(exit_code: int) -> int:
    return RANK.get(exit_code, 2)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else parsed.replace(tzinfo=timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def outcome_of(exit_code: int, is_error: bool) -> str:
    if exit_code == 0 and not is_error:
        return "completed"
    if exit_code == 124:
        return "blocked"
    return "failed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runlog", action="append", required=True, help="shard directory holding run-log.jsonl")
    parser.add_argument("--ledger", required=True, help="rich ledger export (*.ndjson) from export_ledger_window.sql")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    attempts: dict[tuple[str, str, str], list[dict]] = {}
    for root in args.runlog:
        log = Path(root) / "run-log.jsonl"
        if not log.is_file():
            continue
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["package_id"], row["arm"], row["role"])
            attempts.setdefault(key, []).append(row)

    winners: dict[tuple[str, str, str], dict] = {}
    replaced = 0
    for key, rows in attempts.items():
        rows.sort(key=lambda row: (rank(int(row.get("exit_code") or 0)), -(parse_ts(row.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()))
        winners[key] = rows[0]
        replaced += len(rows) - 1

    requests_by_turn: dict[str, list[dict]] = {}
    request_span: dict[str, tuple[datetime, datetime]] = {}
    request_records: list[dict] = []
    for line in Path(args.ledger).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "failure" or not row.get("server_request_id"):
            continue
        record = {"record_type": "request_usage"}
        for field in REQUEST_FIELDS:
            if row.get(field) is not None:
                record[field] = row[field]
        record["input_tokens"] = int(record.get("input_tokens") or 0)
        record["output_tokens"] = int(record.get("output_tokens") or 0)
        record_records_ok = all(field in record for field in ("run_id", "package_id", "arm", "session_id", "turn_correlation_id", "started_at"))
        if not record_records_ok:
            continue
        request_records.append(record)
        turn_id = record["turn_correlation_id"]
        requests_by_turn.setdefault(turn_id, []).append(record["server_request_id"])
        # A retried turn produces requests before its run-log row exists and
        # after the winning attempt timed out. The turn interval therefore has
        # to cover every correlated request, otherwise the ledger checker sees
        # requests that arrive "outside" their own turn.
        start_ts = parse_ts(record.get("started_at"))
        end_ts = parse_ts(record.get("completed_at")) or start_ts
        if start_ts and end_ts:
            low, high = request_span.get(turn_id, (start_ts, end_ts))
            request_span[turn_id] = (min(low, start_ts), max(high, end_ts))

    turn_records: list[dict] = []
    for (package_id, arm, role), row in sorted(winners.items()):
        started = parse_ts(row.get("started_at"))
        if started is None:
            continue
        # A turn that needed a second attempt owns the requests of both
        # attempts, so its interval has to span all of them. Using only the
        # winning attempt makes the earlier attempt's requests look like they
        # arrived after the turn ended.
        span = [parse_ts(item.get("started_at")) for item in attempts[(package_id, arm, role)]]
        ends = [
            (parse_ts(item.get("started_at")), float(item.get("elapsed_seconds") or 0))
            for item in attempts[(package_id, arm, role)]
        ]
        starts = [value for value in span if value is not None]
        started = min(starts)
        finished = max(start + timedelta(seconds=elapsed) for start, elapsed in ends if start is not None)
        turn_id = row["turn_correlation_id"]
        if turn_id in request_span:
            low, high = request_span[turn_id]
            started = min(started, low)
            finished = max(finished, high)
        elapsed = (finished - started).total_seconds()
        exit_code = int(row.get("exit_code") or 0)
        turn_records.append(
            {
                "record_type": "turn_summary",
                "run_id": row["run_id"],
                "package_id": package_id,
                "arm": arm,
                "session_id": row["session_id"],
                "turn_correlation_id": row["turn_correlation_id"],
                "turn_outcome": outcome_of(exit_code, bool(row.get("is_error"))),
                "server_request_ids": sorted(set(requests_by_turn.get(row["turn_correlation_id"], []))),
                "started_at": iso(started),
                "completed_at": iso(started + timedelta(seconds=elapsed)),
            }
        )

    out = Path(args.out)
    out.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in request_records + turn_records) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "turns": len(turn_records),
                "requests": len(request_records),
                "superseded_attempts": replaced,
                "turns_without_requests": sum(1 for t in turn_records if not t["server_request_ids"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
