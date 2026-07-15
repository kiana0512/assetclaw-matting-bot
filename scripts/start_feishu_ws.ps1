$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

Write-Host "Starting Feishu WebSocket receiver..."
Write-Host "Cloudflare/tunnel: disabled"
Write-Host "Event mode: ws"
Write-Host "Public exposure: none"
Write-Host ""

$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
$CondaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
if (-not (Test-Path $CondaExe)) {
  $cmd = Get-Command conda -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "conda not found. Install Miniconda or add conda to PATH." }
  $CondaExe = $cmd.Source
}

& $CondaExe run -n assetclaw python -m assetclaw_matting.feishu.ws_receiver
