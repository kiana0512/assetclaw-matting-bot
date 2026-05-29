# Start ComfyUI in the unified assetclaw conda environment
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$ComfyDir = "$ProjectRoot\ComfyUI"

if (-not (Test-Path $ComfyDir)) {
    Write-Host "ComfyUI not found at $ComfyDir" -ForegroundColor Red
    Write-Host "Run scripts\setup_comfyui_in_unified_env.ps1 first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Activating conda env: assetclaw" -ForegroundColor Cyan
conda activate assetclaw
Set-Location $ComfyDir

Write-Host "Starting ComfyUI on http://127.0.0.1:8188 ..." -ForegroundColor Green
python main.py --listen 127.0.0.1 --port 8188
