$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host 'Stopping Envoy services...'
try {
    Write-Host 'Running docker compose down...'
    docker compose down | Out-Host
} catch {
    Write-Host 'Docker compose down skipped or failed.'
}

Write-Host 'Stopping backend/frontend processes...'
Get-Process uvicorn, python, pythonw, node, npm, next -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host 'Done.'
