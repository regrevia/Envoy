#!/bin/sh
# Envoy one-command installer for the three agent hosts.
#
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- claude-code
#   curl -fsSL https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.sh | sh -s -- codex
#
# It does three things: install one private runtime, register the host plugin,
# then provision the browser once so the first link does not wait for a download.
# With no argument it installs every host it can detect, so the same line works
# for Claude Code, Codex and DeepSeek Harness.
#
# The runtime is pinned to one absolute path (`envoy path` prints it) instead of
# trusting whatever `envoy` PATH happens to resolve to: a stale PATH binary could
# otherwise win and the plugin would spawn an older runtime for months.
#
# Environment overrides:
#   ENVOY_VERSION        runtime version to install (default: release manifest)
#   ENVOY_HOME           private runtime root (default: ~/.local/share/envoy-dsh/runtime)
#   ENVOY_RUNTIME_BIN    explicit runtime to reuse instead of installing one
#   ENVOY_PROFILE       minimal | standard | full (default: full, the recommended one)
#                       minimal: runtime only, ~180 MB
#                       standard: runtime + Chromium, ~860 MB
#                       full: runtime + Chromium + Whisper base, no later downloads
#   ENVOY_SETUP_BROWSER override the profile's browser step: 1 download, 0 skip
#   ENVOY_SETUP_MEDIA   override the profile's speech-recognition step: 1 setup, 0 skip
#   ENVOY_ASR_MODEL     Whisper model the full profile prefetches (default: base)
#   ENVOY_DETECT_DESKTOP_APP 1 (default) also finds the DeepSeek Harness app bundle
#   DSH_PROFILE          Harness profile for the DSH bundle (default: web)
#   ENVOY_MANIFEST_URL   release manifest to read (default: raw.githubusercontent main)
#   ENVOY_RELEASES_BASE  download root holding v<version>/ (default: GitHub releases)
#                        both exist so a mirror or a loopback checkout can serve the
#                        same layout; the defaults are the public paths
#   ENVOY_DRY_RUN=1      print the commands instead of running them
set -eu

# The repository is the one place both default URLs and the plugin marketplaces
# come from, so a fork only has to set ENVOY_REPO.
REPO="${ENVOY_REPO:-regrevia/Envoy}"
MANIFEST_URL="${ENVOY_MANIFEST_URL:-https://raw.githubusercontent.com/$REPO/main/integrations/release-manifest.json}"
# Overridable so a mirror (or a loopback checkout) can serve the same layout
# as releases/download without touching anything else in this script.
RELEASES_BASE="${ENVOY_RELEASES_BASE:-https://github.com/$REPO/releases/download}"
RUNTIME_HOME="${ENVOY_HOME:-$HOME/.local/share/envoy-dsh/runtime}"
RUNTIME_BIN="$RUNTIME_HOME/bin/envoy"
RUNTIME_PIN="${ENVOY_RUNTIME_PIN:-$HOME/.envoy/runtime-path}"
DSH_PROFILE="${DSH_PROFILE:-web}"
DRY_RUN="${ENVOY_DRY_RUN:-0}"
PROFILE="${ENVOY_PROFILE:-full}"
SETUP_BROWSER="${ENVOY_SETUP_BROWSER:-}"
SETUP_MEDIA="${ENVOY_SETUP_MEDIA:-}"
ASR_MODEL="${ENVOY_ASR_MODEL:-base}"
DETECT_DESKTOP_APP="${ENVOY_DETECT_DESKTOP_APP:-1}"

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
usage: install.sh [--profile minimal|standard|full] [dsh|claude-code|codex|mcp|skill|all|path]

  (no argument) every host detected on this machine, full profile

  --profile minimal   runtime only, about 180 MB; nothing is downloaded later
                      until you ask for it
  --profile standard  runtime + Chromium, about 860 MB; reads rendered pages
                      and video pages, no speech recognition
  --profile full      runtime + Chromium + Whisper base model (recommended);
                      every capability this host exposes works offline after
                      install, nothing downloads on first use

  ENVOY_PROFILE sets the same thing without the flag; ENVOY_SETUP_BROWSER and
  ENVOY_SETUP_MEDIA override one step of the chosen profile.
  dsh          install the DeepSeek Harness bundle from npm
  claude-code  add this repository as a marketplace, then install the plugin
  codex        same, plus the user-level prompt hook Codex needs
  mcp          add the envoy MCP server to Claude Desktop, Cursor or Windsurf
  skill        unpack the skill into ~/.envoy/skill/envoy for hosts without plugins
  all          every host whose CLI is on PATH, falling back to the MCP entry
  path         print the runtime path the installer pinned (nothing is changed)
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
  sums_url="$RELEASES_BASE/v$RUNTIME_VERSION/SHA256SUMS"
  if ! sums="$(curl -fsSL "$sums_url" 2>/dev/null)"; then
    printf 'envoy install: checksum unavailable for %s (SHA256SUMS not reachable); continuing unverified\n' "$name"
    return 0
  fi
  expected="$(printf '%s\n' "$sums" | awk -v n="$name" '$2 == n || $2 == "*" n {print $1; exit}')"
  if [ -z "$expected" ]; then
    die "SHA256SUMS has no entry for $name; delete it and re-run"
  fi
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    printf 'envoy install: checksum skipped for %s (no shasum or sha256sum on this machine)\n' "$name"
    return 0
  fi
  if [ "$expected" != "$actual" ]; then
    die "checksum mismatch for $name (expected $expected, got $actual); delete it and re-run"
  fi
  printf 'envoy install: checksum ok for %s\n' "$name"
}

# The CLI requires Python >= 3.10, and macOS ships 3.9 as `python3`. Ask uv for a
# suitable interpreter (it downloads one) and treat an existing older venv as
# stale instead of installing into it.
ENVOY_PYTHON="${ENVOY_PYTHON:-3.11}"

python_is_new_enough() {
  [ -x "$1" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

# uv is the one prerequisite this script can supply itself: without it a machine
# whose only python3 is 3.9 (macOS) cannot get a working runtime. Installing uv
# is a single curl, so do it instead of asking the user to read a doc.
ensure_uv() {
  command -v uv >/dev/null 2>&1 && return 0
  [ "${ENVOY_SKIP_UV_INSTALL:-0}" = "1" ] && return 1
  command -v curl >/dev/null 2>&1 || return 1
  printf 'envoy install: uv not found; installing it -> %s/.local/bin\n' "$HOME"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || return 1
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      PATH="$(dirname "$candidate"):$PATH"
      export PATH
      return 0
    fi
  done
  return 1
}

runtime_version_of() {
  if [ ! -x "$1" ]; then
    return 0
  fi
  "$1" --version 2>/dev/null | sed -n 's/.* \([0-9][0-9.]*\).*/\1/p' | head -1
}

pin_runtime() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + pin %s\n' "$1"
    return 0
  fi
  mkdir -p "$(dirname "$RUNTIME_PIN")"
  printf '%s\n' "$1" > "$RUNTIME_PIN"
}

pinned_runtime() {
  if [ -f "$RUNTIME_PIN" ]; then
    candidate="$(head -1 "$RUNTIME_PIN" 2>/dev/null)"
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s' "$candidate"
    fi
  fi
}

install_runtime() {
  version="$1"
  wheel="envoy_cli-$version-py3-none-any.whl"
  url="$RELEASES_BASE/v$version/$wheel"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT INT TERM

  printf 'envoy install: runtime %s -> %s\n' "$version" "$RUNTIME_HOME"
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + venv %s\n' "$RUNTIME_HOME"
    printf '  + download %s\n' "$url"
    printf '  + pip install %s\n' "$wheel"
    printf '  + pin %s -> %s\n' "$RUNTIME_BIN" "$RUNTIME_PIN"
    return 0
  fi

  if ! command -v uv >/dev/null 2>&1; then
    if ! command -v python3 >/dev/null 2>&1 || ! python_is_new_enough "$(command -v python3)"; then
      ensure_uv || true
    fi
  fi

  if command -v uv >/dev/null 2>&1; then
    if ! python_is_new_enough "$RUNTIME_HOME/bin/python"; then
      rm -rf "$RUNTIME_HOME"
      uv venv --quiet --python "$ENVOY_PYTHON" "$RUNTIME_HOME" >/dev/null \
        || die "uv could not provision Python $ENVOY_PYTHON; set ENVOY_PYTHON to a version >= 3.10"
    fi
    uv pip install --quiet --python "$RUNTIME_HOME/bin/python" "$url" >/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    if ! python_is_new_enough "$RUNTIME_HOME/bin/python"; then
      python_is_new_enough "$(command -v python3)" \
        || die "python3 is $(python3 -c 'import platform; print(platform.python_version())'), envoy needs >= 3.10; install uv (brew install uv) or a newer Python and re-run"
      rm -rf "$RUNTIME_HOME"
      python3 -m venv "$RUNTIME_HOME"
    fi
    curl -fsSL -o "$tmpdir/$wheel" "$url"
    verify_checksum "$tmpdir/$wheel" "$wheel"
    "$RUNTIME_HOME/bin/python" -m pip install --quiet --upgrade "$tmpdir/$wheel"
  else
    die "neither uv nor python3 is available; install one of them and re-run"
  fi
  [ -x "$RUNTIME_BIN" ] || die "runtime install finished without $RUNTIME_BIN"

  # The DSH bundle keeps its runtime private; the two plugin hosts spawn a bare
  # `envoy`, so expose it on PATH without touching an existing install.
  pin_runtime "$RUNTIME_BIN"
  expose_runtime
}

# The two plugin hosts spawn a bare `envoy`; expose the pinned runtime on PATH
# without touching an existing install (a user may have their own envoy).
expose_runtime() {
  if [ "$DRY_RUN" = "1" ]; then
    return 0
  fi
  if ! command -v envoy >/dev/null 2>&1; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$(envoy_binary)" "$HOME/.local/bin/envoy"
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) ;;
      *) printf '\nexport PATH="$HOME/.local/bin:$PATH"\n  (add the line above to your shell profile)\n' >&2 ;;
    esac
  fi
}

# Three profiles, one axis: how much of the optional surface is provisioned up
# front. The default is the complete one, because a capability that silently
# fails on first use costs the user more than the download it would have taken
# at install time.
apply_profile() {
  case "$PROFILE" in
    minimal|standard|full) ;;
    *) die "unknown profile '$PROFILE'; expected minimal, standard or full" ;;
  esac
  if [ -z "$SETUP_BROWSER" ]; then
    case "$PROFILE" in
      minimal) SETUP_BROWSER=0 ;;
      *) SETUP_BROWSER=1 ;;
    esac
  fi
  if [ -z "$SETUP_MEDIA" ]; then
    case "$PROFILE" in
      full) SETUP_MEDIA=1 ;;
      *) SETUP_MEDIA=0 ;;
    esac
  fi
}

# Chromium takes a minute or two and belongs to install time, not to the user's
# first link: a read that downloads it would sit inside the host's tool deadline
# and surface as a failed page instead of a one-time setup step.
provision_browser() {
  if [ "$SETUP_BROWSER" != "1" ]; then
    printf 'envoy install: skipped browser setup (profile %s); run `envoy browser setup` before the first link\n' "$PROFILE"
    return 0
  fi
  printf 'envoy install: browser runtime (one-time, about 1-2 minutes)\n'
  run "$1" browser setup || printf 'envoy install: browser setup did not finish; run `envoy browser setup` later\n' >&2
}

# Speech recognition is the only base capability whose weights are not in the
# wheel: install-time is where they belong, so `full` never downloads on the
# first audio or subtitle-less video.
provision_media() {
  if [ "$SETUP_MEDIA" != "1" ]; then
    printf 'envoy install: skipped speech recognition (profile %s); run `envoy asr install --model %s` if you need audio transcription\n' "$PROFILE" "$ASR_MODEL"
    return 0
  fi
  printf 'envoy install: speech recognition (Whisper %s, one-time; tens to hundreds of MB)\n' "$ASR_MODEL"
  run "$1" asr install --model "$ASR_MODEL" \
    || printf 'envoy install: speech recognition did not finish; run `envoy asr install --model %s` later\n' "$ASR_MODEL" >&2
}

# A finished install that cannot read a page is still a failed install, so say
# so now instead of after the user's first link.
report_health() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '  + envoy doctor --json\n'
    return 0
  fi
  report="$("$(envoy_binary)" doctor --json 2>/dev/null)" || report=""
  if [ -z "$report" ]; then
    printf 'envoy install: envoy doctor did not answer; run it yourself if the first read fails\n' >&2
    return 0
  fi
  summarizer="python3"
  if [ -x "$RUNTIME_HOME/bin/python" ]; then
    summarizer="$RUNTIME_HOME/bin/python"
  fi
  summary_status=0
  printf '%s' "$report" | ENVOY_EXPECT_BROWSER="$SETUP_BROWSER" ENVOY_EXPECT_MEDIA="$SETUP_MEDIA" \
    "$summarizer" -c '
import json, os, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
version = data.get("cli_version") or data.get("version") or "unknown"
protocol = data.get("protocol_version") or "unknown"
runtime = data.get("runtime") or {}
browser_ready = bool((runtime.get("browser") or {}).get("ready"))
browser = "ready" if browser_ready else "missing: envoy browser setup"
install = runtime.get("install") or {}
model = runtime.get("asr_model") or "base"
asr_ready = bool(runtime.get("asr_model_available"))
asr = "ready" if asr_ready else f"missing: run envoy asr install --model {model}"
print(f"envoy install: runtime {version}, protocol {protocol}, browser {browser}, asr {asr}")
status = install.get("status")
if status and status != "pinned":
    pinned = install.get("pinned_runtime") or "none"
    on_path = install.get("path_envoy") or "none"
    print(f"envoy install: install state {status}: pinned {pinned}, PATH runs {on_path}; rerun install.sh if that is wrong")
# The profile promised these at install time; an exit code of 0 that leaves them
# missing is how "it installed but the first video downloaded 140 MB" happens.
missing = []
if os.environ.get("ENVOY_EXPECT_BROWSER") == "1" and not browser_ready:
    missing.append("browser runtime: envoy browser setup")
if os.environ.get("ENVOY_EXPECT_MEDIA") == "1" and not asr_ready:
    missing.append(f"speech recognition: envoy asr install --model {model}")
if missing:
    print("envoy install: install incomplete, still missing " + " and ".join(missing), file=sys.stderr)
    raise SystemExit(3)
' || summary_status=$?
  if [ "$summary_status" -eq 3 ]; then
    printf 'envoy install: install incomplete; run the command above and then this installer again\n' >&2
    exit 1
  fi
  if [ "$summary_status" -ne 0 ]; then
    printf 'envoy install: could not summarize envoy doctor output\n' >&2
  fi
}

RUNTIME_VERSION=""

ensure_runtime() {
  version="$(resolve_version)"
  RUNTIME_VERSION="$version"

  # One runtime, one absolute path. A pinned path wins over PATH so a stale
  # `envoy` earlier in the user's PATH cannot hijack the plugin months later.
  pinned="$(pinned_runtime)"
  if [ -n "$pinned" ]; then
    current="$(runtime_version_of "$pinned")"
    if [ -n "$current" ] && version_at_least "$current" "$version"; then
      printf 'envoy install: using envoy %s from %s (pinned)\n' "$current" "$pinned"
      return 0
    fi
    printf 'envoy install: pinned envoy %s is older than %s\n' "${current:-unknown}" "$version" >&2
  fi

  if [ -n "${ENVOY_RUNTIME_BIN:-}" ]; then
    current="$(runtime_version_of "$ENVOY_RUNTIME_BIN")"
    [ -n "$current" ] || die "ENVOY_RUNTIME_BIN=$ENVOY_RUNTIME_BIN is not runnable"
    version_at_least "$current" "$version" || die "ENVOY_RUNTIME_BIN=$ENVOY_RUNTIME_BIN is envoy $current, older than $version"
    pin_runtime "$ENVOY_RUNTIME_BIN"
    printf 'envoy install: using envoy %s from %s (ENVOY_RUNTIME_BIN)\n' "$current" "$ENVOY_RUNTIME_BIN"
    return 0
  fi

  if [ -x "$RUNTIME_BIN" ]; then
    current="$(runtime_version_of "$RUNTIME_BIN")"
    if [ -n "$current" ] && version_at_least "$current" "$version"; then
      pin_runtime "$RUNTIME_BIN"
      printf 'envoy install: using envoy %s from %s\n' "$current" "$RUNTIME_BIN"
      expose_runtime
      return 0
    fi
  fi

  if command -v envoy >/dev/null 2>&1; then
    current="$(runtime_version_of "$(command -v envoy)")"
    if [ -n "$current" ] && version_at_least "$current" "$version"; then
      pin_runtime "$(command -v envoy)"
      printf 'envoy install: using envoy %s from %s (on PATH)\n' "$current" "$(command -v envoy)"
      return 0
    fi
    printf 'envoy install: envoy %s on PATH is older than %s; installing the private runtime\n' "${current:-unknown}" "$version" >&2
  fi

  install_runtime "$version"
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
  [ "$DETECT_DESKTOP_APP" = "1" ] || return 1
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
  tarball="$RELEASES_BASE/v$RUNTIME_VERSION/envoy-dsh-$RUNTIME_VERSION.tgz"
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
  run claude plugin marketplace add "$REPO"
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
  run "$(envoy_binary)" hook install --host codex
  printf 'Restart Codex so the plugin and hook load.\n'
  report_health
}

# Provisioning belongs to the profile, not to the runtime-install branch: a host
# already on PATH means no wheel is installed, and the browser and the ASR
# weights would otherwise never be fetched for the tier the user picked. Both
# steps are idempotent, so a re-run of the installer is also a repair.
ensure_profile() {
  binary="$(envoy_binary)"
  provision_browser "$binary"
  provision_media "$binary"
}

# Every host-facing step resolves the binary through here, so the plugin that
# gets registered and the runtime that serves it are always the same one.
envoy_binary() {
  pinned="$(pinned_runtime)"
  if [ -n "$pinned" ]; then
    printf '%s' "$pinned"
  elif [ -x "$RUNTIME_BIN" ]; then
    printf '%s' "$RUNTIME_BIN"
  elif command -v envoy >/dev/null 2>&1; then
    command -v envoy
  else
    die "no envoy runtime found; run the installer without a host argument first"
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
  url="$RELEASES_BASE/v$RUNTIME_VERSION/envoy-skill.zip"
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

install_auto() {
  ensure_runtime
  ensure_profile
  installed=0
  if find_dsh >/dev/null 2>&1; then install_dsh; installed=1; fi
  if command -v claude >/dev/null 2>&1; then install_claude_code; installed=1; fi
  if command -v codex >/dev/null 2>&1; then install_codex; installed=1; fi
  if [ "$installed" = "0" ]; then
    printf 'envoy install: no host CLI detected; writing the MCP entry instead\n' >&2
    install_mcp
  fi
  report_health
}

host=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      [ "$#" -ge 2 ] || die "--profile needs a value: minimal, standard or full"
      PROFILE="$2"
      shift 2
      ;;
    --profile=*)
      PROFILE="${1#--profile=}"
      shift
      ;;
    -p)
      [ "$#" -ge 2 ] || die "-p needs a value: minimal, standard or full"
      PROFILE="$2"
      shift 2
      ;;
    *)
      host="$1"
      shift
      ;;
  esac
done
apply_profile
case "$host" in
  "")
    install_auto
    ;;
  auto)
    install_auto
    ;;
  dsh)
    ensure_runtime
    ensure_profile
    install_dsh
    ;;
  claude-code|claude)
    ensure_runtime
    ensure_profile
    install_claude_code
    ;;
  codex)
    ensure_runtime
    ensure_profile
    install_codex
    ;;
  mcp)
    ensure_runtime
    ensure_profile
    install_mcp
    report_health
    ;;
  skill)
    ensure_runtime
    ensure_profile
    install_skill
    report_health
    ;;
  all)
    install_auto
    ;;
  path)
    pinned="$(pinned_runtime)"
    if [ -n "$pinned" ]; then
      printf '%s\n' "$pinned"
    elif [ -x "$RUNTIME_BIN" ]; then
      printf '%s\n' "$RUNTIME_BIN"
    else
      die "no runtime installed yet; run install.sh first"
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
