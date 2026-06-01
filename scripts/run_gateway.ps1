$ErrorActionPreference = "Stop"
Set-Location "E:\assetclaw-matting-bot"
$env:PYTHONPATH = "E:\assetclaw-matting-bot\src"
$CondaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
if (-not (Test-Path $CondaExe)) {
  $cmd = Get-Command conda -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "conda not found. Install Miniconda or add conda to PATH." }
  $CondaExe = $cmd.Source
}
try {
  & $CondaExe run -n assetclaw python -m assetclaw_matting.cli.main gateway
} catch {
  Write-Host "Gateway command failed. Fallback:"
  Write-Host "conda run -n assetclaw python -m uvicorn assetclaw_matting.api.main:app --host 127.0.0.1 --port 7865"
  throw
}
