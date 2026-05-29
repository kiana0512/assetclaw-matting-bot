# Setup unified conda environment: assetclaw (Python 3.11)
# Used for: Gateway, Worker, Skills API, Brain Router, MCP Server, ComfyUI
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

Write-Host "Creating unified conda env: assetclaw (Python 3.11)" -ForegroundColor Cyan
conda create -n assetclaw python=3.11 -y

conda activate assetclaw
Set-Location $ProjectRoot

Write-Host "Installing AssetClaw dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt

if (-not (Test-Path "$ProjectRoot\.env")) {
    Write-Host "Copying .env.example -> .env" -ForegroundColor Yellow
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host "IMPORTANT: Edit .env and fill in your credentials." -ForegroundColor Red
}

Write-Host "Initialising database..." -ForegroundColor Cyan
python -m assetclaw_matting.cli.main init-db

Write-Host ""
Write-Host "Unified environment setup complete!" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit .env (set SKILL_API_TOKEN, WORKER_TOKEN, brain provider keys)"
Write-Host "  2. scripts\run_gateway.ps1"
Write-Host "  3. scripts\run_worker.ps1"
Write-Host "  4. If real ComfyUI: scripts\setup_comfyui_in_unified_env.ps1"
