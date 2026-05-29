# Start the AssetClaw Gateway (FastAPI)
# conda env: assetclaw (unified env for all components)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Activating conda env: assetclaw" -ForegroundColor Cyan
conda activate assetclaw
Set-Location $ProjectRoot

Write-Host "Starting gateway (brain=$env:BRAIN_PROVIDER, fake=$env:COMFYUI_FAKE_MODE)..." -ForegroundColor Green
python -m assetclaw_matting.cli.main gateway
