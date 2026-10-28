#requires -Version 5.1
<#
Registers this machine as a self-hosted GitHub Actions runner for the Envoy repo
and installs it as a Windows service, so the acceptance workflow can run on a
machine that keeps real state (an old install, a real user PATH).

Run it on a dedicated Windows account or a dedicated VM, never on a desktop you
also use for work. Labels must match the workflow: self-hosted, windows, x64,
envoy-win.

  # 1. get a registration token (on any machine with gh, owner or admin rights)
  gh api -X POST repos/regrevia/Envoy/actions/runners/registration-token --jq .token

  # 2. register, on the Windows machine, in an elevated PowerShell
  .\scripts\windows\runner-setup.ps1 -Token <token>

The token expires after one hour and is not a secret worth keeping: it only
allows registering one runner. Never commit it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Repo = "regrevia/Envoy",
    [string]$RunnerRoot = "C:\actions-runner",
    [string]$Work = "_work",
    [string]$Name = $env:COMPUTERNAME.ToLowerInvariant(),
    [string[]]$Labels = @("windows", "x64", "envoy-win"),
    [switch]$Ephemeral
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if ($Labels -notcontains "windows") { throw "labels must include windows" }
if ($Labels -notcontains "x64") { throw "labels must include x64" }
if ($Labels -notcontains "envoy-win") { throw "labels must include envoy-win" }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "run this from an elevated PowerShell: the runner is installed as a service"
}

$env:RUNNER_ALLOW_RUNASROOT = $null
if (-not (Test-Path -LiteralPath $RunnerRoot)) { New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null }
Push-Location $RunnerRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $RunnerRoot "config.cmd"))) {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest" -UseBasicParsing
        $asset = $release.assets | Where-Object { $_.name -like "actions-runner-win-x64-*.zip" } | Select-Object -First 1
        if (-not $asset) { throw "no actions-runner-win-x64 asset in the latest runner release" }
        $zip = Join-Path $env:TEMP $asset.name
        Write-Host ("downloading {0}" -f $asset.name)
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $RunnerRoot -Force
        Remove-Item -LiteralPath $zip -Force
    }

    $labelArg = ($Labels -join ",")
    $configArgs = @(
        "--url", "https://github.com/$Repo",
        "--token", $Token,
        "--name", $Name,
        "--labels", $labelArg,
        "--work", $Work,
        "--unattended",
        "--replace"
    )
    if ($Ephemeral) { $configArgs += "--ephemeral" }

    Write-Host ("configuring runner {0} with labels {1}" -f $Name, $labelArg)
    & (Join-Path $RunnerRoot "config.cmd") @configArgs
    if ($LASTEXITCODE -ne 0) { throw "config.cmd exited $LASTEXITCODE" }

    Write-Host "installing the runner as a service"
    & (Join-Path $RunnerRoot "svc.cmd") install
    if ($LASTEXITCODE -ne 0) { throw "svc.cmd install exited $LASTEXITCODE" }
    & (Join-Path $RunnerRoot "svc.cmd") start
    if ($LASTEXITCODE -ne 0) { throw "svc.cmd start exited $LASTEXITCODE" }

    & (Join-Path $RunnerRoot "svc.cmd") status
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "runner registered. Prerequisites the acceptance workflow expects:"
Write-Host "  - Python 3.11+ or uv on PATH (install.ps1 needs one of them)"
Write-Host "  - the runner account can write HKCU\Environment (acceptance restores it)"
Write-Host "  - this machine is NOT used to review untrusted pull requests: the"
Write-Host "    workflow only runs on workflow_dispatch and on tag releases"
