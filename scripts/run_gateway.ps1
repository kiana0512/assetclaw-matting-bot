# Start the AssetClaw Gateway (FastAPI)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\run_gateway.ps1

$ErrorActionPreference = "Continue"
$ProjectRoot = "E:\assetclaw-matting-bot"
Set-Location $ProjectRoot

Write-Host "Activating conda env: assetclaw" -ForegroundColor Cyan
conda activate assetclaw

Write-Host "Starting Gateway on http://127.0.0.1:7865 ..." -ForegroundColor Green
python -m assetclaw_matting.cli.main gateway

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "CLI entry point failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
    Write-Host "Falling back to uvicorn directly..." -ForegroundColor Yellow
    uvicorn assetclaw_matting.api.main:app --host 127.0.0.1 --port 7865
}
