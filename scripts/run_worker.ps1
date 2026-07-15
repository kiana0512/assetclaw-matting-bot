$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
conda activate assetclaw
$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"
python -m assetclaw_matting.cli.main worker
