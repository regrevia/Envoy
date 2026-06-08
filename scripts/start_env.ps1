$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path 'logs')) { New-Item -ItemType Directory -Path 'logs' | Out-Null }
$backendLog = Join-Path $root 'logs/backend-startup.log'
$frontendLog = Join-Path $root 'logs/frontend-startup.log'
$startupLog = Join-Path $root 'logs/startup.log'
$backendLogMaxLines = 250
$frontendLogMaxLines = 250
$startupLogMaxLines = 250
$backendPy = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'

function Test-Port {
    param([int]$Port)
    try {
        $c = New-Object Net.Sockets.TcpClient
        $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(500)
        if ($ok -and $c.Connected) {
            $c.EndConnect($iar)
            $c.Close()
            return $true
        }
        $c.Close()
        return $false
    } catch { return $false }
}

function Wait-Http {
    param([int]$Port, [string]$Path, [int]$Seconds, [string]$Name, [string]$LogFile)
    for ($i = 0; $i -lt $Seconds; $i += 2) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
                Write-Host "[$Name] ready on http://127.0.0.1:$Port$Path" -ForegroundColor Green
                return $true
            }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "[$Name] failed to start. Log tail:" -ForegroundColor Red
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 200 }
    Trim-LogFile -Path $LogFile -MaxLines 250
    return $false
}

function Open-Url {
    param([string]$Url)
    Start-Process $Url | Out-Null
}

function Trim-LogFile {
    param(
        [string]$Path,
        [int]$MaxLines = 250
    )

    if (-not (Test-Path $Path)) { return }
    $lines = Get-Content $Path -Tail $MaxLines -ErrorAction SilentlyContinue
    if ($null -eq $lines) { return }
    Set-Content -Path $Path -Value $lines
}

function Write-StartupLog {
    param([string]$Message)
    Add-Content -Path $startupLog -Value $Message
    Trim-LogFile -Path $startupLog -MaxLines $startupLogMaxLines
    Write-Host $Message
}

function Write-LogTail {
    param([string]$Name, [string]$LogFile, [int]$Lines = 100)
    Write-StartupLog "[$Name] recent log tail from ${LogFile}:"
    if (Test-Path $LogFile) {
        $tail = Get-Content $LogFile -Tail $Lines
        foreach ($line in $tail) {
            Add-Content -Path $startupLog -Value "[$Name] $line"
        }
        Trim-LogFile -Path $startupLog -MaxLines $startupLogMaxLines
        $tail
    } else {
        Write-StartupLog "[$Name] log file not found."
    }
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $Hint"
    }
}

function Find-FreePort {
    param([int[]]$Candidates, [string]$Name)
    foreach ($port in $Candidates) {
        if (-not (Test-Port $port)) {
            return $port
        }
        Write-Host "[$Name] port $port is already in use, trying next candidate..." -ForegroundColor Yellow
    }
    throw "[$Name] no available port found. Tried: $($Candidates -join ', ')"
}

function Start-ComposeService {
    param(
        [string]$ServiceName,
        [int[]]$Candidates,
        [string]$EnvVarName,
        [int]$DefaultPort
    )

    $port = Find-FreePort -Candidates $Candidates -Name $ServiceName
    if ($port -ne [int]$DefaultPort) {
        Write-Host "[$ServiceName] using fallback port $port instead of $DefaultPort" -ForegroundColor Yellow
    } else {
        Write-Host "[$ServiceName] using port $port" -ForegroundColor Green
    }

    Set-Item -Path "Env:$EnvVarName" -Value $port
    return $port
}

Assert-Command 'docker' 'Install Docker Desktop and make sure it is on PATH.'
Assert-Command 'cmd' 'cmd.exe should be available on Windows.'

try {
    docker info | Out-Null
} catch {
    throw "Docker does not appear to be running. Please start Docker Desktop and try again.`n$($_.Exception.Message)"
}

$mysqlPort = Start-ComposeService -ServiceName 'MySQL' -Candidates @(3307, 3308, 3309, 3310) -EnvVarName 'MYSQL_PORT' -DefaultPort 3307
$redisPort = Start-ComposeService -ServiceName 'Redis' -Candidates @(6379, 6380, 6381, 6382) -EnvVarName 'REDIS_PORT' -DefaultPort 6379
$minioPort = Start-ComposeService -ServiceName 'MinIO' -Candidates @(9000, 9002, 9003, 9004) -EnvVarName 'MINIO_PORT' -DefaultPort 9000
$minioConsolePort = Start-ComposeService -ServiceName 'MinIO Console' -Candidates @(9001, 9005, 9006, 9007) -EnvVarName 'MINIO_CONSOLE_PORT' -DefaultPort 9001

Write-StartupLog "Starting Docker services with ports: MySQL=$mysqlPort, Redis=$redisPort, MinIO=$minioPort, MinIO Console=$minioConsolePort"
docker compose up -d mysql redis minio | Out-Host
Write-StartupLog "Final ports in use: MySQL=$mysqlPort, Redis=$redisPort, MinIO=$minioPort, MinIO Console=$minioConsolePort"
Write-StartupLog '---'

$ports = @($mysqlPort, $redisPort, $minioPort)
$names = @('MySQL', 'Redis', 'MinIO')
for ($i = 0; $i -lt $ports.Count; $i++) {
    $port = $ports[$i]
    $name = $names[$i]
    $ready = $false
    for ($j = 0; $j -lt 30; $j++) {
        if (Test-Port $port) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        Write-StartupLog "[$name] not ready on port $port"
        exit 1
    }
    Write-StartupLog "[$name] ready on port $port"
}

$backendCandidates = @(8140, 8141, 8142, 8143, 8144, 8145, 8146, 8147, 8148, 8149)
$frontendCandidates = @(8200, 8201, 8202, 8203, 8204, 8205, 8206, 8207, 8208, 8209)

function Start-ServerWithRetry {
    param(
        [string]$Name,
        [int[]]$Candidates,
        [string]$Path,
        [int]$TimeoutSeconds,
        [string]$LogFile,
        [scriptblock]$StartAction
    )

    foreach ($candidate in $Candidates) {
        Write-StartupLog "[$Name] trying port $candidate"
        $proc = & $StartAction $candidate
        if ($null -eq $proc) {
            Write-StartupLog "[$Name] failed to launch process for port $candidate"
            continue
        }

        $elapsed = 0
        $ready = $false
        $failedToBind = $false
        while ($elapsed -lt $TimeoutSeconds) {
            Start-Sleep -Seconds 2
            $elapsed += 2

            $tail = ''
            if (Test-Path $LogFile) { $tail = (Get-Content $LogFile -Tail 40) -join "`n" }

            if ($tail -match 'bind on address|attempting to bind|access socket|Errno 13') {
                $failedToBind = $true
                break
            }
            if ($tail -match 'address already in use|Only one usage of each socket address|forbidden by its access permissions') {
                $failedToBind = $true
                break
            }

            if (Test-Port $candidate) {
                try {
                    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$candidate$Path" -UseBasicParsing -TimeoutSec 3
                    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
                        $ready = $true
                        break
                    }
                } catch {
                    # keep waiting
                }
            }
        }

        if ($ready) {
            Write-StartupLog "[$Name] using port $candidate"
            return @{ Process = $proc; Port = $candidate }
        }

        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        if ($failedToBind) {
            Write-StartupLog "[$Name] port $candidate failed to bind, trying next candidate"
            if (Test-Path $LogFile) {
                $tail = Get-Content $LogFile -Tail 40
                foreach ($line in $tail) {
                    Add-Content -Path $startupLog -Value "[$Name] $line"
                }
                Trim-LogFile -Path $LogFile -MaxLines 250
            }
            Trim-LogFile -Path $startupLog -MaxLines $startupLogMaxLines
            continue
        }

        Write-StartupLog "[$Name] failed on port $candidate"
        Write-LogTail -Name $Name -LogFile $LogFile -Lines 40 | Out-Null
    }

    Write-StartupLog "[$Name] no available port could be started. Tried: $($Candidates -join ', ')"
    Write-LogTail -Name $Name -LogFile $LogFile -Lines 100 | Out-Null
    Trim-LogFile -Path $startupLog -MaxLines $startupLogMaxLines
    throw "[$Name] no available port could be started. Tried: $($Candidates -join ', ')"
}

$backendResult = Start-ServerWithRetry -Name 'Backend' -Candidates $backendCandidates -Path '/api/v1/health' -TimeoutSeconds 60 -LogFile $backendLog -StartAction {
    param($candidate)
    $cmd = "cd /d `"$backendPy`" && set PYTHONUNBUFFERED=1 && set BACKEND_PORT=$candidate && set PORT=$candidate && py -m uvicorn app.main:app --host 0.0.0.0 --port $candidate >> `"$backendLog`" 2>&1"
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd) -PassThru -WindowStyle Hidden
}
$backend = $backendResult.Process
$backendPort = $backendResult.Port

Write-StartupLog 'Starting frontend...'
if (-not (Test-Path $frontendLog)) { New-Item -ItemType File -Path $frontendLog | Out-Null }
$frontendBuildDir = Join-Path $frontendDir '.next'
if (Test-Path $frontendBuildDir) {
    Write-StartupLog '[Frontend] clearing stale .next cache before startup...'
    Remove-Item -Recurse -Force $frontendBuildDir -ErrorAction SilentlyContinue
}
if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    Write-StartupLog '[Frontend] node_modules missing, running npm install first...'
    Push-Location $frontendDir
    try {
        & cmd.exe /c "npm install"
    } finally {
        Pop-Location
    }
}
Push-Location $frontendDir
try {
    Write-StartupLog '[Frontend] building production bundle...'
    & cmd.exe /c "npm run build"
} finally {
    Pop-Location
}
$frontendResult = Start-ServerWithRetry -Name 'Frontend' -Candidates $frontendCandidates -Path '/' -TimeoutSeconds 180 -LogFile $frontendLog -StartAction {
    param($candidate)
    $cmd = "cd /d `"$frontendDir`" && set PORT=$candidate && set NEXT_TELEMETRY_DISABLED=1 && call npm.cmd run start -- --hostname 127.0.0.1 --port $candidate >> `"$frontendLog`" 2>&1"
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd) -PassThru -WindowStyle Hidden
}
if (-not $frontendResult) {
    Write-StartupLog '[Frontend] failed to start.'
    exit 1
}
$frontend = $frontendResult.Process
$frontendPort = $frontendResult.Port
if (-not $frontendPort) {
    Write-StartupLog '[Frontend] no port selected.'
    exit 1
}

Write-StartupLog 'All services started successfully.'
$browserUrl = "http://127.0.0.1:$frontendPort"
Write-StartupLog "Opening browser at $browserUrl"
Start-Sleep -Seconds 2
Open-Url $browserUrl
