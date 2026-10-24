#!/bin/sh
# Resolve the Envoy CLI from PATH or the usual install locations and delegate
# to `envoy hook ...`. Hooks must fail open: if Envoy is missing we exit 0 with
# no output so the host prompt continues normally.
set -u

if [ -n "${ENVOY_BIN:-}" ] && [ -x "${ENVOY_BIN}" ]; then
  ENVOY="${ENVOY_BIN}"
elif command -v envoy >/dev/null 2>&1; then
  ENVOY="$(command -v envoy)"
else
  ENVOY=""
  for candidate in \
    "${HOME}/.local/bin/envoy" \
    "${HOME}/.cargo/bin/envoy" \
    /opt/homebrew/bin/envoy \
    /usr/local/bin/envoy; do
    if [ -x "${candidate}" ]; then
      ENVOY="${candidate}"
      break
    fi
  done
fi

if [ -z "${ENVOY}" ]; then
  exit 0
fi

"${ENVOY}" hook "$@" || exit 0
exit 0
