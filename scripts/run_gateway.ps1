# Run the AssetClaw Matting Bot gateway (FastAPI)
# Activate the conda environment first, then launch uvicorn.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Activating conda environment: assetclaw-matting" -ForegroundColor Cyan
conda activate assetclaw-matting

Set-Location $ProjectRoot

Write-Host "Starting gateway on port 7865..." -ForegroundColor Green
python -m assetclaw_matting.cli.main gateway
