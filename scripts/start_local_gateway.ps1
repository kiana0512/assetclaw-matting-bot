$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

Write-Host "Gateway local debug only: http://127.0.0.1:7865"
Write-Host "No public exposure."
Write-Host "Cloudflare disabled."
Write-Host ""

$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
function Get-AssetPythonExe {
  $candidates = @(
    (Join-Path $env:USERPROFILE "miniconda3\envs\assetclaw\python.exe"),
    (Join-Path $env:USERPROFILE "anaconda3\envs\assetclaw\python.exe"),
    (Join-Path $env:USERPROFILE "mambaforge\envs\assetclaw\python.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { return $candidate }
  }
  throw "assetclaw python.exe not found."
}

$AssetPython = Get-AssetPythonExe
function Invoke-AssetPython {
  & $script:AssetPython @args
}

# Init DB (idempotent)
Write-Host "Using assetclaw Python: $AssetPython"
Invoke-AssetPython -m assetclaw_matting.cli.main init-db 2>&1 | Write-Host

Write-Host "Starting Gateway on 127.0.0.1:7865 ..."
$Workers = if ($env:ASSETCLAW_GATEWAY_WORKERS) { [int]$env:ASSETCLAW_GATEWAY_WORKERS } else { 1 }
& $AssetPython -m uvicorn assetclaw_matting.api.main:app --host 127.0.0.1 --port 7865 --log-level info --workers $Workers
