$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$AkiRoot = if ($env:COMFYUI_AKI_ROOT) { $env:COMFYUI_AKI_ROOT } else { "C:\Users\lilithgames\Downloads\ComfyUI-aki-v3" }
$ComfyDir = if ($env:COMFYUI_DIR) { $env:COMFYUI_DIR } else { Join-Path $AkiRoot "ComfyUI" }
$PythonExe = if ($env:COMFYUI_PYTHON_EXE) { $env:COMFYUI_PYTHON_EXE } else { Join-Path $AkiRoot "python\python.exe" }
$ListenHost = if ($env:COMFYUI_LISTEN_HOST) { $env:COMFYUI_LISTEN_HOST } else { "0.0.0.0" }
$Port = if ($env:COMFYUI_PORT) { $env:COMFYUI_PORT } else { "8188" }

if (-not (Test-Path $ComfyDir)) {
  throw "ComfyUI dir not found: $ComfyDir"
}
if (-not (Test-Path $PythonExe)) {
  throw "Aki ComfyUI python.exe not found: $PythonExe"
}

Write-Host "Starting ComfyUI with Aki environment only."
Write-Host "ComfyUI: $ComfyDir"
Write-Host "Python:  $PythonExe"
Write-Host "Listen:  $ListenHost`:$Port"
Write-Host "Agent env assetclaw is not activated here."

Set-Location $ComfyDir
& $PythonExe main.py --listen $ListenHost --port $Port
