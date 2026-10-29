// Cross-platform launcher for the Envoy MCP bridge.
//
// The bundle used to spawn a POSIX shell running a shell script. Windows ships
// no such shell, so the bridge never mounted there. This is the same launcher
// expressed once in JavaScript, run as `node -e` straight from
// cordis.patch.yml: it is inlined rather than shipped as a file because the MCP
// client spawns with the host's cwd, so no relative path inside the package is
// dependable.
//
// It keeps the documented contract: resolve `ENVOY_BIN`, then `envoy` on PATH,
// then the private runtime under the Envoy data dir; compare `envoy --version`
// against the required minimum; provision or upgrade the private runtime from
// the release wheel when needed; then exec `envoy mcp` with stdio inherited so
// the MCP client talks to the Envoy process directly.
//
// Windows specifics: the data dir is %LOCALAPPDATA%\envoy-dsh\runtime, the
// executables are Scripts\envoy.exe and Scripts\python.exe, and `uv`/`python`
// are resolved through PATH with PATHEXT because they are .cmd/.exe shims.
//
// This file is the source of truth; tests/test_dsh_plugin.py extracts the
// script from cordis.patch.yml and asserts the two stay identical.

const { spawn, spawnSync } = require('node:child_process')
const { existsSync, mkdirSync, mkdtempSync, rmSync } = require('node:fs')
const { homedir, tmpdir } = require('node:os')
const { delimiter, join } = require('node:path')

const IS_WINDOWS = process.platform === 'win32'
const VERSION = process.env.ENVOY_DSH_VERSION || '0.1.34'
const REQUIRED = process.env.ENVOY_DSH_REQUIRED_RUNTIME || VERSION
const WHEEL =
  'https://github.com/regrevia/Envoy/releases/download/v' + VERSION +
  '/envoy_cli-' + VERSION + '-py3-none-any.whl'

function fail(message) {
  process.stderr.write('envoy-dsh: ' + message + '\n')
  process.exit(1)
}

function runtimeDir() {
  if (process.env.ENVOY_DSH_RUNTIME_DIR) return process.env.ENVOY_DSH_RUNTIME_DIR
  if (IS_WINDOWS) {
    const base = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local')
    return join(base, 'envoy-dsh', 'runtime')
  }
  const base = process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share')
  return join(base, 'envoy-dsh', 'runtime')
}

const RUNTIME = runtimeDir()
const BIN_DIR = IS_WINDOWS ? join(RUNTIME, 'Scripts') : join(RUNTIME, 'bin')
const RUNTIME_BIN = IS_WINDOWS ? join(BIN_DIR, 'envoy.exe') : join(BIN_DIR, 'envoy')
const RUNTIME_PY = IS_WINDOWS ? join(BIN_DIR, 'python.exe') : join(BIN_DIR, 'python')

// Resolve a command the way a shell would, honouring PATHEXT on Windows so
// `uv.cmd` and `python.exe` are found.
function which(command) {
  if (!command) return ''
  if (command.indexOf('/') >= 0 || command.indexOf('\\') >= 0) {
    return existsSync(command) ? command : ''
  }
  const exts = IS_WINDOWS
    ? (process.env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)
    : ['']
  const dirs = (process.env.PATH || '').split(delimiter).filter(Boolean)
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = join(dir, command + ext)
      if (existsSync(candidate)) return candidate
    }
  }
  return ''
}

// Node cannot execute a .cmd/.bat directly (it spawns with no shell), and
// `install.ps1` exposes `envoy.cmd` as the PATH shim, so on Windows the
// resolved command is very likely a batch file. Run those through cmd.exe,
// otherwise the version probe silently fails and a perfectly good runtime is
// reported as `unknown` and rejected.
function needsShell(binary) {
  return IS_WINDOWS && /\.(cmd|bat)$/i.test(binary)
}

function shellInvocation(binary, args) {
  if (!needsShell(binary)) return [binary, args]
  const comspec = process.env.COMSPEC || 'cmd.exe'
  const line = '"' + binary + '"' + (args.length ? ' ' + args.join(' ') : '')
  return [comspec, ['/d', '/s', '/c', line]]
}

function runSync(binary, args, options) {
  const [command, commandArgs] = shellInvocation(binary, args)
  return spawnSync(command, commandArgs, options)
}

function versionOf(binary) {
  const result = runSync(binary, ['--version'], { encoding: 'utf8', timeout: 30000 })
  if (result.error || result.status !== 0) return ''
  const text = String(result.stdout || '') + ' ' + String(result.stderr || '')
  const match = /(\d+\.\d+\.\d+[0-9A-Za-z.+-]*)/.exec(text)
  return match ? match[1] : ''
}

function atLeast(have, want) {
  const parse = (v) => v.split(/[.+-]/)
  const a = parse(have)
  const b = parse(want)
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if (a[i] === undefined) return false
    if (b[i] === undefined) return true
    const left = /^\d+$/.test(a[i]) ? Number(a[i]) : a[i]
    const right = /^\d+$/.test(b[i]) ? Number(b[i]) : b[i]
    if (left === right) continue
    if (typeof left === 'number' && typeof right === 'number') return left > right
    return String(left) > String(right)
  }
  return true
}

function step(command, args, label) {
  const result = runSync(command, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  if (result.error || result.status !== 0) {
    const detail = String(result.stderr || result.stdout || result.error.message || '').trim()
    fail(label + (detail ? ': ' + detail.split('\n')[0] : ''))
  }
}

function pythonOk(binary) {
  if (!existsSync(binary)) return false
  const result = runSync(
    binary,
    ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'],
    { timeout: 30000 },
  )
  return !result.error && result.status === 0
}

// Download the release wheel to a temp file first. Handing the GitHub URL
// straight to pip/uv fails with `HTTP error 403` on Windows: the release asset
// is served through a redirect and the installer's request is rejected. Fetch
// it ourselves, then install from the local file.
function fetchWheel() {
  const dir = mkdtempSync(join(tmpdir(), 'envoy-dsh-'))
  const file = join(dir, WHEEL.slice(WHEEL.lastIndexOf('/') + 1))
  const uv = which('uv')
  const python = existsSync(RUNTIME_PY) ? RUNTIME_PY : which('python3') || which('python')
  let fetched = false
  if (uv) {
    const result = runSync(uv, ['pip', 'download', '--quiet', '--no-deps',
      '--dest', dir, WHEEL], { encoding: 'utf8', timeout: 300000 })
    fetched = !result.error && result.status === 0 && existsSync(file)
  }
  if (!fetched && python) {
    // curl/wget are present on macOS and Linux; on Windows let Python do it.
    const script =
      'import sys,urllib.request;' +
      'urllib.request.urlretrieve(sys.argv[1], sys.argv[2])'
    const result = runSync(python, ['-c', script, WHEEL, file],
      { encoding: 'utf8', timeout: 300000 })
    fetched = !result.error && result.status === 0 && existsSync(file)
  }
  if (!fetched) {
    rmSync(dir, { recursive: true, force: true })
    fail('failed to download ' + WHEEL)
  }
  return { dir, file }
}

function provision() {
  mkdirSync(RUNTIME, { recursive: true })
  const uv = which('uv')
  // `python3` does not exist on Windows; `python` is the interpreter there.
  const system = which('python3') || which('python')
  if (!uv && !system) {
    fail(
      'no Envoy runtime and no uv/python to install one; install uv from ' +
      'https://docs.astral.sh/uv/ then restart Harness',
    )
  }
  if (uv) {
    if (!pythonOk(RUNTIME_PY)) {
      rmSync(RUNTIME, { recursive: true, force: true })
      step(
        uv,
        ['venv', '--quiet', '--python', process.env.ENVOY_PYTHON || '3.11', RUNTIME],
        'cannot provision Python ' + (process.env.ENVOY_PYTHON || '3.11') + ' for ' + RUNTIME,
      )
    }
  } else if (!pythonOk(RUNTIME_PY)) {
    if (!pythonOk(system)) {
      fail('python on PATH is older than 3.10; envoy needs >= 3.10 (install uv, then restart Harness)')
    }
    rmSync(RUNTIME, { recursive: true, force: true })
    step(system, ['-m', 'venv', RUNTIME], 'cannot create ' + RUNTIME)
  }

  const wheel = fetchWheel()
  try {
    if (uv) {
      step(uv, ['pip', 'install', '--quiet', '--upgrade', '--python', RUNTIME_PY, wheel.file],
        'failed to install the Envoy wheel')
    } else {
      step(RUNTIME_PY, ['-m', 'pip', 'install', '--quiet', '--upgrade', wheel.file],
        'failed to install the Envoy wheel')
    }
  } finally {
    rmSync(wheel.dir, { recursive: true, force: true })
  }
}

function resolveRuntime() {
  let binary = ''
  let external = false
  if (process.env.ENVOY_BIN) {
    binary = process.env.ENVOY_BIN
    external = true
  } else {
    const onPath = which('envoy')
    if (onPath) {
      binary = onPath
      external = true
    } else if (existsSync(RUNTIME_BIN)) {
      binary = RUNTIME_BIN
    }
  }

  if (binary) {
    const have = versionOf(binary)
    if (have && atLeast(have, REQUIRED)) return binary
    if (external) {
      fail(
        binary + ' is Envoy ' + (have || 'unknown') + ', this DSH bundle needs >=' + REQUIRED +
        '; upgrade that install (uv tool upgrade envoy-cli, or brew/uv accordingly) or unset ' +
        'ENVOY_BIN / PATH envoy so the bundle provisions its own runtime',
      )
    }
    process.stderr.write(
      'envoy-dsh: upgrading the bundled runtime from ' + (have || 'unknown') + ' to ' + VERSION +
      '; the Envoy settings and downloaded models are kept\n',
    )
  }

  provision()
  if (!existsSync(RUNTIME_BIN)) fail('runtime install finished without ' + RUNTIME_BIN)
  if (!versionOf(RUNTIME_BIN)) fail('cannot run ' + RUNTIME_BIN + ' --version after install')
  return RUNTIME_BIN
}

const binary = resolveRuntime()

// Hand the process over to `envoy mcp`. The MCP client talks to this process
// over stdio, so the child's stdio is wired straight to this process's own
// handles rather than 'inherit': under a cmd.exe hop the inherited console
// handles are not the pipes the MCP client reads, and the handshake goes
// silent. Passing process.stdin/stdout/stderr keeps the same descriptors.
const env = Object.assign({}, process.env)
env.PATH = env.PATH ? BIN_DIR + delimiter + env.PATH : BIN_DIR
if (IS_WINDOWS && !env.PATHEXT) env.PATHEXT = '.COM;.EXE;.BAT;.CMD'

// A .cmd shim (what `install.ps1` puts on PATH) needs cmd.exe to start.
const [command, commandArgs] = shellInvocation(binary, ['mcp'])
const child = spawn(command, commandArgs, {
  stdio: [process.stdin, process.stdout, process.stderr],
  env,
})
child.on('error', (error) => fail('cannot start ' + binary + ': ' + error.message))
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  process.exit(code === null ? 1 : code)
})
