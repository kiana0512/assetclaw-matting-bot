$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
conda create -n assetclaw python=3.11 -y
conda activate assetclaw
pip install -r requirements.txt
pip install -e .
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
python -m assetclaw_matting.cli.main init-db
Write-Host "assetclaw environment is ready."
