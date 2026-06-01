$ErrorActionPreference = "Stop"
Set-Location "E:\assetclaw-matting-bot\ComfyUI"
conda activate assetclaw
python main.py --listen 127.0.0.1 --port 8188
