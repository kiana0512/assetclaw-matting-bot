$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$env:PYTHONPATH = "$ProjectRoot\src;$ProjectRoot"

$CondaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
if (-not (Test-Path -LiteralPath $CondaExe -PathType Leaf)) {
    $command = Get-Command conda -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "conda not found. Install Miniconda or add conda to PATH."
    }
    $CondaExe = $command.Source
}

& $CondaExe run -n assetclaw python -m assetclaw_matting.services.character_resolution_monitor
