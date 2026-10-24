$ErrorActionPreference = "SilentlyContinue"

$envoy = $env:ENVOY_BIN
if (-not $envoy -or -not (Test-Path $envoy)) {
    $command = Get-Command envoy -ErrorAction SilentlyContinue
    if ($command) {
        $envoy = $command.Source
    }
}
if (-not $envoy) {
    foreach ($candidate in @(
        (Join-Path $HOME ".local\bin\envoy.exe"),
        (Join-Path $HOME ".local\bin\envoy"),
        "C:\Program Files\Envoy\envoy.exe"
    )) {
        if (Test-Path $candidate) {
            $envoy = $candidate
            break
        }
    }
}
if (-not $envoy) {
    exit 0
}

& $envoy hook @args
exit 0
