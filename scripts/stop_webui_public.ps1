$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$pidFile = Join-Path $ProjectRoot "logs\cloudflared.pid"
if (Test-Path -LiteralPath $pidFile) {
  $value = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($value -and ($value -as [int])) {
    Stop-Process -Id ([int]$value) -Force -ErrorAction SilentlyContinue
    Write-Host "cloudflared PID $value stopped."
  }
}

Remove-Item -LiteralPath `
  $pidFile, `
  (Join-Path $ProjectRoot "logs\public_webui_url.txt"), `
  (Join-Path $ProjectRoot "logs\public_webui_access.txt") `
  -Force -ErrorAction SilentlyContinue

Write-Host "Public tunnel stopped. Password-free local WebUI and backend were left running."
