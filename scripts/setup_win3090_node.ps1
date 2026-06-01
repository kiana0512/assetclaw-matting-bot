$ErrorActionPreference = "Continue"
Write-Host "=== AssetClaw Win3090 Animation Butler setup check ==="
foreach ($tool in @("git","conda")) {
  try { & $tool --version | Out-Null; Write-Host "[OK] $tool" }
  catch { Write-Host "[MISSING] $tool" }
}
New-Item -ItemType Directory -Force -Path "E:\assetclaw-matting-bot\data","E:\assetclaw-matting-bot\logs","E:\assetclaw-matting-bot\storage\batch_inputs","E:\assetclaw-matting-bot\storage\batch_outputs","E:\assetclaw-matting-bot\workflows" | Out-Null
Write-Host "Next: powershell -ExecutionPolicy Bypass -File scripts\setup_unified_env.ps1"
