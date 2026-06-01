$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

Write-Host "==== test_feishu_ws_config.ps1 ===="
Write-Host ""

function Get-DotEnvValue {
  param([Parameter(Mandatory=$true)] [string]$Name, [string]$Default = "")
  $line = Get-Content ".env" -ErrorAction SilentlyContinue |
    Where-Object { $_ -match "^\s*$Name\s*=" } |
    Select-Object -First 1
  if ([string]::IsNullOrWhiteSpace($line)) { return $Default }
  $value = ($line -replace "^\s*$Name\s*=\s*", "").Trim().Trim('"').Trim("'")
  return $value
}

# Check .env values
$appId = Get-DotEnvValue "FEISHU_APP_ID"
$appSecret = Get-DotEnvValue "FEISHU_APP_SECRET"
$eventMode = Get-DotEnvValue "FEISHU_EVENT_MODE" "ws"

Write-Host "FEISHU_EVENT_MODE: $eventMode"
Write-Host "FEISHU_APP_ID: $(if ($appId) { $appId.Substring(0, [Math]::Min(8,$appId.Length)) + '***' } else { '(empty)' })"
Write-Host "FEISHU_APP_SECRET: $(if ($appSecret) { '[set]' } else { '(empty)' })"
Write-Host ""

$errors = @()
if ([string]::IsNullOrWhiteSpace($appId)) { $errors += "FEISHU_APP_ID is empty" }
if ([string]::IsNullOrWhiteSpace($appSecret)) { $errors += "FEISHU_APP_SECRET is empty" }
if ($eventMode -ne "ws") { Write-Host "WARNING: FEISHU_EVENT_MODE=$eventMode (expected 'ws' for long connection)" }

# Check lark_oapi installed
$condaPath = "C:\Users\$env:USERNAME\miniconda3\Scripts\conda.exe"
Write-Host "Checking lark_oapi installation..."
try {
  $result = & $condaPath run -n assetclaw python -c "import inspect; import lark_oapi; from lark_oapi.ws import Client; assert 'app_id' in str(inspect.signature(Client)); print('lark_oapi OK')" 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host $result
  } else {
    $errors += "lark_oapi not installed. Run: pip install lark-oapi"
    Write-Host "lark_oapi: NOT installed"
  }
} catch {
  Write-Host "Could not check lark_oapi (conda not found or env issue)"
}

# Check config module parses correctly
Write-Host ""
Write-Host "Checking config module..."
try {
  $env:PYTHONPATH = "E:\assetclaw-matting-bot\src"
  $cfgCode = "import sys; sys.path.insert(0,'src'); from assetclaw_matting.config import settings; print('feishu_event_mode:', settings.feishu_event_mode); print('feishu_enable_websocket:', settings.feishu_enable_websocket); print('app_id_set:', bool(settings.feishu_app_id))"
  $cfgResult = & $condaPath run -n assetclaw python -c $cfgCode 2>&1
  if ($LASTEXITCODE -eq 0) {
    Write-Host $cfgResult
    Write-Host "Config: OK"
  } else {
    Write-Host $cfgResult
    $errors += "Config module failed to load"
  }
} catch {
  Write-Host "Config check failed: $_"
}

Write-Host ""
if ($errors.Count -gt 0) {
  Write-Host "Issues found:"
  $errors | ForEach-Object { Write-Host "  - $_" }
  Write-Host ""
  Write-Host "[FAIL] Fix the above issues before starting the WS receiver."
} else {
  Write-Host "[PASS] Feishu WS config looks good."
}

Write-Host ""
Write-Host "==== test_feishu_ws_config.ps1 DONE ===="
