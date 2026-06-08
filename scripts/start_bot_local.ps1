param(
  [switch]$NoMonitor
)
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

function Get-AssetPythonExe {
  $candidates = @(
    (Join-Path $env:USERPROFILE "miniconda3\envs\assetclaw\python.exe"),
    (Join-Path $env:USERPROFILE "anaconda3\envs\assetclaw\python.exe"),
    (Join-Path $env:USERPROFILE "mambaforge\envs\assetclaw\python.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { return $candidate }
  }

  $json = & $script:CondaExe env list --json 2>$null
  if ($LASTEXITCODE -eq 0 -and $json) {
    $envs = ($json | ConvertFrom-Json).envs
    foreach ($envPath in $envs) {
      if ((Split-Path $envPath -Leaf) -eq "assetclaw") {
        $pythonExe = Join-Path $envPath "python.exe"
        if (Test-Path $pythonExe) { return $pythonExe }
      }
    }
  }

  throw "assetclaw python.exe not found. Please check conda env assetclaw."
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

  $oldWebUi = Get-NetTCPConnection -LocalPort 5180 -ErrorAction SilentlyContinue
  if ($oldWebUi) {
    $oldWebUi | ForEach-Object {
      Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "old WebUI on port 5180: stopped"
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

function Wait-WebUI {
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
      Invoke-WebRequest "http://127.0.0.1:5180" -UseBasicParsing -TimeoutSec 2 | Out-Null
      return $true
    } catch {}
  }
  return $false
}

function Show-Status {
  Write-Host ""
  Write-Host "================ AssetClaw Local Bot ================"
  Write-Host "Gateway:        http://127.0.0.1:7865"
  Write-Host "WebUI:          http://127.0.0.1:5180"
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
    Invoke-WebRequest "http://127.0.0.1:5180" -UseBasicParsing -TimeoutSec 3 | Out-Null
    Write-Host "WebUI health:   OK"
  } catch {
    Write-Host "WebUI health:   FAILED"
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
  Write-Host "  webui:        logs\webui_console.out.log"
  Write-Host "====================================================="
  Write-Host ""
  if (-not $NoMonitor) {
    Write-Host "Live conversation trace. Press q or Ctrl+C to close this log monitor."
  }
  Write-Host "Services keep running in background. Stop with: pwsh -File scripts\stop_bot_local.ps1"
  Write-Host ""
}

function Watch-ConversationLog {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    New-Item -ItemType File -Path $Path -Force | Out-Null
  }
  & $script:AssetPythonExe -u "scripts\watch_conversation_log.py" $Path --tail 80
}

Write-Host ""
Write-Host "AssetClaw Bot - Local Safe Mode"
Write-Host "Starting Gateway + Feishu WS + WebUI in background..."
Write-Host ""

$script:CondaExe = Get-CondaExe
$script:AssetPythonExe = Get-AssetPythonExe
$env:PYTHONPATH = "E:\assetclaw-matting-bot\src;E:\assetclaw-matting-bot"

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

Write-Host "Starting WebUI hidden..."
Start-Process pwsh `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File scripts\start_external_webui.ps1" `
  -RedirectStandardOutput "logs\webui_console.out.log" `
  -RedirectStandardError "logs\webui_console.err.log" `
  -WindowStyle Hidden

if (-not (Wait-WebUI)) {
  Write-Host "WebUI did not become ready. Check logs\webui_console.err.log and logs\webui_console.out.log"
}

Start-Sleep -Seconds 5
Show-Status

if (-not $NoMonitor) {
  Watch-ConversationLog "logs\conversation.log"
}
