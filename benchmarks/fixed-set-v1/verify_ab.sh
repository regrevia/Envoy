#!/usr/bin/env bash
# Rebuild the whole A/B evidence chain from the shard directories.
# Usage: bash verify_ab.sh /tmp/AB3 envoy-ab-20261025-ff0ae997d591
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
AB_DIR="${1:?usage: verify_ab.sh <ab-dir> <run-id>}"
RUN_ID="${2:?usage: verify_ab.sh <ab-dir> <run-id>}"
OUT="$AB_DIR/report"
mkdir -p "$OUT"

ROOTS=()
RUNLOGS=()
for shard in "$AB_DIR"/s*; do
  [ -d "$shard" ] || continue
  [ -f "$shard/run-log.jsonl" ] || continue
  ROOTS+=(--root "$shard")
  RUNLOGS+=(--runlog "$shard/run-log.jsonl")
done

python3 "$ROOT/ab_telemetry.py" "${ROOTS[@]}" --out "$OUT/telemetry.jsonl"

ANSWER_ARGS=()
for shard in "$AB_DIR"/s*; do
  [ -f "$shard/answers.jsonl" ] || continue
  ANSWER_ARGS+=(--answers "$shard/answers.jsonl")
done
python3 "$ROOT/ab_grade.py" "${ANSWER_ARGS[@]}" --out "$OUT/grade.jsonl"

T0=$(python3 - "$AB_DIR" <<'PY'
import json, sys, pathlib
stamps = []
for path in pathlib.Path(sys.argv[1]).glob("s*/run-log.jsonl"):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            stamps.append(json.loads(line)["started_at"])
print(min(stamps))
PY
)
T1=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)

psql -h 127.0.0.1 -p 5433 -U apple -d litellm \
  -v run_id="$RUN_ID" -v t0="$T0" -v t1="$T1" -tA \
  -f $HOME/envoy-ledger-gateway/export_ledger.sql > "$OUT/run-ledger.ndjson"

python3 "$ROOT/ab_report.py" --ledger "$OUT/run-ledger.ndjson" "${RUNLOGS[@]}" \
  --telemetry "$OUT/telemetry.jsonl" --grade "$OUT/grade.jsonl" \
  --out "$OUT/summary.json" --markdown "$OUT/summary.md"

echo "--- $OUT/summary.md"
cat "$OUT/summary.md"
