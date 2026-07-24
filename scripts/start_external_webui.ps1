$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location (Join-Path $ProjectRoot "external_webui")

$env:ASSETCLAW_AGENT_URL = if ($env:ASSETCLAW_AGENT_URL) { $env:ASSETCLAW_AGENT_URL } else { "http://127.0.0.1:7865" }
$env:ASSETCLAW_WEBUI_HOST = if ($env:ASSETCLAW_WEBUI_HOST) { $env:ASSETCLAW_WEBUI_HOST } else { "127.0.0.1" }
$env:ASSETCLAW_WEBUI_PORT = if ($env:ASSETCLAW_WEBUI_PORT) { $env:ASSETCLAW_WEBUI_PORT } else { "5180" }

$npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npm) {
  throw "npm.cmd not found. Install Node.js or add it to PATH."
}

if (-not (Test-Path "node_modules\vite") -or -not (Test-Path "node_modules\vue")) {
  Write-Host "WebUI dependencies missing. Installing with npm..."
  if (Test-Path "package-lock.json") {
    & $npm ci
  } else {
    & $npm install
  }
}

Write-Host "Starting AssetClaw External WebUI on $($env:ASSETCLAW_WEBUI_HOST):$($env:ASSETCLAW_WEBUI_PORT) ..."
& $npm run dev
