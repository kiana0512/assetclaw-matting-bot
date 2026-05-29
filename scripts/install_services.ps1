# Install AssetClaw Gateway and Worker as Windows services using NSSM
# Requires: NSSM installed (winget install NSSM.NSSM)
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$CondaBase = (conda info --base 2>$null).Trim()
$Python = "$CondaBase\envs\assetclaw\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Python not found at $Python" -ForegroundColor Red
    Write-Host "Run scripts\setup_unified_env.ps1 first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Installing AssetClaw Gateway service..." -ForegroundColor Cyan
nssm install AssetClawGateway $Python "-m" "assetclaw_matting.cli.main" "gateway"
nssm set AssetClawGateway AppDirectory $ProjectRoot
nssm set AssetClawGateway AppEnvironmentExtra "PYTHONPATH=$ProjectRoot\src"
nssm set AssetClawGateway Description "AssetClaw Win3090 Gateway"

Write-Host "Installing AssetClaw Worker service..." -ForegroundColor Cyan
nssm install AssetClawWorker $Python "-m" "assetclaw_matting.cli.main" "worker"
nssm set AssetClawWorker AppDirectory $ProjectRoot
nssm set AssetClawWorker AppEnvironmentExtra "PYTHONPATH=$ProjectRoot\src"
nssm set AssetClawWorker Description "AssetClaw Win3090 Worker"

Write-Host ""
Write-Host "Services installed. Start with:" -ForegroundColor Green
Write-Host "  nssm start AssetClawGateway"
Write-Host "  nssm start AssetClawWorker"
Write-Host ""
Write-Host "Or via Windows Services manager (services.msc)"
