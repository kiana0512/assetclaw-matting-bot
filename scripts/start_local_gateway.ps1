$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

Write-Host "Gateway local debug only: http://127.0.0.1:7865"
Write-Host "No public exposure."
Write-Host "Cloudflare disabled."
Write-Host ""

$env:PYTHONPATH = "E:\assetclaw-matting-bot\src;E:\assetclaw-matting-bot"
$CondaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
if (-not (Test-Path $CondaExe)) {
  $cmd = Get-Command conda -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "conda not found. Install Miniconda or add conda to PATH." }
  $CondaExe = $cmd.Source
}
function Invoke-AssetPython {
  & $CondaExe run -n assetclaw python @args
}
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

# Init DB (idempotent)
Invoke-AssetPython -m assetclaw_matting.cli.main init-db 2>&1 | Write-Host

Write-Host "Starting Gateway on 127.0.0.1:7865 ..."
$Workers = if ($env:ASSETCLAW_GATEWAY_WORKERS) { [int]$env:ASSETCLAW_GATEWAY_WORKERS } else { 1 }
$AssetPython = Get-AssetPythonExe
& $AssetPython -m uvicorn assetclaw_matting.api.main:app --host 127.0.0.1 --port 7865 --log-level info --workers $Workers
