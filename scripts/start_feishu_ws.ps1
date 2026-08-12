param(
  [switch]$SkipStartupRecovery
)

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
if ($SkipStartupRecovery) {
  $env:ASSETCLAW_SKIP_STARTUP_RECOVERY = "1"
  Write-Host "Startup task recovery: skipped (receiver-only reload)"
}
$PythonCandidates = @(
  (Join-Path $env:USERPROFILE "miniconda3\envs\assetclaw\python.exe"),
  (Join-Path $env:USERPROFILE "anaconda3\envs\assetclaw\python.exe"),
  (Join-Path $env:USERPROFILE "mambaforge\envs\assetclaw\python.exe")
)
$AssetPython = $PythonCandidates | Where-Object {
  Test-Path -LiteralPath $_ -PathType Leaf
} | Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($AssetPython)) {
  throw "assetclaw python.exe not found. Please check the assetclaw environment."
}

Write-Host "Using assetclaw Python: $AssetPython"
& $AssetPython -m assetclaw_matting.feishu.ws_receiver
