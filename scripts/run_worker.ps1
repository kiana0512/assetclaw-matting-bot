# Start the AssetClaw Worker (polls gateway, calls ComfyUI)
# conda env: assetclaw
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Activating conda env: assetclaw" -ForegroundColor Cyan
conda activate assetclaw
Set-Location $ProjectRoot

Write-Host "Starting worker (fake=$env:COMFYUI_FAKE_MODE)..." -ForegroundColor Green
python -m assetclaw_matting.cli.main worker
