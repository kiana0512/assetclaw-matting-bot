param(
    [int]$GatewayPort = 7865
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$powerShellExe = (Get-Process -Id $PID).Path
$logRoot = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

# Detached direct-video workers are deliberately outside this rolling reload.
$detachedWorkers = @{}
Get-ChildItem -LiteralPath (Join-Path $repoRoot "storage\direct_video_runs") -Directory -Filter "VID_*" -ErrorAction SilentlyContinue | ForEach-Object {
    $statusPath = Join-Path $_.FullName "status.json"
    if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
        try {
            $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([string]$status.status -eq "RUNNING" -and [int]$status.worker_pid -gt 0) {
                $detachedWorkers[[string]$status.id] = [int]$status.worker_pid
            }
        } catch {}
    }
}

$gatewayOwners = @(Get-NetTCPConnection -LocalPort $GatewayPort -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($processId in $gatewayOwners) {
    Stop-Process -Id $processId -Force -ErrorAction Stop
}

$receiverProcesses = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -like "*assetclaw_matting.feishu.ws_receiver*" })
foreach ($receiver in $receiverProcesses) {
    Stop-Process -Id $receiver.ProcessId -Force -ErrorAction Stop
}

Start-Process -FilePath $powerShellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_local_gateway.ps1")) `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "gateway_console.out.log") `
    -RedirectStandardError (Join-Path $logRoot "gateway_console.err.log")

$gatewayReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod "http://127.0.0.1:$GatewayPort/health" -TimeoutSec 2 | Out-Null
        $gatewayReady = $true
        break
    } catch {}
}
if (-not $gatewayReady) {
    throw "Gateway did not become healthy after rolling reload"
}

Start-Process -FilePath $powerShellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_feishu_ws.ps1")) `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "feishu_ws_console.out.log") `
    -RedirectStandardError (Join-Path $logRoot "feishu_ws_console.err.log")

Start-Sleep -Seconds 3
$receiverReady = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop |
    Where-Object { $_.CommandLine -like "*assetclaw_matting.feishu.ws_receiver*" }).Count -gt 0
if (-not $receiverReady) {
    throw "Feishu WebSocket receiver did not restart"
}

$workerChecks = foreach ($entry in $detachedWorkers.GetEnumerator()) {
    [pscustomobject]@{
        run_id = $entry.Key
        pid = $entry.Value
        alive = [bool](Get-Process -Id $entry.Value -ErrorAction SilentlyContinue)
    }
}
if (@($workerChecks | Where-Object { -not $_.alive }).Count -gt 0) {
    throw "One or more detached direct-video workers exited during routing reload"
}

[pscustomobject]@{
    gateway = "healthy"
    feishu_ws = "running"
    detached_workers_checked = @($workerChecks).Count
    detached_workers_alive = @($workerChecks | Where-Object { $_.alive }).Count
    matting_backend_mode = "hybrid"
}
