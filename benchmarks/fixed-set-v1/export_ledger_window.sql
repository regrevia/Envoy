-- Export one A/B run window as newline-delimited JSON, one row per request.
--
-- Why this file exists instead of reusing the gateway's export_ledger.sql:
-- the gateway writes the *local wall clock* of this Mac into a `timestamptz`
-- column while its own session timezone is UTC, so every stored value is the
-- real instant plus the local offset (+08 here). Verified by joining a stored
-- row to its run-log row: the run-log (true UTC) says 06:20:27Z, the ledger row
-- renders as 14:20:27Z. Filtering on the raw column silently returns zero rows,
-- which reads like "the run is missing from the ledger" instead of a clock skew.
-- `started_at - interval '8 hours'` restores the true instant; the interval is
-- the machine's UTC offset and is asserted at export time by verify_ab.sh.
--
-- Usage:
--   psql -h 127.0.0.1 -p 5433 -U apple -d litellm \
--     -v run_id=... -v t0="2026-10-25T04:00:00+00:00" -v t1="2026-10-25T09:00:00+00:00" \
--     -tA -f export_ledger_window.sql > run-ledger.ndjson
SELECT json_build_object(
    'record_type', 'request_usage',
    'server_request_id', server_request_id,
    'local_request_id', local_request_id,
    'run_id', request_metadata->>'run_id',
    'package_id', request_metadata->>'package_id',
    'arm', request_metadata->>'arm',
    'session_id', request_metadata->>'session_id',
    'turn_correlation_id', request_metadata->>'turn_correlation_id',
    'provider', provider,
    'model', model,
    'status', status,
    'error_class', error_class,
    'started_at', to_char((started_at - interval '8 hours') at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'completed_at', to_char((completed_at - interval '8 hours') at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'input_tokens', prompt_tokens,
    'output_tokens', completion_tokens,
    'cache_read_input_tokens', cache_read_input_tokens,
    'cache_creation_input_tokens', cache_creation_input_tokens,
    'salt_sha256', salt_sha256
)::text
FROM envoy_request_ledger
WHERE request_metadata->>'run_id' = :'run_id'
  AND (started_at - interval '8 hours') >= :'t0'::timestamptz
  AND (started_at - interval '8 hours') <  :'t1'::timestamptz
ORDER BY started_at;
