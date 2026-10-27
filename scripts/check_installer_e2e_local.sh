#!/bin/sh
# End-to-end installer check against a loopback mirror.
#
# The real one-liner downloads from github.com; nothing in this check leaves the
# machine. It lays the release layout out in a temp dir, serves it on 127.0.0.1
# and runs `install.sh skill`, so version resolution, download, checksum
# verification and unpacking are exercised together. No public site is read and
# no runtime wheel is installed (the skill target needs neither uv nor a host
# CLI).
#
# Usage: sh scripts/check_installer_e2e_local.sh [<delivery-dir>]
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DELIVERY="${1:-/Users/apple/Desktop/2026/envoy-delivery/0.1.24}"
INSTALL="$ROOT/integrations/install.sh"

[ -f "$INSTALL" ] || { echo "missing $INSTALL" >&2; exit 1; }
[ -f "$DELIVERY/release-manifest.json" ] || { echo "missing $DELIVERY/release-manifest.json" >&2; exit 1; }
[ -f "$DELIVERY/SHA256SUMS" ] || { echo "missing $DELIVERY/SHA256SUMS" >&2; exit 1; }
[ -f "$DELIVERY/envoy-skill.zip" ] || { echo "missing $DELIVERY/envoy-skill.zip" >&2; exit 1; }

work="$(mktemp -d)"
server_pid=""
cleanup() {
  [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT INT TERM

version="$(python3 - "$DELIVERY/release-manifest.json" <<'ENVOY_VERSION'
import json, sys
print(json.load(open(sys.argv[1]))["version"])
ENVOY_VERSION
)"

serve="$work/serve"
mkdir -p "$serve/releases/download/v$version"
cp "$DELIVERY/release-manifest.json" "$serve/release-manifest.json"
cp "$DELIVERY/SHA256SUMS" "$serve/releases/download/v$version/SHA256SUMS"
cp "$DELIVERY/envoy-skill.zip" "$serve/releases/download/v$version/envoy-skill.zip"

port="$(python3 - <<'ENVOY_PORT'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
ENVOY_PORT
)"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$serve" >"$work/server.log" 2>&1 &
server_pid=$!

ready=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$port/release-manifest.json" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.2
done
[ -n "$ready" ] || { echo "loopback mirror did not come up" >&2; cat "$work/server.log" >&2; exit 1; }

failures=0
checks=0
pass() { checks=$((checks + 1)); printf 'ok   %s\n' "$1"; }
fail() { checks=$((checks + 1)); printf 'FAIL %s: %s\n' "$1" "$2"; failures=$((failures + 1)); }

run_install() {
  home="$1"
  skill_dir="$2"
  env -i \
    HOME="$home" \
    PATH="$PATH" \
    ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
    ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
    ENVOY_SKILL_DIR="$skill_dir" \
    sh "$INSTALL" skill
}

home="$work/home-ok"
mkdir -p "$home"
if output="$(run_install "$home" "$work/skill-ok" 2>&1)"; then
  case "$output" in
    *"checksum ok for envoy-skill.zip"*) pass "loopback install verifies the downloaded zip" ;;
    *) fail "checksum announcement" "checksum ok line missing from: $(printf '%s' "$output" | tail -3)" ;;
  esac
  if [ -f "$work/skill-ok/SKILL.md" ]; then
    pass "loopback install unpacks the skill"
  else
    fail "skill unpack" "SKILL.md missing under $work/skill-ok"
  fi
else
  fail "loopback install" "$(printf '%s' "$output" | tail -5)"
fi

# A dry run must describe the work without touching the mirror's payload.
home="$work/home-dry"
mkdir -p "$home"
if output="$(env -i HOME="$home" PATH="$PATH" \
  ENVOY_DRY_RUN=1 \
  ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
  ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
  ENVOY_SKILL_DIR="$work/skill-dry" \
  sh "$INSTALL" skill 2>&1)"; then
  case "$output" in
    *download*envoy-skill.zip*) pass "dry run prints the download it would do" ;;
    *) fail "dry run plan" "$(printf '%s' "$output" | tail -3)" ;;
  esac
  if [ ! -e "$work/skill-dry" ]; then
    pass "dry run writes nothing"
  else
    fail "dry run side effect" "$work/skill-dry exists after a dry run"
  fi
else
  fail "dry run" "$(printf '%s' "$output" | tail -5)"
fi

# Every dispatch branch must build its asset URL from the same root: the DSH
# bundle is the one download a host CLI pulls, so check the printed plan.
home="$work/home-dsh"
mkdir -p "$home"
if output="$(env -i HOME="$home" PATH="$PATH" \
  ENVOY_VERSION="$version" ENVOY_DRY_RUN=1 ENVOY_SKIP_UV_INSTALL=1 \
  ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
  ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
  sh "$INSTALL" dsh 2>&1)"; then
  case "$output" in
    *"http://127.0.0.1:$port/releases/download/v$version/envoy-dsh-$version.tgz"*)
      pass "the DSH bundle URL comes from the overridable root" ;;
    *"DeepSeek Harness bundle"*)
      fail "DSH bundle URL" "the plan still points somewhere else: $(printf '%s' "$output" | grep -m1 'plugin' || true)" ;;
    *) pass "no DSH host on this machine, bundle branch not exercised" ;;
  esac
else
  fail "DSH dry run" "$(printf '%s' "$output" | tail -5)"
fi

# A dry run must not leave a pinned runtime behind: pinning happens inside
# ensure_runtime, before any host install, so every branch has to stay clean.
home="$work/home-mcp"
mkdir -p "$home"
if output="$(env -i HOME="$home" PATH="$PATH" \
  ENVOY_VERSION="$version" ENVOY_DRY_RUN=1 ENVOY_SKIP_UV_INSTALL=1 \
  ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
  ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
  sh "$INSTALL" mcp 2>&1)"; then
  case "$output" in
    *'add {"envoy"'*) pass "the MCP entry is planned for every detected host" ;;
    *) fail "MCP dry run" "$(printf '%s' "$output" | tail -5)" ;;
  esac
  if [ -e "$home/.envoy/runtime-path" ] || [ -e "$home/.envoy/mcp.json" ]; then
    fail "MCP dry run wrote state" "$(find "$home" -type f | tr '\n' ' ')"
  else
    pass "nothing at all is written by a dry run"
  fi
else
  fail "MCP dry run" "$(printf '%s' "$output" | tail -5)"
fi

# The two host-CLI dispatches are the one-click path: their plan must name the
# marketplace source, follow ENVOY_REPO, and still write nothing on a dry run.
for target in claude-code codex; do
  home="$work/home-$target"
  mkdir -p "$home"
  if output="$(env -i HOME="$home" PATH="$PATH" \
    ENVOY_VERSION="$version" ENVOY_DRY_RUN=1 ENVOY_SKIP_UV_INSTALL=1 \
    ENVOY_REPO=acme/Envoy \
    ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
    ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
    sh "$INSTALL" "$target" 2>&1)"; then
    case "$target:$output" in
      claude-code:*"claude plugin marketplace add acme/Envoy"*)
        pass "the Claude Code plan follows ENVOY_REPO" ;;
      codex:*"codex plugin marketplace add https://github.com/acme/Envoy.git"*)
        pass "the Codex plan follows ENVOY_REPO" ;;
      *)
        fail "$target plan" "$(printf '%s' "$output" | tail -4)" ;;
    esac
    if [ -z "$(find "$home" -type f 2>/dev/null)" ]; then
      pass "$target dry run writes nothing"
    else
      fail "$target side effect" "$(find "$home" -type f | tr '\n' ' ')"
    fi
  else
    fail "$target dry run" "$(printf '%s' "$output" | tail -5)"
  fi
done

# A machine that really has the dsh CLI must get the bundle from the same
# overridable root, and a dry run must not call it.
mkdir -p "$work/bin"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n' "$work/dsh-calls" > "$work/bin/dsh"
chmod +x "$work/bin/dsh"
home="$work/home-dsh-cli"
mkdir -p "$home"
if output="$(env -i HOME="$home" PATH="$work/bin:$PATH" \
  ENVOY_VERSION="$version" ENVOY_DRY_RUN=1 ENVOY_SKIP_UV_INSTALL=1 \
  ENVOY_MANIFEST_URL="http://127.0.0.1:$port/release-manifest.json" \
  ENVOY_RELEASES_BASE="http://127.0.0.1:$port/releases/download" \
  sh "$INSTALL" dsh 2>&1)"; then
  case "$output" in
    *"http://127.0.0.1:$port/releases/download/v$version/envoy-dsh-$version.tgz"*)
      pass "the DSH bundle is added from the overridable root" ;;
    *) fail "DSH bundle plan" "$(printf '%s' "$output" | tail -4)" ;;
  esac
  if [ ! -s "$work/dsh-calls" ]; then
    pass "the DSH dry run does not call the host CLI"
  else
    fail "DSH dry run called the host" "$(cat "$work/dsh-calls")"
  fi
else
  fail "DSH host dry run" "$(printf '%s' "$output" | tail -5)"
fi

# The same mirror with a wrong digest must stop the installation.
cp "$DELIVERY/SHA256SUMS" "$serve/releases/download/v$version/SHA256SUMS"
python3 - "$serve/releases/download/v$version/SHA256SUMS" <<'ENVOY_TAMPER'
import sys
path = sys.argv[1]
lines = []
for line in open(path):
    digest, name = line.split()
    if name == "envoy-skill.zip":
        digest = "0" * 64
    lines.append(f"{digest}  {name}\n")
open(path, "w").write("".join(lines))
ENVOY_TAMPER
home="$work/home-bad"
mkdir -p "$home"
if output="$(run_install "$home" "$work/skill-bad" 2>&1)"; then
  fail "tampered mirror" "installer succeeded with a wrong digest: $(printf '%s' "$output" | tail -3)"
else
  case "$output" in
    *"checksum mismatch for envoy-skill.zip"*) pass "wrong digest stops the install" ;;
    *) fail "tampered mirror message" "$(printf '%s' "$output" | tail -3)" ;;
  esac
fi

if [ "$failures" -eq 0 ]; then
  printf '\n%s loopback installer checks passed\n' "$checks"
  exit 0
fi
printf '\n%s loopback installer checks failed\n' "$failures" >&2
exit 1
