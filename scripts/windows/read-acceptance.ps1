param(
    [string]$EnvoyHome = (Join-Path $env:RUNNER_TEMP "envoy-acceptance"),
    [string]$Source = "https://raw.githubusercontent.com/regrevia/Envoy/main/README.md",
    [string]$PdfSource = "https://raw.githubusercontent.com/regrevia/Envoy/main/tests/fixtures/pdf/long-120-pages.pdf",
    [string]$OutFile = ""
)
$ErrorActionPreference = "Stop"

if (-not $OutFile) { $OutFile = Join-Path $env:RUNNER_TEMP "envoy-windows-read-acceptance.json" }

$shim = Join-Path $EnvoyHome "bin\envoy.cmd"
$python = Join-Path $EnvoyHome "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $shim)) { throw "envoy shim missing at $shim; install first (acceptance.ps1)" }
if (-not (Test-Path -LiteralPath $python)) { throw "runtime python missing at $python; install first (acceptance.ps1)" }

# Materials and cookies stay inside the acceptance home, never the runner's own profile.
$env:ENVOY_HOME = $EnvoyHome
Write-Host ("---- read acceptance against {0}" -f $shim)
& $python (Join-Path $PSScriptRoot "read_acceptance.py") --envoy $shim --source $Source --pdf-source $PdfSource --out $OutFile
$code = $LASTEXITCODE
Write-Host ("---- read acceptance exit {0}" -f $code)
if (Test-Path -LiteralPath $OutFile) { Get-Content -LiteralPath $OutFile -Raw | Write-Host }
if ($code -ne 0) { throw "read acceptance failed with exit $code" }
