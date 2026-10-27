# Envoy one-command installer for the three agent hosts, Windows host side.
#
#   irm https://raw.githubusercontent.com/regrevia/Envoy/main/integrations/install.ps1 | iex
#   powershell -ExecutionPolicy Bypass -File install.ps1 claude-code
#   powershell -ExecutionPolicy Bypass -File install.ps1 codex
#
# Same contract as install.sh: one private pinned runtime, then the host plugin,
# then the one-time Chromium download. With no argument it installs every host it
# can detect.
#
# Environment overrides:
#   ENVOY_VERSION       runtime version to install (default: release manifest)
#   ENVOY_HOME          private runtime root (default: %LOCALAPPDATA%\envoy-dsh\runtime)
#   ENVOY_RUNTIME_BIN   explicit runtime to reuse instead of installing one
#   ENVOY_SETUP_BROWSER 1 (default) downloads Chromium at install time; 0 skips
#   DSH_PROFILE         Harness profile for the DSH bundle (default: web)
#   ENVOY_DRY_RUN=1     print the commands instead of running them
#
# Not yet exercised on a real Windows host in this repo: install.ps1 is written to
# the same contract as install.sh, but scripts/check_installer_offline.sh covers
# the POSIX script only. COMPATIBILITY.md records the Windows row as untested.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = ""
)

$ErrorActionPreference = "Stop"

$Script:Repo = "regrevia/Envoy"
$Script:ManifestUrl = "https://raw.githubusercontent.com/$($Script:Repo)/main/integrations/release-manifest.json"
$Script:RuntimeHome = if ($env:ENVOY_HOME) { $env:ENVOY_HOME } else { Join-Path $env:LOCALAPPDATA "envoy-dsh\runtime" }
$Script:RuntimeBin = Join-Path $Script:RuntimeHome "Scripts\envoy.exe"
$Script:RuntimePin = if ($env:ENVOY_RUNTIME_PIN) { $env:ENVOY_RUNTIME_PIN } else { Join-Path $HOME ".envoy\runtime-path" }
$Script:ShimDir = Join-Path $HOME ".envoy\bin"
$Script:DshProfile = if ($env:DSH_PROFILE) { $env:DSH_PROFILE } else { "web" }
$Script:DryRun = ($env:ENVOY_DRY_RUN -eq "1")
$Script:SetupBrowser = if ($env:ENVOY_SETUP_BROWSER) { $env:ENVOY_SETUP_BROWSER } else { "1" }
$Script:RuntimeVersion = ""

function Stop-Install {
    param([string]$Message)
    Write-Host "envoy install: $Message" -ForegroundColor Red
    exit 1
}

# Runs a native command, or prints it when ENVOY_DRY_RUN=1. Native stderr from
# the host CLIs is noise we do not want to turn into a terminating error.
function Invoke-Step {
    param([string]$FilePath, [string[]]$Arguments = @())
    if ($Script:DryRun) {
        Write-Host ("  + {0} {1}" -f $FilePath, ($Arguments -join " "))
        return
    }
    $display = "$FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "envoy install: failed: $display" -ForegroundColor Yellow
        throw "command failed: $display"
    }
}

function Resolve-EnvoyVersion {
    if ($env:ENVOY_VERSION) { return $env:ENVOY_VERSION }
    try {
        $manifest = Invoke-RestMethod -Uri $Script:ManifestUrl -UseBasicParsing -TimeoutSec 20
    } catch {
        Stop-Install "could not read the release manifest; set ENVOY_VERSION"
    }
    if (-not $manifest.version) { Stop-Install "the release manifest has no version; set ENVOY_VERSION" }
    return [string]$manifest.version
}

function Test-VersionAtLeast {
    param([string]$Current, [string]$Required)
    if ($Current -eq $Required) { return $true }
    $left = $Current.Split(".")
    $right = $Required.Split(".")
    for ($i = 0; $i -lt [Math]::Max($left.Length, $right.Length); $i++) {
        $a = if ($i -lt $left.Length) { [int]$left[$i] } else { 0 }
        $b = if ($i -lt $right.Length) { [int]$right[$i] } else { 0 }
        if ($a -ne $b) { return $a -gt $b }
    }
    return $true
}

# `envoy --version` prints "envoy <version>"; anything unparsable is unknown.
function Get-RuntimeVersion {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    try {
        $raw = & $Path --version 2>$null
    } catch {
        return ""
    }
    if (-not $raw) { return "" }
    $match = [regex]::Match([string]$raw, "\b([0-9]+(\.[0-9]+)+)\b")
    if ($match.Success) { return $match.Groups[1].Value }
    return ""
}

function Get-PinnedRuntime {
    if (-not (Test-Path -LiteralPath $Script:RuntimePin)) { return "" }
    $candidate = (Get-Content -LiteralPath $Script:RuntimePin -TotalCount 1)
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { return [string]$candidate }
    return ""
}

function Set-PinnedRuntime {
    param([string]$Path)
    if ($Script:DryRun) {
        Write-Host ("  + pin {0} -> {1}" -f $Path, $Script:RuntimePin)
        return
    }
    $parent = Split-Path -Parent $Script:RuntimePin
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -LiteralPath $Script:RuntimePin -Value $Path -Encoding ASCII
}

# Every host-facing step resolves the binary through here, so the plugin and the
# runtime that serves it are the same one.
function Get-EnvoyBinary {
    $pinned = Get-PinnedRuntime
    if ($pinned) { return $pinned }
    if (Test-Path -LiteralPath $Script:RuntimeBin) { return $Script:RuntimeBin }
    $onPath = Get-Command envoy -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    Stop-Install "no envoy runtime found; run the installer without a host argument first"
}

function Confirm-Checksum {
    param([string]$Path, [string]$FileName)
    $sumsUrl = "https://github.com/$($Script:Repo)/releases/download/v$($Script:RuntimeVersion)/SHA256SUMS"
    $sums = Join-Path ([System.IO.Path]::GetTempPath()) ("envoy-sums-" + [guid]::NewGuid().ToString("N"))
    try {
        Invoke-WebRequest -Uri $sumsUrl -OutFile $sums -UseBasicParsing -TimeoutSec 60
        $line = Get-Content -LiteralPath $sums | Where-Object { $_ -match [regex]::Escape($FileName) } | Select-Object -First 1
        if (-not $line) { Stop-Install "SHA256SUMS has no entry for $FileName" }
        $expected = ($line -split "\s+")[0].ToLowerInvariant()
        $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { Stop-Install "checksum mismatch for $FileName" }
    } finally {
        if (Test-Path -LiteralPath $sums) { Remove-Item -LiteralPath $sums -Force -ErrorAction SilentlyContinue }
    }
}

function Add-ToUserPath {
    param([string]$Directory)
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($current) { $parts = $current -split ";" | Where-Object { $_ } }
    if ($parts -notcontains $Directory) {
        $updated = (($parts + $Directory) -join ";")
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
        Write-Host "envoy install: added $Directory to the user PATH (open a new terminal)"
    }
}

# The two plugin hosts spawn a bare `envoy`; a .cmd shim avoids symlink
# privileges and points at the pinned runtime rather than a copy.
function Set-ExposedRuntime {
    $binary = Get-EnvoyBinary
    if ($Script:DryRun) {
        Write-Host ("  + shim {0} -> {1}" -f (Join-Path $Script:ShimDir "envoy.cmd"), $binary)
        return
    }
    if (-not (Test-Path -LiteralPath $Script:ShimDir)) {
        New-Item -ItemType Directory -Path $Script:ShimDir -Force | Out-Null
    }
    $shim = Join-Path $Script:ShimDir "envoy.cmd"
    $content = "@echo off`r`n`"$binary`" %*`r`n"
    Set-Content -LiteralPath $shim -Value $content -Encoding ASCII
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath -or ($userPath -split ";" -notcontains $Script:ShimDir)) {
        Add-ToUserPath $Script:ShimDir
    }
}

function Install-Runtime {
    param([string]$Version)
    $wheel = "envoy_cli-$Version-py3-none-any.whl"
    $url = "https://github.com/$($Script:Repo)/releases/download/v$Version/$wheel"
    Write-Host "envoy install: runtime $Version -> $($Script:RuntimeHome)"
    if ($Script:DryRun) {
        Write-Host ("  + venv {0}" -f $Script:RuntimeHome)
        Write-Host ("  + download {0}" -f $url)
        Write-Host ("  + pip install {0}" -f $wheel)
        Set-PinnedRuntime $Script:RuntimeBin
        Provision-Browser $Script:RuntimeBin
        return
    }
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        $pythonOk = Test-Path -LiteralPath (Join-Path $Script:RuntimeHome "Scripts\python.exe")
        if (-not $pythonOk) {
            if (Test-Path -LiteralPath $Script:RuntimeHome) { Remove-Item -LiteralPath $Script:RuntimeHome -Recurse -Force }
            Invoke-Step $uv.Source @("venv", "--quiet", "--python", "3.11", $Script:RuntimeHome)
        }
        Invoke-Step $uv.Source @("pip", "install", "--quiet", "--python", (Join-Path $Script:RuntimeHome "Scripts\python.exe"), $url)
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { Stop-Install "neither uv nor python is available; install Python 3.11+ (or uv) and re-run" }
        if (-not (Test-Path -LiteralPath (Join-Path $Script:RuntimeHome "Scripts\python.exe"))) {
            if (Test-Path -LiteralPath $Script:RuntimeHome) { Remove-Item -LiteralPath $Script:RuntimeHome -Recurse -Force }
            Invoke-Step $python.Source @("-m", "venv", $Script:RuntimeHome)
        }
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) $wheel
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 300
        Confirm-Checksum $tmp $wheel
        Invoke-Step (Join-Path $Script:RuntimeHome "Scripts\python.exe") @("-m", "pip", "install", "--quiet", "--upgrade", $tmp)
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $Script:RuntimeBin)) { Stop-Install "runtime install finished without $($Script:RuntimeBin)" }
    Set-PinnedRuntime $Script:RuntimeBin
    Set-ExposedRuntime
    Provision-Browser $Script:RuntimeBin
}

# Chromium belongs to install time, not to the first link: a read that downloads
# it would sit inside the host's tool deadline and look like a failed page.
function Provision-Browser {
    param([string]$Binary)
    if ($Script:SetupBrowser -ne "1") {
        Write-Host "envoy install: skipped browser setup (ENVOY_SETUP_BROWSER=0); run ``envoy browser setup`` before the first link"
        return
    }
    Write-Host "envoy install: browser runtime (one-time, about 1-2 minutes)"
    try {
        Invoke-Step $Binary @("browser", "setup")
    } catch {
        Write-Host "envoy install: browser setup did not finish; run ``envoy browser setup`` later" -ForegroundColor Yellow
    }
}

function Confirm-Runtime {
    $version = Resolve-EnvoyVersion
    $Script:RuntimeVersion = $version

    # One runtime, one absolute path: a pinned runtime wins over PATH so a stale
    # envoy earlier in PATH cannot hijack the plugin later.
    $pinned = Get-PinnedRuntime
    if ($pinned) {
        $current = Get-RuntimeVersion $pinned
        if ($current -and (Test-VersionAtLeast $current $version)) {
            Write-Host "envoy install: using envoy $current from $pinned (pinned)"
            return
        }
        Write-Host "envoy install: pinned envoy $(if ($current) { $current } else { 'unknown' }) is older than $version" -ForegroundColor Yellow
    }

    if ($env:ENVOY_RUNTIME_BIN) {
        $current = Get-RuntimeVersion $env:ENVOY_RUNTIME_BIN
        if (-not $current) { Stop-Install "ENVOY_RUNTIME_BIN=$($env:ENVOY_RUNTIME_BIN) is not runnable" }
        if (-not (Test-VersionAtLeast $current $version)) {
            Stop-Install "ENVOY_RUNTIME_BIN is envoy $current, older than $version"
        }
        Set-PinnedRuntime $env:ENVOY_RUNTIME_BIN
        Write-Host "envoy install: using envoy $current from $($env:ENVOY_RUNTIME_BIN) (ENVOY_RUNTIME_BIN)"
        return
    }

    $current = Get-RuntimeVersion $Script:RuntimeBin
    if ($current -and (Test-VersionAtLeast $current $version)) {
        Set-PinnedRuntime $Script:RuntimeBin
        Write-Host "envoy install: using envoy $current from $($Script:RuntimeBin)"
        Set-ExposedRuntime
        return
    }

    $onPath = Get-Command envoy -ErrorAction SilentlyContinue
    if ($onPath) {
        $current = Get-RuntimeVersion $onPath.Source
        if ($current -and (Test-VersionAtLeast $current $version)) {
            Set-PinnedRuntime $onPath.Source
            Write-Host "envoy install: using envoy $current from $($onPath.Source) (on PATH)"
            return
        }
        Write-Host "envoy install: envoy $(if ($current) { $current } else { 'unknown' }) on PATH is older than $version; installing the private runtime" -ForegroundColor Yellow
    }

    Install-Runtime $version
}

function Find-Dsh {
    if ($env:DSH_CLI -and (Test-Path -LiteralPath $env:DSH_CLI)) { return $env:DSH_CLI }
    $onPath = Get-Command dsh -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"),
        (Join-Path $env:ProgramFiles "DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return ""
}

function Install-Dsh {
    $dsh = Find-Dsh
    if (-not $dsh) { Stop-Install "the dsh CLI was not found; install DeepSeek Harness Desktop, or set DSH_CLI to its dsh binary" }
    Write-Host "envoy install: DeepSeek Harness bundle (profile $($Script:DshProfile))"
    $tarball = "https://github.com/$($Script:Repo)/releases/download/v$($Script:RuntimeVersion)/envoy-dsh-$($Script:RuntimeVersion).tgz"
    Invoke-Step $dsh @("plugin", "--profile", $Script:DshProfile, "add", $tarball)
    Write-Host "Restart DeepSeek Harness so the bundle mounts."
    Confirm-Health
}

function Install-ClaudeCode {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Stop-Install "the claude CLI is not on PATH; install Claude Code first" }
    Write-Host "envoy install: Claude Code plugin"
    Invoke-Step "claude" @("plugin", "marketplace", "add", "regrevia/Envoy")
    Invoke-Step "claude" @("plugin", "install", "envoy@envoy")
    Write-Host "Restart Claude Code, or run /plugin to confirm Envoy is enabled."
    Confirm-Health
}

function Install-Codex {
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { Stop-Install "the codex CLI is not on PATH; install Codex first" }
    Write-Host "envoy install: Codex plugin"
    Invoke-Step "codex" @("plugin", "marketplace", "add", "https://github.com/$($Script:Repo).git")
    Invoke-Step "codex" @("plugin", "add", "envoy@envoy")
    Write-Host "envoy install: Codex prompt hook"
    Invoke-Step (Get-EnvoyBinary) @("hook", "install", "--host", "codex")
    Write-Host "Restart Codex so the plugin and hook load."
    Confirm-Health
}

# Hosts that only take an MCP server have no plugin manager, so the install is
# one JSON entry merged into their existing config.
function Install-Mcp {
    $binary = Get-EnvoyBinary
    Write-Host "envoy install: MCP entry -> $binary"
    if ($Script:DryRun) {
        Write-Host "  + add mcpServers.envoy to each detected host config"
        return
    }
    $candidates = @(
        (Join-Path $env:APPDATA "Claude\claude_desktop_config.json"),
        (Join-Path $HOME ".cursor\mcp.json"),
        (Join-Path $HOME ".codeium\windsurf\mcp_config.json")
    )
    $wrote = @()
    foreach ($path in $candidates) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $raw = Get-Content -LiteralPath $path -Raw
            $data = if ($raw) { $raw | ConvertFrom-Json } else { [pscustomobject]@{} }
        } catch {
            Write-Host "envoy install: skipped $path (invalid JSON)" -ForegroundColor Yellow
            continue
        }
        if (-not $data.PSObject.Properties["mcpServers"]) {
            $data | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([pscustomobject]@{})
        }
        $entry = [pscustomobject]@{ command = $binary; args = @("mcp") }
        $data.mcpServers | Add-Member -MemberType NoteProperty -Name "envoy" -Value $entry -Force
        $stamp = (Get-Date).ToString("yyyyMMddHHmmss")
        Copy-Item -LiteralPath $path -Destination "$path.envoy-$stamp" -Force
        $data | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding UTF8
        $wrote += $path
    }
    if ($wrote.Count -gt 0) {
        foreach ($path in $wrote) { Write-Host "envoy install: wrote $path" }
    } else {
        $template = Join-Path $HOME ".envoy\mcp.json"
        $parent = Split-Path -Parent $template
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        [pscustomobject]@{ mcpServers = [pscustomobject]@{ envoy = [pscustomobject]@{ command = $binary; args = @("mcp") } } } |
            ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $template -Encoding UTF8
        Write-Host "envoy install: no host config found; template written to $template"
    }
    Write-Host "Restart the host so it launches the envoy MCP server."
}

function Install-Skill {
    $dest = if ($env:ENVOY_SKILL_DIR) { $env:ENVOY_SKILL_DIR } else { Join-Path $HOME ".envoy\skill\envoy" }
    $url = "https://github.com/$($Script:Repo)/releases/download/v$($Script:RuntimeVersion)/envoy-skill.zip"
    Write-Host "envoy install: skill -> $dest"
    if ($Script:DryRun) {
        Write-Host ("  + download {0}" -f $url)
        Write-Host ("  + expand to {0}" -f $dest)
        return
    }
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) ("envoy-skill-" + [guid]::NewGuid().ToString("N") + ".zip")
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 120
    Confirm-Checksum $zip "envoy-skill.zip"
    if (-not (Test-Path -LiteralPath $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }
    Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Write-Host "Point the host at $dest, then restart it."
}

# A finished install that cannot read a page is still a failed install.
function Confirm-Health {
    $binary = Get-EnvoyBinary
    if ($Script:DryRun) {
        Write-Host "  + envoy doctor --json"
        return
    }
    try {
        $raw = & $binary doctor --json 2>$null
        $data = $raw | ConvertFrom-Json
    } catch {
        Write-Host "envoy install: envoy doctor did not answer; run it yourself if the first read fails" -ForegroundColor Yellow
        return
    }
    $version = if ($data.cli_version) { $data.cli_version } elseif ($data.version) { $data.version } else { "unknown" }
    $protocol = if ($data.protocol_version) { $data.protocol_version } else { "unknown" }
    Write-Host "envoy install: runtime $version, protocol $protocol"
}

function Install-Auto {
    Confirm-Runtime
    $installed = $false
    if (Find-Dsh) { Install-Dsh; $installed = $true }
    if (Get-Command claude -ErrorAction SilentlyContinue) { Install-ClaudeCode; $installed = $true }
    if (Get-Command codex -ErrorAction SilentlyContinue) { Install-Codex; $installed = $true }
    if (-not $installed) {
        Write-Host "envoy install: no host CLI detected; writing the MCP entry instead" -ForegroundColor Yellow
        Install-Mcp
    }
    Confirm-Health
}

$command = if ($Target) { $Target.ToLowerInvariant() } else { "" }
switch ($command) {
    "" { Install-Auto }
    "auto" { Install-Auto }
    "all" { Install-Auto }
    "dsh" { Confirm-Runtime; Install-Dsh }
    "claude-code" { Confirm-Runtime; Install-ClaudeCode }
    "claude" { Confirm-Runtime; Install-ClaudeCode }
    "codex" { Confirm-Runtime; Install-Codex }
    "mcp" { Confirm-Runtime; Install-Mcp; Confirm-Health }
    "skill" { Confirm-Runtime; Install-Skill; Confirm-Health }
    "path" {
        $pinned = Get-PinnedRuntime
        if ($pinned) { Write-Host $pinned }
        elseif (Test-Path -LiteralPath $Script:RuntimeBin) { Write-Host $Script:RuntimeBin }
        else { Stop-Install "no runtime installed yet; run install.ps1 first" }
    }
    "help" { Get-Help $PSCommandPath }
    "-h" { Get-Help $PSCommandPath }
    "--help" { Get-Help $PSCommandPath }
    default {
        Write-Host "usage: install.ps1 [dsh|claude-code|codex|mcp|skill|all|path]" -ForegroundColor Red
        exit 2
    }
}
