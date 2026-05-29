# Win3090 Node prerequisites check and guided setup
$ErrorActionPreference = "Continue"

Write-Host "=== AssetClaw Win3090 Node Setup Check ===" -ForegroundColor Cyan

function Check-Tool($name, $cmd, $installCmd) {
    try {
        $null = Invoke-Expression $cmd 2>$null
        Write-Host "  [OK] $name" -ForegroundColor Green
    } catch {
        Write-Host "  [MISSING] $name" -ForegroundColor Red
        Write-Host "    Install: $installCmd" -ForegroundColor Yellow
    }
}

Write-Host "`n--- GPU ---"
Check-Tool "NVIDIA Driver / nvidia-smi" "nvidia-smi -q 2>$null | Select-String 'Driver Version'" `
    "Download from https://www.nvidia.com/drivers"

Write-Host "`n--- Core Tools ---"
Check-Tool "Git"       "git --version"     "winget install Git.Git"
Check-Tool "Git LFS"   "git lfs version"   "winget install GitHub.GitLFS"
Check-Tool "Miniconda" "conda --version"   "winget install Anaconda.Miniconda3"
Check-Tool "Node.js"   "node --version"    "winget install OpenJS.NodeJS.LTS"
Check-Tool "cloudflared" "cloudflared version" "winget install Cloudflare.cloudflared"
Check-Tool "NSSM"      "nssm version"      "winget install NSSM.NSSM"

Write-Host "`n--- VS Build Tools (for Python native extensions) ---"
Check-Tool "cl.exe (MSVC)" "cl.exe 2>&1 | Select-String 'Microsoft'" `
    "winget install Microsoft.VisualStudio.2022.BuildTools"

Write-Host "`n--- Create required directories ---"
$dirs = @(
    "E:\assetclaw-matting-bot\data",
    "E:\assetclaw-matting-bot\storage\batch_inputs",
    "E:\assetclaw-matting-bot\storage\batch_outputs",
    "E:\assetclaw-matting-bot\logs",
    "E:\assetclaw-matting-bot\workflows"
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Force $d | Out-Null
        Write-Host "  Created: $d" -ForegroundColor Green
    } else {
        Write-Host "  Exists:  $d" -ForegroundColor Gray
    }
}

Write-Host "`nNext steps:"
Write-Host "  1. Fix any [MISSING] tools above"
Write-Host "  2. scripts\setup_unified_env.ps1"
Write-Host "  3. scripts\setup_comfyui_in_unified_env.ps1  (if real ComfyUI needed)"
Write-Host "  4. Edit .env with your credentials"
Write-Host "  5. scripts\run_gateway.ps1 + scripts\run_worker.ps1"
