$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

function Get-CondaExe {
  $condaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
  if (Test-Path $condaExe) { return $condaExe }
  $cmd = Get-Command conda -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "conda not found. Install Miniconda or add conda to PATH."
}

function Invoke-AssetPython {
  & $script:CondaExe run -n assetclaw python @args
}

function Stop-OldProcesses {
  Get-Process cloudflared -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

  $oldGateway = Get-NetTCPConnection -LocalPort 7865 -ErrorAction SilentlyContinue
  if ($oldGateway) {
    $oldGateway | ForEach-Object {
      Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "old Gateway on port 7865: stopped"
  }

  Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*assetclaw_matting.feishu.ws_receiver*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Write-Host "old Feishu WS receiver: stopped (if any)"
}

function Wait-Gateway {
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
      Invoke-RestMethod "http://127.0.0.1:7865/health" -TimeoutSec 2 | Out-Null
      return $true
    } catch {}
  }
  return $false
}

function Show-Status {
  Write-Host ""
  Write-Host "================ AssetClaw Local Bot ================"
  Write-Host "Gateway:        http://127.0.0.1:7865"
  Write-Host "Feishu mode:    WebSocket long connection"
  Write-Host "Public expose:  none"
  Write-Host "Cloudflare:     disabled"
  Write-Host ""

  try {
    $health = Invoke-RestMethod "http://127.0.0.1:7865/health" -TimeoutSec 3
    Write-Host "Gateway health: OK"
    Write-Host "Brain provider: $($health.brain_provider)"
    Write-Host "ComfyUI fake:   $($health.comfyui_fake_mode)"
  } catch {
    Write-Host "Gateway health: FAILED"
  }

  try {
    $manifest = Invoke-RestMethod "http://127.0.0.1:7865/skills/v1/manifest" -TimeoutSec 3
    $implemented = @($manifest.skills | Where-Object { $_.implemented -eq $true }).Count
    Write-Host "Skills:         $implemented / $(@($manifest.skills).Count) implemented"
  } catch {
    Write-Host "Skills:         unavailable"
  }

  $wsProc = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*assetclaw_matting.feishu.ws_receiver*" } |
    Select-Object -First 1
  Write-Host "Feishu WS:      $(if ($wsProc) { "running PID=$($wsProc.ProcessId)" } else { "not running" })"
  Write-Host "Logs:"
  Write-Host "  conversation: logs\conversation.log"
  Write-Host "  gateway:      logs\gateway.log"
  Write-Host "  feishu ws:    logs\feishu_ws.log"
  Write-Host "====================================================="
  Write-Host ""
  Write-Host "Live conversation trace. Press Ctrl+C to close this log monitor."
  Write-Host "Services keep running in background. Stop with: pwsh -File scripts\stop_bot_local.ps1"
  Write-Host ""
}

Write-Host ""
Write-Host "AssetClaw Bot - Local Safe Mode"
Write-Host "Starting Gateway + Feishu WS in background..."
Write-Host ""

$script:CondaExe = Get-CondaExe
$env:PYTHONPATH = "E:\assetclaw-matting-bot\src"

Stop-OldProcesses

Write-Host "Checking Python dependencies in conda env: assetclaw..."
$depCheck = Invoke-AssetPython -c "import pydantic_settings, uvicorn, lark_oapi; from lark_oapi.ws import Client; print('deps OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Installing requirements.txt into conda env assetclaw..."
  Invoke-AssetPython -m pip install -r requirements.txt
}

Write-Host "Initializing database..."
Invoke-AssetPython -m assetclaw_matting.cli.main init-db 2>&1 | Write-Host

if (-not (Test-Path "logs\conversation.log")) {
  New-Item -ItemType File -Path "logs\conversation.log" -Force | Out-Null
}

Write-Host "Starting local Gateway hidden..."
Start-Process pwsh `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1" `
  -RedirectStandardOutput "logs\gateway_console.out.log" `
  -RedirectStandardError "logs\gateway_console.err.log" `
  -WindowStyle Hidden

if (-not (Wait-Gateway)) {
  Write-Host "Gateway did not become healthy. Check logs\gateway_console.err.log"
  exit 1
}

Write-Host "Starting Feishu WS receiver hidden..."
Start-Process pwsh `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File scripts\start_feishu_ws.ps1" `
  -RedirectStandardOutput "logs\feishu_ws_console.out.log" `
  -RedirectStandardError "logs\feishu_ws_console.err.log" `
  -WindowStyle Hidden

Start-Sleep -Seconds 5
Show-Status

Get-Content "logs\conversation.log" -Tail 80 -Wait
