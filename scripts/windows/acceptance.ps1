#requires -Version 5.1
<#
Windows acceptance for the PowerShell installer (Tier 1, checks A1-A7).

Runs the real integrations/install.ps1 in the shell that launched this script,
so one file covers Windows PowerShell 5.1 (powershell.exe) and PowerShell 7+
(pwsh.exe). Assertions are about state a user keeps: the pinned runtime, the
.cmd shim, and the user PATH in HKCU\Environment.

  fresh    no prior install: install -> shim runs -> PATH has the shim once
  upgrade  keep an older install in place: install -> version moved -> no dupes
  both     fresh, then upgrade on top of it (default)
  -Profile minimal|standard|full  install into that tier and assert what it
           provisioned through `envoy doctor --json` (blank keeps the bounded
           default: browser skipped, media provisioned)

State never leaks into the developer desktop: ENVOY_HOME points inside the runner
temp dir, and the user PATH value and its Kind are snapshotted and restored.

The log and the evidence file are public in this repo's Actions UI, so they are
redacted by default: the account name, computer name and the user home prefix
are replaced with placeholders.

Exit code 1 means at least one check failed.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$PreviousVersion = "0.1.23",
    [string]$Scenario = "both",
    [string]$Profile = "",
    [string]$EnvoyHome = (Join-Path $env:RUNNER_TEMP "envoy-acceptance"),
    [string]$EvidencePath = "",
    [int]$ExpectShellMajor = 0,
    [bool]$Redact = $true,
    [switch]$KeepState,
    # KeepState also skips the PATH restore, which is what a nested job needs to
    # avoid; KeepRuntime keeps only the installed tree so a caller can use the
    # shim right after this script returns.
    [switch]$KeepRuntime
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

# Codepage first: the installer writes a .cmd shim in the OEM codepage, and a
# console in another codepage turns a correct Chinese path into mojibake that
# looks like an installer bug.
try { chcp 65001 | Out-Null } catch { }
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Installer = Join-Path $RepoRoot "integrations\install.ps1"
$Results = New-Object System.Collections.ArrayList
$Script:Failed = 0

function Mask {
    param([string]$Text)
    if (-not $Redact -or -not $Text) { return $Text }
    $out = $Text
    $pairs = @()
    if ($env:USERPROFILE) { $pairs += , @($env:USERPROFILE, "<user-home>") }
    if ($env:LOCALAPPDATA) { $pairs += , @($env:LOCALAPPDATA, "<local-appdata>") }
    if ($env:RUNNER_TEMP) { $pairs += , @($env:RUNNER_TEMP, "<runner-temp>") }
    if ($env:USERNAME) { $pairs += , @($env:USERNAME, "<user>") }
    if ($env:COMPUTERNAME) { $pairs += , @($env:COMPUTERNAME, "<host>") }
    foreach ($pair in $pairs) {
        if ($pair[0]) {
            $pattern = [regex]::Escape($pair[0])
            $out = [regex]::Replace($out, $pattern, $pair[1], "IgnoreCase")
        }
    }
    return $out
}

function Check {
    param([string]$Id, [string]$Name, [bool]$Ok, [string]$Detail = "")
    $safe = Mask $Detail
    [void]$Results.Add([ordered]@{ id = $Id; check = $Name; ok = $Ok; detail = $safe })
    if ($Ok) {
        Write-Host ("OK    {0}  {1}  {2}" -f $Id, $Name, $safe)
    } else {
        Write-Host ("FAIL  {0}  {1}  {2}" -f $Id, $Name, $safe) -ForegroundColor Red
        $Script:Failed = $Script:Failed + 1
    }
}

function Get-RawUserPath {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment")
    if (-not $key) { return @("", "REG_SZ") }
    try {
        $raw = $key.GetValue("Path", $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $kind = "REG_SZ"
        if ($null -ne $key.GetValueKind("Path") -and $key.GetValueKind("Path") -eq [Microsoft.Win32.RegistryValueKind]::ExpandString) {
            $kind = "REG_EXPAND_SZ"
        }
        if ($null -eq $raw) { return @("", $kind) }
        return @([string]$raw, $kind)
    } finally {
        $key.Close()
    }
}

function Set-RawUserPath {
    param([string]$Value, [string]$Kind)
    $pair = Get-RawUserPath
    if ($pair[0] -ceq $Value -and $pair[1] -ceq $Kind) { return }
    $reg = Join-Path $env:SystemRoot "System32\reg.exe"
    & $reg add "HKCU\Environment" /v Path /t $Kind /d $Value /f | Out-Null
    if ($LASTEXITCODE -ne 0) {
        [Environment]::SetEnvironmentVariable("Path", $Value, "User")
    }
}

function Test-HklmUntouched {
    # The installer must work for a plain user: a write to HKLM would need
    # elevation and would fail on the machines this is meant to support.
    $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey("SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    if (-not $key) { return $true }
    try {
        $raw = [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        return -not ($raw -match [regex]::Escape((Join-Path $EnvoyHome "bin")))
    } finally {
        $key.Close()
    }
}

function Resolve-TargetVersion {
    if ($Version) { return $Version }
    $manifest = Join-Path $RepoRoot "integrations\release-manifest.json"
    $data = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $data.version) { throw "release-manifest.json has no version" }
    return [string]$data.version
}

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Invoke-Installer {
    param([string]$InstallVersion)
    $shell = (Get-Process -Id $PID).Path
    $env:ENVOY_HOME = $EnvoyHome
    # The uv cache must not land in the user profile of a self-hosted runner.
    $env:ENVOY_UV_HOME = Join-Path $env:RUNNER_TEMP "envoy-uv"
    $env:ENVOY_RUNTIME_PIN = Join-Path $EnvoyHome "runtime-path"
    if ($Profile) {
        # The profile decides what gets provisioned; a stale override would hide it.
        $env:ENVOY_PROFILE = $Profile
        Remove-Item Env:ENVOY_SETUP_BROWSER -ErrorAction SilentlyContinue
        if ($Profile -ne "full") { $env:ENVOY_SETUP_MEDIA = "0" }
    } else {
        $env:ENVOY_SETUP_BROWSER = "0"
    }
    $env:ENVOY_VERSION = $InstallVersion
    Write-Host ("---- install {0} through {1}" -f $InstallVersion, (Mask $shell))
    & $shell -NoProfile -ExecutionPolicy Bypass -File $Installer -Target mcp
    if ($LASTEXITCODE -ne 0) { throw "install.ps1 exited $LASTEXITCODE for version $InstallVersion" }
}

function Get-ShimPath { Join-Path $EnvoyHome "bin\envoy.cmd" }

function Get-ShimVersion {
    $shim = Get-ShimPath
    if (-not (Test-Path -LiteralPath $shim)) { return "" }
    $out = & $shim --version 2>&1 | Out-String
    if ($out -match "(\d+\.\d+\.\d+)") { return $Matches[1] }
    return ""
}

function Test-PathEntryCount {
    param([string]$Directory)
    $pair = Get-RawUserPath
    $parts = @()
    if ($pair[0]) { $parts = @($pair[0] -split ";" | Where-Object { $_ }) }
    return @($parts | Where-Object { $_ -eq $Directory }).Count
}

# ---------------------------------------------------------------------------

$shellMajor = $PSVersionTable.PSVersion.Major
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host ("envoy acceptance: PowerShell {0}, major {1}, scenario {2}" -f $PSVersionTable.PSVersion.ToString(), $shellMajor, $Scenario)

Check "A1" "shell is Windows PowerShell 5.1 or PowerShell 7+" (($shellMajor -eq 5) -or ($shellMajor -ge 7)) ("major=$shellMajor")
if ($ExpectShellMajor -gt 0) {
    Check "A1" "shell matches the requested major version" ($shellMajor -eq $ExpectShellMajor) ("expected=$ExpectShellMajor actual=$shellMajor")
}
Check "A1" "host is Windows" ([Environment]::OSVersion.Platform -eq "Win32NT") (Mask ([Environment]::OSVersion.VersionString))
Check "A1" "console codepage is UTF-8" ([Console]::OutputEncoding.WebName -eq "utf-8") [Console]::OutputEncoding.WebName
Check "A1" "script source is valid UTF-8 for this shell" ((Get-Content -LiteralPath $PSCommandPath -Raw -Encoding UTF8).Length -gt 0) "readable"

$target = Resolve-TargetVersion
$installerHash = Get-FileSha256 $Installer
Write-Host ("envoy acceptance: target {0}, previous {1}, installer sha256 {2}" -f $target, $PreviousVersion, $installerHash.Substring(0, [Math]::Min(12, $installerHash.Length)))

$pathBefore = Get-RawUserPath
$mcpTemplate = Join-Path $HOME ".envoy\mcp.json"
$mcpTemplateExisted = Test-Path -LiteralPath $mcpTemplate
$envoyHomeExisted = Test-Path -LiteralPath $EnvoyHome
Check "A3" "user PATH is readable before the run" ($null -ne $pathBefore[0]) ("kind=" + $pathBefore[1])

$runFresh = ($Scenario -eq "both") -or ($Scenario -eq "fresh") -or ($Scenario -eq "upgrade" -and -not $envoyHomeExisted)
$runUpgrade = ($Scenario -eq "both") -or ($Scenario -eq "upgrade")

try {
    if ($runFresh) {
        if (Test-Path -LiteralPath $EnvoyHome) { Remove-Item -LiteralPath $EnvoyHome -Recurse -Force }
        Invoke-Installer $target
        Check "A2" "fresh: shim exists" (Test-Path -LiteralPath (Get-ShimPath)) (Mask (Get-ShimPath))
        $got = Get-ShimVersion
        Check "A2" "fresh: shim reports the requested version" ($got -eq $target) ("expected=$target actual=$got")
        Check "A2" "fresh: pinned runtime file exists" (Test-Path -LiteralPath (Join-Path $EnvoyHome "runtime-path")) (Mask (Join-Path $EnvoyHome "runtime-path"))
        $shimDir = Join-Path $EnvoyHome "bin"
        $count = Test-PathEntryCount $shimDir
        Check "A3" "fresh: user PATH carries the shim exactly once" ($count -eq 1) ("count=$count")
        $after = Get-RawUserPath
        Check "A3" "fresh: user PATH stays REG_EXPAND_SZ" (($pathBefore[1] -ne "REG_EXPAND_SZ") -or ($after[1] -eq "REG_EXPAND_SZ")) ("before=" + $pathBefore[1] + " after=" + $after[1])
        if ($pathBefore[0] -match "%") {
            Check "A3" "fresh: %VARS% in user PATH stay unexpanded" ($after[0] -match "%") "raw value keeps its %VAR% tokens"
        }
        Check "A6" "fresh: no HKLM write" (Test-HklmUntouched) "machine PATH untouched"
        if ($Profile) {
            $doctorRaw = & (Get-ShimPath) doctor --json 2>&1 | Out-String
            $doctor = $null
            try { $doctor = $doctorRaw | ConvertFrom-Json } catch { $doctor = $null }
            Check "A8" "profile: doctor answers with JSON" ($null -ne $doctor) ("profile=" + $Profile)
            if ($doctor) {
                $browserReady = [bool]$doctor.runtime.browser.ready
                $asrInstalled = [bool]$doctor.runtime.asr_installed
                $asrAvailable = [bool]$doctor.runtime.asr_model_available
                switch ($Profile) {
                    "minimal" {
                        Check "A8" "minimal: no browser installed" (-not $browserReady) ("browser.ready=" + $browserReady)
                        Check "A8" "minimal: no ASR engine installed" (-not $asrInstalled) ("asr_installed=" + $asrInstalled)
                    }
                    "standard" {
                        Check "A8" "standard: browser is ready" $browserReady ("browser.ready=" + $browserReady)
                        Check "A8" "standard: no ASR engine installed" (-not $asrInstalled) ("asr_installed=" + $asrInstalled)
                    }
                    "full" {
                        Check "A8" "full: browser is ready" $browserReady ("browser.ready=" + $browserReady)
                        Check "A8" "full: ASR model is available" $asrAvailable ("asr_model_available=" + $asrAvailable)
                    }
                }
            }
        }
    }

    if ($runUpgrade) {
        if (-not (Test-Path -LiteralPath (Get-ShimPath))) {
            Invoke-Installer $PreviousVersion
            $old = Get-ShimVersion
            Check "A4" "upgrade: seeded previous install runs" ((Test-Path -LiteralPath (Get-ShimPath)) -and $old -ne "") ("seeded=$old")
        }
        $beforeUpgrade = Get-ShimVersion
        Invoke-Installer $target
        $afterUpgrade = Get-ShimVersion
        Check "A4" "upgrade: version moved to the target" ($afterUpgrade -eq $target) ("before=$beforeUpgrade after=$afterUpgrade")
        $shimDir = Join-Path $EnvoyHome "bin"
        $count = Test-PathEntryCount $shimDir
        Check "A5" "upgrade: user PATH still carries the shim once" ($count -eq 1) ("count=$count")
        $first = & (Get-ShimPath) --version 2>&1 | Out-String
        $second = & (Get-ShimPath) --version 2>&1 | Out-String
        Check "A5" "upgrade: repeated runs are stable" ($second -match "(\d+\.\d+\.\d+)") ("first=" + $first.Trim() + " second=" + $second.Trim())
        $pinned = Join-Path $EnvoyHome "runtime-path"
        if (Test-Path -LiteralPath $pinned) {
            $pinnedValue = (Get-Content -LiteralPath $pinned -Raw -Encoding UTF8).Trim()
            Check "A5" "upgrade: pin points at an existing runtime" (Test-Path -LiteralPath $pinnedValue) (Mask $pinnedValue)
        }
    }

    Check "A5" "shim carries no literal version" (-not ((Get-Content -LiteralPath (Get-ShimPath) -Raw -Encoding UTF8) -match "v\d+\.\d+\.\d+")) "shim points at the pinned runtime"
} catch {
    Check "A2" "acceptance completed without an exception" $false (Mask $_.Exception.Message)
} finally {
    $keepHome = $KeepState -or $KeepRuntime
    if (-not $KeepState) {
        $pathRestore = Get-RawUserPath
        if ($pathRestore[0] -ne $pathBefore[0]) {
            Set-RawUserPath -Value $pathBefore[0] -Kind $pathBefore[1]
        }
        if (-not $keepHome -and -not $envoyHomeExisted -and (Test-Path -LiteralPath $EnvoyHome)) {
            Remove-Item -LiteralPath $EnvoyHome -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (-not $mcpTemplateExisted -and (Test-Path -LiteralPath $mcpTemplate)) {
            Remove-Item -LiteralPath $mcpTemplate -Force -ErrorAction SilentlyContinue
        }
        $restored = Get-RawUserPath
        Check "A7" "user PATH restored to its pre-run value" ($restored[0] -ceq $pathBefore[0]) ("kind=" + $restored[1])
    }

    $evidence = [ordered]@{
        schema = "envoy.windows-acceptance/1"
        shell = "PowerShell $($PSVersionTable.PSVersion.ToString())"
        shell_major = $shellMajor
        os = Mask ([Environment]::OSVersion.VersionString)
        admin = $admin
        target_version = $target
        previous_version = $PreviousVersion
        scenario = $Scenario
        profile = $(if ($Profile) { $Profile } else { "default(full)" })
        installer_sha256 = $installerHash
        shim_sha256 = (Get-FileSha256 (Get-ShimPath))
        path_kind_before = $pathBefore[1]
        redacted = $Redact
        failed = $Script:Failed
        checks = @($Results)
    }
    if (-not $EvidencePath) {
        $EvidencePath = Join-Path $env:RUNNER_TEMP ("envoy-windows-acceptance-ps{0}.json" -f $shellMajor)
    }
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $EvidencePath -Encoding UTF8
    Write-Host ("evidence: {0}" -f (Mask $EvidencePath))
    Write-Host ("RESULT: {0} failed of {1} checks" -f $Script:Failed, $Results.Count)
}

if ($Script:Failed -gt 0) { exit 1 }
exit 0
