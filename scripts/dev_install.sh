#!/usr/bin/env bash
# Install Envoy from this checkout for development, in one command.
#
#   scripts/dev_install.sh              # .venv in the repo root, Python 3.11
#   ENVOY_DEV_PYTHON=3.12 scripts/dev_install.sh
#   ENVOY_DEV_VENV=/tmp/envoy-dev scripts/dev_install.sh
#
# Source installs need both packages: envoy-cli imports envoy-core, and
# envoy-core is not on PyPI, so `pip install -e packages/envoy-cli` alone cannot
# resolve. The CLI also requires Python >= 3.10, which the system python3 on
# macOS is not; uv fetches a suitable interpreter instead of failing late.
set -euo pipefail

cd "$(dirname "$0")/.."

PY_VERSION="${ENVOY_DEV_PYTHON:-3.11}"
VENV="${ENVOY_DEV_VENV:-$PWD/.venv}"
CORE="$PWD/packages/envoy-core"
CLI="$PWD/packages/envoy-cli"

die() { printf 'dev_install: %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || die "uv is required: brew install uv, or install it from astral.sh/uv"

uv venv --quiet --python "$PY_VERSION" "$VENV" || die "could not create a Python $PY_VERSION venv"
uv pip install --quiet --python "$VENV/bin/python" --editable "$CORE" --editable "$CLI"
"$VENV/bin/envoy" --version

printf '\nUse it:   source %s/bin/activate && envoy --help\n' "$VENV"
printf 'Test it:  %s/bin/envoy read --request-json -\n' "$VENV"
