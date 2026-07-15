$ErrorActionPreference = "Continue"
Write-Host "=== AssetClaw Win3090 Animation Butler setup check ==="
foreach ($tool in @("git","conda")) {
  try { & $tool --version | Out-Null; Write-Host "[OK] $tool" }
  catch { Write-Host "[MISSING] $tool" }
}
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "data"),(Join-Path $ProjectRoot "logs"),(Join-Path $ProjectRoot "storage\batch_inputs"),(Join-Path $ProjectRoot "storage\batch_outputs"),(Join-Path $ProjectRoot "workflows") | Out-Null
Write-Host "Next: powershell -ExecutionPolicy Bypass -File scripts\setup_unified_env.ps1"
