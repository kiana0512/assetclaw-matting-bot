param(
    [Parameter(Mandatory = $true)]
    [string[]]$RunId,
    [Parameter(Mandatory = $true)]
    [string]$CaBundle,
    [string]$PythonExe = "C:\Users\zhangqichao\miniconda3\envs\assetclaw\python.exe",
    [string]$BaseUrl = "https://10.3.34.11",
    [switch]$AllowCaWithoutKeyUsage,
    [switch]$WorkerMode
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runsRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "storage\direct_video_runs"))
$caPath = [System.IO.Path]::GetFullPath($CaBundle)

if ($RunId.Count -eq 1 -and $RunId[0].Contains(",")) {
    $RunId = @($RunId[0].Split(",", [System.StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { $_.Trim() })
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable not found: $PythonExe"
}
if (-not (Test-Path -LiteralPath $caPath -PathType Leaf)) {
    throw "GPU Control CA bundle not found: $caPath"
}

if ($WorkerMode) {
    if ($RunId.Count -ne 1) {
        throw "WorkerMode requires exactly one RunId"
    }
    $env:MATTING_BACKEND_MODE = "gpu_control"
    $env:GPU_CONTROL_BASE_URL = $BaseUrl
    $env:GPU_CONTROL_VERIFY_TLS = "true"
    $env:GPU_CONTROL_CA_BUNDLE = $caPath
    $env:GPU_CONTROL_ALLOW_CA_WITHOUT_KEY_USAGE = if ($AllowCaWithoutKeyUsage) { "true" } else { "false" }
    Set-Location -LiteralPath $repoRoot
    & $PythonExe "scripts\direct_video_worker.py" $RunId[0]
    exit $LASTEXITCODE
}

$plans = @()
foreach ($id in $RunId) {
    if ($id -notmatch '^VID_[A-Z0-9]+$') {
        throw "Invalid direct-video run id: $id"
    }
    $statusPath = [System.IO.Path]::GetFullPath((Join-Path $runsRoot "$id\status.json"))
    if (-not $statusPath.StartsWith($runsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Run path escaped the direct-video root: $statusPath"
    }
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        throw "Run status not found: $statusPath"
    }
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$status.status -ne "RUNNING" -or [string]$status.stage -ne "waiting_pipeline_queue") {
        throw "$id is not safely migratable: status=$($status.status), stage=$($status.stage)"
    }
    $oldPid = [int]$status.worker_pid
    if ($oldPid -le 0) {
        throw "$id has no waiting worker pid"
    }
    $process = Get-Process -Id $oldPid -ErrorAction Stop
    if ($process.ProcessName -notlike "python*") {
        throw "$id pid $oldPid is not a Python worker; refusing to stop $($process.ProcessName)"
    }
    $plans += [pscustomobject]@{ RunId = $id; OldPid = $oldPid }
}

foreach ($plan in $plans) {
    Stop-Process -Id $plan.OldPid -ErrorAction Stop
    Wait-Process -Id $plan.OldPid -ErrorAction SilentlyContinue

    $logRoot = Join-Path $repoRoot "logs"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $stdout = Join-Path $logRoot "gpu_control_migration_$($plan.RunId).out.log"
    $stderr = Join-Path $logRoot "gpu_control_migration_$($plan.RunId).err.log"
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-WorkerMode",
        "-RunId", $plan.RunId,
        "-CaBundle", $caPath,
        "-PythonExe", $PythonExe,
        "-BaseUrl", $BaseUrl
    )
    if ($AllowCaWithoutKeyUsage) {
        $arguments += "-AllowCaWithoutKeyUsage"
    }
    $replacement = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    [pscustomobject]@{
        run_id = $plan.RunId
        previous_pid = $plan.OldPid
        replacement_launcher_pid = $replacement.Id
        backend = "gpu_control"
        status = "MIGRATED"
    }
}
