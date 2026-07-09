$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$TunnelPidFile = Join-Path $ProjectRoot "logs\cloudflared.pid"
$PublicUrlFile = Join-Path $ProjectRoot "logs\public_webui_url.txt"

Write-Host "Stopping AssetClaw Public WebUI..."

$stopped = $false
if (Test-Path $TunnelPidFile) {
  $pidText = Get-Content $TunnelPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pidText -and ($pidText -as [int])) {
    Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
    Write-Host "cloudflared PID ${pidText}: stopped"
    $stopped = $true
  }
}

if (-not $stopped) {
  Write-Host "No tunnel PID file found. This script will not stop other local services or unrelated cloudflared processes."
}

Remove-Item -LiteralPath $TunnelPidFile, $PublicUrlFile -Force -ErrorAction SilentlyContinue

Write-Host "Local backend/WebUI services were left running."

Write-Host ""
Write-Host "AssetClaw Public WebUI: stopped."
