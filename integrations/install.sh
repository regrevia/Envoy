#!/bin/sh
# Envoy one-command installer for the three agent hosts.
#
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- dsh
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex
#
# It does two things: put an up-to-date `envoy` on PATH (reusing one that is
# already new enough), then register the plugin the named host needs. Each host
# keeps its own README for the manual path; this script exists so a marketplace
# visitor has one line to paste instead of a runtime step followed by two
# plugin commands.
#
# Environment overrides:
#   ENVOY_VERSION   runtime version to install (default: release manifest)
#   ENVOY_HOME      private runtime root (default: ~/.local/share/envoy-dsh/runtime)
#   DSH_PROFILE     Harness profile for the DSH bundle (default: web)
#   ENVOY_DRY_RUN=1 print the commands instead of running them
set -eu

REPO="regrevia/Envoy"
MANIFEST_URL="https://raw.githubusercontent.com/$REPO/main/integrations/release-manifest.json"
RUNTIME_HOME="${ENVOY_HOME:-$HOME/.local/share/envoy-dsh/runtime}"
RUNTIME_BIN="$RUNTIME_HOME/bin/envoy"
DSH_PROFILE="${DSH_PROFILE:-web}"
DRY_RUN="${ENVOY_DRY_RUN:-0}"

die() {
  printf 'envoy install: %s\n' "$1" >&2
  exit 1
}

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + %s\n' "$*"
    return 0
  fi
  "$@"
}

usage() {
  cat <<'EOF'
usage: install.sh <dsh|claude-code|codex|mcp|skill|all>

  dsh          install the DeepSeek Harness bundle from npm
  claude-code  add this repository as a marketplace, then install the plugin
  codex        same, plus the user-level prompt hook Codex needs
  mcp          add the envoy MCP server to Claude Desktop, Cursor or Windsurf
  skill        unpack the skill into ~/.envoy/skill/envoy for hosts without plugins
  all          every host whose CLI is on PATH, falling back to the MCP entry
EOF
}

resolve_version() {
  if [ -n "${ENVOY_VERSION:-}" ]; then
    printf '%s' "$ENVOY_VERSION"
    return 0
  fi
  manifest="$(curl -fsSL "$MANIFEST_URL" 2>/dev/null || true)"
  version="$(printf '%s' "$manifest" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  [ -n "$version" ] || die "could not read the release manifest; set ENVOY_VERSION"
  printf '%s' "$version"
}

version_at_least() {
  current="$1"
  required="$2"
  [ "$current" = "$required" ] && return 0
  # Plain dotted comparison; Envoy has never shipped a pre-release tag here.
  IFS=.
  set -- $current
  a1=${1:-0} a2=${2:-0} a3=${3:-0}
  set -- $required
  b1=${1:-0} b2=${2:-0} b3=${3:-0}
  unset IFS
  [ "$a1" -gt "$b1" ] && return 0
  [ "$a1" -lt "$b1" ] && return 1
  [ "$a2" -gt "$b2" ] && return 0
  [ "$a2" -lt "$b2" ] && return 1
  [ "$a3" -ge "$b3" ]
}

# Releases publish SHA256SUMS next to the wheel; verifying costs one extra
# request and turns a truncated download into a clear error instead of a
# half-installed runtime.
verify_checksum() {
  file="$1"
  name="$2"
  sums_url="https://github.com/$REPO/releases/download/v$RUNTIME_VERSION/SHA256SUMS"
  sums="$(curl -fsSL "$sums_url" 2>/dev/null)" || return 0
  expected="$(printf '%s\n' "$sums" | awk -v n="$name" '$2 == n || $2 == "*" n {print $1; exit}')"
  [ -n "$expected" ] || return 0
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    return 0
  fi
  if [ "$expected" != "$actual" ]; then
    die "checksum mismatch for $name (expected $expected, got $actual); delete it and re-run"
  fi
  printf 'envoy install: checksum ok for %s\n' "$name"
}

install_runtime() {
  version="$1"
  wheel="envoy_cli-$version-py3-none-any.whl"
  url="https://github.com/$REPO/releases/download/v$version/$wheel"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT INT TERM

  printf 'envoy install: runtime %s -> %s\n' "$version" "$RUNTIME_HOME"
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + venv %s\n' "$RUNTIME_HOME"
    printf '  + download %s\n' "$url"
    printf '  + pip install %s\n' "$wheel"
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    uv venv --allow-existing "$RUNTIME_HOME" >/dev/null 2>&1 || uv venv "$RUNTIME_HOME" >/dev/null
    uv pip install --python "$RUNTIME_HOME/bin/python" "$url" >/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    [ -x "$RUNTIME_HOME/bin/python" ] || python3 -m venv "$RUNTIME_HOME"
    curl -fsSL -o "$tmpdir/$wheel" "$url"
    verify_checksum "$tmpdir/$wheel" "$wheel"
    "$RUNTIME_HOME/bin/python" -m pip install --quiet --upgrade "$tmpdir/$wheel"
  else
    die "neither uv nor python3 is available; install one of them and re-run"
  fi
  [ -x "$RUNTIME_BIN" ] || die "runtime install finished without $RUNTIME_BIN"

  # The DSH bundle keeps its runtime private; the two plugin hosts spawn a bare
  # `envoy`, so expose it on PATH without touching an existing install.
  if ! command -v envoy >/dev/null 2>&1; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$RUNTIME_BIN" "$HOME/.local/bin/envoy"
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) ;;
      *) printf '\nexport PATH="$HOME/.local/bin:$PATH"\n  (add the line above to your shell profile)\n' >&2 ;;
    esac
  fi
}

# A finished install that cannot read a page is still a failed install, so say
# so now instead of after the user's first link.
report_health() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + envoy doctor --json\n'
    return 0
  fi
  report="$(envoy doctor --json 2>/dev/null)" || report=""
  if [ -z "$report" ]; then
    printf 'envoy install: envoy doctor did not answer; run it yourself if the first read fails\n' >&2
    return 0
  fi
  printf '%s' "$report" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
version = data.get("cli_version") or data.get("version") or "unknown"
protocol = data.get("protocol_version") or "unknown"
print(f"envoy install: runtime {version}, protocol {protocol}")
' 2>/dev/null || printf 'envoy install: could not summarize envoy doctor output\n' >&2
}

RUNTIME_VERSION=""

ensure_runtime() {
  version="$(resolve_version)"
  RUNTIME_VERSION="$version"
  if command -v envoy >/dev/null 2>&1; then
    current="$(envoy --version 2>/dev/null | sed -n 's/.* \([0-9][0-9.]*\).*/\1/p' | head -1)"
    if [ -n "$current" ] && version_at_least "$current" "$version"; then
      printf 'envoy install: using envoy %s from %s\n' "$current" "$(command -v envoy)"
      return 0
    fi
    printf 'envoy install: envoy %s on PATH is older than %s\n' "${current:-unknown}" "$version" >&2
  fi
  if [ -x "$RUNTIME_BIN" ]; then
    current="$("$RUNTIME_BIN" --version 2>/dev/null | sed -n 's/.* \([0-9][0-9.]*\).*/\1/p' | head -1)"
    if [ -n "$current" ] && version_at_least "$current" "$version"; then
      printf 'envoy install: using envoy %s from %s\n' "$current" "$RUNTIME_BIN"
      expose_runtime
      return 0
    fi
  fi
  install_runtime "$version"
}

expose_runtime() {
  if ! command -v envoy >/dev/null 2>&1; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$RUNTIME_BIN" "$HOME/.local/bin/envoy"
  fi
}

find_dsh() {
  if [ -n "${DSH_CLI:-}" ] && [ -x "$DSH_CLI" ]; then
    printf '%s' "$DSH_CLI"
    return 0
  fi
  if command -v dsh >/dev/null 2>&1; then
    command -v dsh
    return 0
  fi
  # DeepSeek Harness Desktop ships its CLI inside the app bundle and does not
  # put it on PATH, so a marketplace visitor has no `dsh` to type.
  for candidate in \
    "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" \
    "$HOME/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"; do
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

install_dsh() {
  dsh_bin="$(find_dsh)" || die "the dsh CLI was not found; install DeepSeek Harness Desktop, or set DSH_CLI to its dsh binary"
  printf 'envoy install: DeepSeek Harness bundle (profile %s)\n' "$DSH_PROFILE"
  # The published npm package trails the repo, and a release tarball carries the
  # same files without waiting for a publish, so try the tarball first.
  tarball="https://github.com/$REPO/releases/download/v$RUNTIME_VERSION/envoy-dsh-$RUNTIME_VERSION.tgz"
  if [ "$DRY_RUN" = "1" ]; then
    run "$dsh_bin" plugin --profile "$DSH_PROFILE" add "$tarball"
  elif curl -fsSL -o /dev/null --range 0-0 "$tarball" 2>/dev/null; then
    run "$dsh_bin" plugin --profile "$DSH_PROFILE" add "$tarball"
  else
    run "$dsh_bin" plugin --profile "$DSH_PROFILE" add envoy-dsh
  fi
  printf 'Restart DeepSeek Harness so the bundle mounts.\n'
  report_health
}

install_claude_code() {
  command -v claude >/dev/null 2>&1 || die "the claude CLI is not on PATH; install Claude Code first"
  printf 'envoy install: Claude Code plugin\n'
  run claude plugin marketplace add "regrevia/Envoy"
  run claude plugin install "envoy@envoy"
  printf 'Restart Claude Code, or run /plugin to confirm Envoy is enabled.\n'
  report_health
}

install_codex() {
  command -v codex >/dev/null 2>&1 || die "the codex CLI is not on PATH; install Codex first"
  printf 'envoy install: Codex plugin\n'
  run codex plugin marketplace add "https://github.com/$REPO.git"
  run codex plugin add "envoy@envoy"
  printf 'envoy install: Codex prompt hook\n'
  run envoy hook install --host codex
  printf 'Restart Codex so the plugin and hook load.\n'
  report_health
}

envoy_binary() {
  if command -v envoy >/dev/null 2>&1; then
    command -v envoy
  else
    printf '%s' "$RUNTIME_BIN"
  fi
}

# Hosts that only take an MCP server (Claude Desktop, Cursor, Windsurf) have no
# plugin manager and no skill directory, so the install is one JSON entry. Merge
# into an existing config instead of replacing it: those files hold the user's
# other servers.
install_mcp() {
  bin="$(envoy_binary)"
  printf 'envoy install: MCP entry -> %s\n' "$bin"
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + add {"envoy": {"command": "%s", "args": ["mcp"]}} to each detected host config\n' "$bin"
    return 0
  fi
  ENVOY_MCP_BIN="$bin" python3 - <<'ENVOY_MCP'
import json, os, pathlib, sys, time

home = pathlib.Path.home()
bin_path = os.environ["ENVOY_MCP_BIN"]
candidates = [
    home / "Library/Application Support/Claude/claude_desktop_config.json",
    home / ".config/Claude/claude_desktop_config.json",
    home / ".cursor/mcp.json",
    home / ".config/Cursor/User/mcp.json",
    home / ".codeium/windsurf/mcp_config.json",
]
wrote = []
for path in candidates:
    if not path.is_file():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        print(f"envoy install: skipped {path} (invalid JSON: {exc})", file=sys.stderr)
        continue
    servers = data.setdefault("mcpServers", {})
    servers["envoy"] = {"command": bin_path, "args": ["mcp"]}
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(path.suffix + ".envoy-" + stamp)
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    wrote.append(path)

if wrote:
    for path in wrote:
        print(f"envoy install: wrote {path}")
else:
    template = home / ".envoy/mcp.json"
    template.parent.mkdir(parents=True, exist_ok=True)
    entry = {"mcpServers": {"envoy": {"command": bin_path, "args": ["mcp"]}}}
    template.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    print(f"envoy install: no host config found; template written to {template}")
    print("envoy install: copy that mcpServers.envoy entry into your host MCP config")
ENVOY_MCP
  printf 'Restart the host so it launches the envoy MCP server.\n'
}

# For hosts that read a plain skill directory rather than a plugin package.
install_skill() {
  dest="${ENVOY_SKILL_DIR:-$HOME/.envoy/skill/envoy}"
  url="https://github.com/$REPO/releases/download/v$RUNTIME_VERSION/envoy-skill.zip"
  printf 'envoy install: skill -> %s\n' "$dest"
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + download %s\n  + extract to %s\n' "$url" "$dest"
    return 0
  fi
  mkdir -p "$dest"
  tmpzip="$(mktemp)"
  curl -fsSL -o "$tmpzip" "$url"
  verify_checksum "$tmpzip" "envoy-skill.zip"
  python3 - "$tmpzip" "$dest" <<'ENVOY_SKILL'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2])
print(f"envoy install: wrote {sys.argv[2]}")
ENVOY_SKILL
  rm -f "$tmpzip"
  printf 'Point the host at %s, then restart it.\n' "$dest"
}

host="${1:-}"
case "$host" in
  dsh)
    ensure_runtime
    install_dsh
    ;;
  claude-code|claude)
    ensure_runtime
    install_claude_code
    ;;
  codex)
    ensure_runtime
    install_codex
    ;;
  mcp)
    ensure_runtime
    install_mcp
    report_health
    ;;
  skill)
    ensure_runtime
    install_skill
    report_health
    ;;
  all)
    ensure_runtime
    installed=0
    if find_dsh >/dev/null 2>&1; then install_dsh; installed=1; fi
    if command -v claude >/dev/null 2>&1; then install_claude_code; installed=1; fi
    if command -v codex >/dev/null 2>&1; then install_codex; installed=1; fi
    if [ "$installed" = "0" ]; then install_mcp; installed=1; fi
    report_health
    [ "$installed" = "1" ] || die "no host CLI found (dsh, claude, codex)"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
