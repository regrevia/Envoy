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
RUNLOG_DIRS=()
RUNLOGS=()
for shard in "$AB_DIR"/s* "$AB_DIR"/plan-s*; do
  [ -d "$shard" ] || continue
  [ -f "$shard/run-log.jsonl" ] || continue
  ROOTS+=(--root "$shard")
  # ab_usage_records.py takes shard directories (it appends /run-log.jsonl);
  # ab_report.py takes the log files themselves. Feeding the same file paths to
  # both silently made the ledger see zero turns.
  RUNLOG_DIRS+=(--runlog "$shard")
  RUNLOGS+=(--runlog "$shard/run-log.jsonl")
done

python3 "$ROOT/ab_telemetry.py" "${ROOTS[@]}" --out "$OUT/telemetry.jsonl"

ANSWER_ARGS=()
for shard in "$AB_DIR"/s* "$AB_DIR"/plan-s*; do
  [ -f "$shard/answers.jsonl" ] || continue
  ANSWER_ARGS+=(--answers "$shard/answers.jsonl")
done
# Turns that printed no final text still left text in the transcript: a capped
# turn, or one that stopped after a tool call. Recovering it keeps the quality
# denominator honest, and every recovered row is labelled by source.
BACKFILL_ARGS=()
for shard in "$AB_DIR"/s* "$AB_DIR"/plan-s*; do
  [ -f "$shard/answers.jsonl" ] || continue
  BACKFILL_ARGS+=(--shard "$shard")
done
python3 "$ROOT/backfill_answers.py" "${BACKFILL_ARGS[@]}" --out "$OUT/answers-recovered.jsonl" \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(json.dumps({'answers_recovered': r['recovered']}))"
python3 "$ROOT/ab_grade.py" "${ANSWER_ARGS[@]}" --answers "$OUT/answers-recovered.jsonl" \
  --out "$OUT/grade.jsonl"

T0=$(python3 - "$AB_DIR" <<'PY'
import json, sys, pathlib
stamps = []
for path in pathlib.Path(sys.argv[1]).glob("*/run-log.jsonl"):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            stamps.append(json.loads(line)["started_at"])
print(min(stamps))
PY
)
T1=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)

psql -h 127.0.0.1 -p 5433 -U apple -d litellm \
  -v run_id="$RUN_ID" -v t0="$T0" -v t1="$T1" -tA \
  -f "$ROOT/export_ledger_window.sql" > "$OUT/run-ledger.ndjson"

python3 "$ROOT/ab_usage_records.py" "${RUNLOG_DIRS[@]}" --ledger "$OUT/run-ledger.ndjson" \
  --out "$OUT/usage-records.ndjson"

# The ledger gate exits 2 while the sweep is still short of 120 turns. That is
# a state of the run, not a broken pipeline, so the reason is recorded and the
# chain continues: the report and the four questions already refuse conclusions
# when the ledger is not valid.
set +e
python3 "$ROOT/../usage_ledger.py" --plan "$ROOT/plan.json" \
  --ledger "$OUT/usage-records.ndjson" --server-export-complete \
  --output "$OUT/usage-ledger.json"
LEDGER_RC=$?
set -e
python3 - "$OUT/usage-ledger.json" "$LEDGER_RC" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({
    "usage_ledger_exit": int(sys.argv[2]),
    "valid": report["valid"],
    "reason": report.get("invalid_reason") or report.get("errors")
    or {"observed_turn_count": report["observed_turn_count"],
        "missing_turn_count": report["missing_turn_count"]},
    "cache_isolation_status": report["cache_isolation_status"],
}, ensure_ascii=False))
PY

python3 "$ROOT/ab_report.py" --ledger "$OUT/run-ledger.ndjson" "${RUNLOGS[@]}" \
  --telemetry "$OUT/telemetry.jsonl" --grade "$OUT/grade.jsonl" --run-id "$RUN_ID" \
  --out "$OUT/summary.json" --markdown "$OUT/summary.md"

python3 "$ROOT/four_questions.py" --report "$OUT" --markdown "$OUT/four-questions.md"

echo "--- $OUT/summary.md"
cat "$OUT/summary.md"

echo "--- $OUT/four-questions.md"
cat "$OUT/four-questions.md"
