# One-time project setup: create conda env, install deps, init DB.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Creating conda environment: assetclaw-matting (Python 3.11)" -ForegroundColor Cyan
conda create -n assetclaw-matting python=3.11 -y

Write-Host "Activating environment..." -ForegroundColor Cyan
conda activate assetclaw-matting

Set-Location $ProjectRoot

Write-Host "Installing dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt

if (-not (Test-Path "$ProjectRoot\.env")) {
    Write-Host "Copying .env.example -> .env" -ForegroundColor Yellow
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host "IMPORTANT: Edit .env and fill in your real credentials." -ForegroundColor Red
} else {
    Write-Host ".env already exists, skipping." -ForegroundColor Green
}

Write-Host "Initialising database..." -ForegroundColor Cyan
python -m assetclaw_matting.cli.main init-db

Write-Host ""
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit .env (fill in tokens, confirm COMFYUI_FAKE_MODE=true for testing)"
Write-Host "  2. scripts\run_gateway.ps1"
Write-Host "  3. scripts\run_batch.ps1     (edit paths first)"
Write-Host "  4. scripts\run_worker.ps1"
