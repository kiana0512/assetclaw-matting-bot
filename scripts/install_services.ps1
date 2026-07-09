$ErrorActionPreference = "Stop"
$ProjectRoot = "E:\assetclaw-matting-bot"
$CondaBase = (conda info --base).Trim()
$Python = "$CondaBase\envs\assetclaw\python.exe"
if (-not (Test-Path $Python)) { throw "Run scripts\setup_unified_env.ps1 first." }
nssm install AssetClawGateway $Python "-m" "assetclaw_matting.cli.main" "gateway"
nssm set AssetClawGateway AppDirectory $ProjectRoot
nssm set AssetClawGateway AppEnvironmentExtra "PYTHONPATH=$ProjectRoot\src;$ProjectRoot"
nssm install AssetClawWorker $Python "-m" "assetclaw_matting.cli.main" "worker"
nssm set AssetClawWorker AppDirectory $ProjectRoot
nssm set AssetClawWorker AppEnvironmentExtra "PYTHONPATH=$ProjectRoot\src;$ProjectRoot"
Write-Host "Services installed: AssetClawGateway, AssetClawWorker"
