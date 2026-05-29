# Setup ComfyUI inside the unified assetclaw conda environment
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$ComfyDir = "$ProjectRoot\ComfyUI"

conda activate assetclaw

# 1. Clone ComfyUI
if (-not (Test-Path $ComfyDir)) {
    Write-Host "Cloning ComfyUI..." -ForegroundColor Cyan
    git clone https://github.com/comfyanonymous/ComfyUI.git $ComfyDir
} else {
    Write-Host "ComfyUI already cloned at $ComfyDir" -ForegroundColor Green
}

Set-Location $ComfyDir

# 2. Install PyTorch with CUDA (adjust CUDA version if needed)
Write-Host "Installing PyTorch with CUDA 12.1..." -ForegroundColor Cyan
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 3. Install ComfyUI dependencies
Write-Host "Installing ComfyUI requirements..." -ForegroundColor Cyan
pip install -r requirements.txt

# 4. Install ComfyUI Manager
$ManagerDir = "$ComfyDir\custom_nodes\ComfyUI-Manager"
if (-not (Test-Path $ManagerDir)) {
    Write-Host "Installing ComfyUI Manager..." -ForegroundColor Cyan
    git clone https://github.com/ltdrdata/ComfyUI-Manager.git $ManagerDir
}

Write-Host ""
Write-Host "ComfyUI setup complete in unified assetclaw env!" -ForegroundColor Green
Write-Host "Next:"
Write-Host "  1. Launch ComfyUI: scripts\run_comfyui.ps1"
Write-Host "  2. Open http://127.0.0.1:8188 and install RMBG/BiRefNet via Manager"
Write-Host "  3. Export your matting workflow as API JSON -> workflows\matting_api.json"
Write-Host "  4. Set COMFYUI_FAKE_MODE=false in .env"
