$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Get-DotEnvValue {
  param([Parameter(Mandatory=$true)] [string]$Name)
  $line = Get-Content (Join-Path $ProjectRoot ".env") -ErrorAction SilentlyContinue |
    Where-Object { $_ -match "^\s*$Name\s*=" } |
    Select-Object -First 1
  if ([string]::IsNullOrWhiteSpace($line)) { return "" }
  return ($line -replace "^\s*$Name\s*=\s*", "").Trim().Trim('"').Trim("'")
}

function Get-ConfiguredValue {
  param([Parameter(Mandatory=$true)] [string]$Name)
  $value = [Environment]::GetEnvironmentVariable($Name)
  if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
  return Get-DotEnvValue $Name
}

$AkiRoot = Get-ConfiguredValue "COMFYUI_AKI_ROOT"
if ([string]::IsNullOrWhiteSpace($AkiRoot)) {
  $projectParent = Split-Path $ProjectRoot -Parent
  $candidates = @(
    (Join-Path $projectParent "ComfyUI-aki-v3"),
    (Join-Path $env:USERPROFILE "Desktop\ComfyUI-aki-v3"),
    (Join-Path $env:USERPROFILE "OneDrive\Desktop\ComfyUI-aki-v3")
  )
  $AkiRoot = $candidates | Where-Object {
    (Test-Path -LiteralPath (Join-Path $_ "ComfyUI")) -and
    (Test-Path -LiteralPath (Join-Path $_ "python\python.exe"))
  } | Select-Object -First 1
  if ([string]::IsNullOrWhiteSpace($AkiRoot)) { $AkiRoot = $candidates[0] }
}

$ComfyDir = Get-ConfiguredValue "COMFYUI_DIR"
if ([string]::IsNullOrWhiteSpace($ComfyDir)) { $ComfyDir = Join-Path $AkiRoot "ComfyUI" }
$PythonDir = Get-ConfiguredValue "COMFYUI_PYTHON_DIR"
if ([string]::IsNullOrWhiteSpace($PythonDir)) { $PythonDir = Join-Path $AkiRoot "python" }
$PythonExe = Get-ConfiguredValue "COMFYUI_PYTHON_EXE"
if ([string]::IsNullOrWhiteSpace($PythonExe)) { $PythonExe = Join-Path $PythonDir "python.exe" }
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
