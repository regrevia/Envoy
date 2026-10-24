#!/usr/bin/env bash
# One-command local verification for the Envoy workspace.
#
#   scripts/dev_test.sh          lint + repo consistency checks + the green test baseline
#   scripts/dev_test.sh --full   also run the whole tests/ tree (records known backend failures)
#
# The baseline is the set of suites that run without the backend web app's
# optional dependencies; `--full` surfaces the rest without pretending they pass.
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-baseline}"
PY_VERSION="${ENVOY_DEV_PYTHON:-3.11}"
BASELINE=(
  tests/cli
  tests/core_protocol
  tests/test_agent_product_eval.py
  tests/test_benchmark_manifest.py
  tests/test_dsh_plugin.py
  tests/test_link_routing_hooks.py
  tests/test_ocr_quality_eval.py
  tests/test_paired_experiment_plan.py
)

step() { printf '\n== %s ==\n' "$1"; }

step "lint (ruff)"
if command -v uvx >/dev/null 2>&1; then
  uvx ruff@0.17.2 check .
else
  python -m ruff check .
fi

step "repo consistency (generated artifacts are up to date)"
python scripts/build_envoy_dsh_plugin.py --check
python scripts/sync_host_skills.py --check
python scripts/check_versions.py --check

step "tests (baseline: ${#BASELINE[@]} targets)"
uv run --python "${PY_VERSION}" \
  --with pytest --with httpx --with pyyaml --with pillow --with reportlab \
  --with-editable packages/envoy-core --with-editable packages/envoy-cli \
  pytest -q "${BASELINE[@]}"

if [ "${MODE}" = "--full" ]; then
  step "tests (full tests/ tree; backend web-app suites need deps this repo does not ship)"
  set +e
  uv run --python "${PY_VERSION}" \
    --with pytest --with httpx --with pyyaml --with pillow --with reportlab \
    --with-editable packages/envoy-core --with-editable packages/envoy-cli \
    pytest -q tests 2>&1 | tail -5
  status=${PIPESTATUS[0]}
  set -e
  if [ "${status}" -ne 0 ]; then
    printf '\nfull tree exit=%s: expected for tests needing the backend app; baseline above is the gate\n' "${status}"
  fi
fi

step "done"
