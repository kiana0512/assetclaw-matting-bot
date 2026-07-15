$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location (Join-Path $ProjectRoot "external_webui")

$env:ASSETCLAW_AGENT_URL = if ($env:ASSETCLAW_AGENT_URL) { $env:ASSETCLAW_AGENT_URL } else { "http://127.0.0.1:7865" }

if (-not (Test-Path "node_modules\vite") -or -not (Test-Path "node_modules\vue")) {
  Write-Host "WebUI dependencies missing. Installing with npm..."
  if (Test-Path "package-lock.json") {
    npm ci
  } else {
    npm install
  }
}

Write-Host "Starting AssetClaw External WebUI on http://127.0.0.1:5180 ..."
# Host, port, and strict-port behavior are already defined in package.json.
# Repeating them through npm.ps1 on Windows can strip the option names and
# pass only their values to Vite, which then treats 127.0.0.1 as the app root.
npm run dev
