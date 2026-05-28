# Run the AssetClaw worker loop
# The worker polls the gateway for tasks and calls ComfyUI locally.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Activating conda environment: assetclaw-matting" -ForegroundColor Cyan
conda activate assetclaw-matting

Set-Location $ProjectRoot

Write-Host "Starting worker loop (WORKER_ID=$env:WORKER_ID)..." -ForegroundColor Green
python -m assetclaw_matting.cli.main worker
