#!/bin/sh
# Offline checks for the one-command installer: runtime pinning and host
# auto-detection, without downloading anything (ENVOY_DRY_RUN=1).
#
# Every case runs in a throwaway HOME and PATH. A fake `envoy` on PATH is the
# regression that matters: an older binary earlier in PATH used to win, and the
# plugin then spawned a runtime the installer never installed.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL="$ROOT/integrations/install.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM
FAILED=0
CASES=0

ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1" >&2; FAILED=$((FAILED + 1)); }
case_start() { CASES=$((CASES + 1)); }

fake_envoy() {
  # $1 path, $2 version
  mkdir -p "$(dirname "$1")"
  cat > "$1" <<EOF
#!/bin/sh
case "\$1" in
  --version) echo "envoy $2" ;;
  doctor) printf '{"cli_version":"%s","protocol_version":"1","runtime":{"browser":{"ready":false},"install":{"status":"%s","pinned_runtime":"%s","path_envoy":null}}}\n' "$2" "\${FAKE_INSTALL_STATUS:-pinned}" "$1" ;;
  browser) echo "browser setup (fake)" ;;
  *) echo "fake envoy \$*" ;;
esac
EOF
  chmod +x "$1"
}

new_home() {
  home="$WORK/home-$1"
  mkdir -p "$home"
  printf '%s' "$home"
}

run_install() {
  home="$1"; shift
  HOME="$home" ENVOY_VERSION=0.1.23 ENVOY_DRY_RUN=1 ENVOY_SETUP_BROWSER="${SETUP_BROWSER:-0}" \
    ENVOY_DETECT_DESKTOP_APP="${DETECT_APP:-1}" \
    PATH="${CASE_PATH:-/usr/bin:/bin}" \
    sh "$INSTALL" "$@" 2>&1
}

# --- case: path with no runtime is a clear error, not a wrong guess ---------
case_start
home="$(new_home nopath)"
set +e
out="$(run_install "$home" path)"
status=$?
set -e
if [ "$status" -ne 0 ] && printf '%s' "$out" | grep -q 'no runtime installed yet'; then
  ok "path with no runtime exits with a message"
else
  bad "path with no runtime (status=$status, out=$out)"
fi

# --- case: a stale envoy on PATH does not win over the pinned runtime -------
case_start
home="$(new_home pinned)"
fake_envoy "$home/stale/bin/envoy" 0.1.0
fake_envoy "$home/runtime/bin/envoy" 0.1.23
mkdir -p "$home/.envoy"
printf '%s\n' "$home/runtime/bin/envoy" > "$home/.envoy/runtime-path"
CASE_PATH="$home/stale/bin:/usr/bin:/bin"
out="$(run_install "$home" mcp)"
if printf '%s' "$out" | grep -q "using envoy 0.1.23 from $home/runtime/bin/envoy (pinned)" \
  && ! printf '%s' "$out" | grep -q "using envoy 0.1.0"; then
  ok "pinned runtime wins over an older envoy earlier in PATH"
else
  bad "pinned runtime precedence: $out"
fi

# --- case: `path` prints the pinned binary even when PATH disagrees ---------
case_start
home="$(new_home pathprint)"
fake_envoy "$home/stale/bin/envoy" 0.1.0
fake_envoy "$home/runtime/bin/envoy" 0.1.23
mkdir -p "$home/.envoy"
printf '%s\n' "$home/runtime/bin/envoy" > "$home/.envoy/runtime-path"
CASE_PATH="$home/stale/bin:/usr/bin:/bin"
printed="$(run_install "$home" path)"
if [ "$printed" = "$home/runtime/bin/envoy" ]; then
  ok "path prints the pinned binary"
else
  bad "path printed '$printed'"
fi

# --- case: a finished MCP install reports runtime health ---------------------
case_start
home="$(new_home health)"
fake_envoy "$WORK/fake-runtime/envoy" 0.1.23
pybin="$WORK/pybin-health"
mkdir -p "$pybin"
ln -sf "$(command -v python3)" "$pybin/python3"
if output="$(HOME="$home" ENVOY_VERSION=0.1.23 ENVOY_DRY_RUN=0 ENVOY_SETUP_BROWSER=0 \
    ENVOY_RUNTIME_BIN="$WORK/fake-runtime/envoy" FAKE_INSTALL_STATUS=shadowed \
    PATH="$pybin:/usr/bin:/bin" sh "$INSTALL" mcp 2>&1)" \
  && printf '%s' "$output" | grep -q 'browser missing: envoy browser setup' \
  && printf '%s' "$output" | grep -q 'install state shadowed'; then
  ok "mcp install reports browser and install state"
else
  bad "mcp install health (out=$output)"
fi

# --- case: installing a runtime pins it and prints the browser step --------
case_start
home="$(new_home fresh)"
fake_envoy "$home/stale/bin/envoy" 0.1.0
CASE_PATH="$home/stale/bin:/usr/bin:/bin"
SETUP_BROWSER=1
out="$(run_install "$home" mcp)"
SETUP_BROWSER=0
if printf '%s' "$out" | grep -q "pin $home/.local/share/envoy-dsh/runtime/bin/envoy -> $home/.envoy/runtime-path" \
  && printf '%s' "$out" | grep -q 'browser setup'; then
  ok "fresh install pins the runtime and provisions the browser"
else
  bad "fresh install: $out"
fi

# --- case: no host argument installs every detected host -------------------
case_start
home="$(new_home auto)"
fake_envoy "$home/bin/envoy" 0.1.23
fake_envoy "$home/hosts/claude" 2.0.0
fake_envoy "$home/hosts/codex" 1.0.0
CASE_PATH="$home/bin:$home/hosts:/usr/bin:/bin"
out="$(run_install "$home")"
if printf '%s' "$out" | grep -q 'claude plugin install envoy@envoy' \
  && printf '%s' "$out" | grep -q 'codex plugin add envoy@envoy'; then
  ok "no-argument install covers the detected hosts"
else
  bad "no-argument auto install: $out"
fi

# --- case: no host CLI falls back to the MCP entry, not a failure ----------
case_start
home="$(new_home nohost)"
fake_envoy "$home/bin/envoy" 0.1.23
CASE_PATH="$home/bin:/usr/bin:/bin"
DETECT_APP=0
out="$(run_install "$home")"
DETECT_APP=1
if printf '%s' "$out" | grep -q 'no host CLI detected' \
  && printf '%s' "$out" | grep -q 'MCP entry ->'; then
  ok "no host CLI falls back to the MCP entry"
else
  bad "no host fallback: $out"
fi

printf '\n%s/%s installer checks passed\n' "$((CASES - FAILED))" "$CASES"
[ "$FAILED" -eq 0 ]
