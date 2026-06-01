$ErrorActionPreference = "Stop"
Set-Location "E:\assetclaw-matting-bot"
conda activate assetclaw
$env:PYTHONPATH = "E:\assetclaw-matting-bot\src"
python -m assetclaw_matting.cli.main worker
