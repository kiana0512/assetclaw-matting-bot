$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

Write-Host "Stopping AssetClaw Bot..."

# 1. Stop any cloudflared (safety)
$cf = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($cf) {
  $cf | Stop-Process -Force
  Write-Host "cloudflared: stopped"
} else {
  Write-Host "cloudflared: not running"
}

# 2. Stop Gateway on port 7865
$port7865 = Get-NetTCPConnection -LocalPort 7865 -ErrorAction SilentlyContinue
if ($port7865) {
  $processIds = $port7865 | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    Write-Host "Stopping Gateway process PID=$processId on port 7865"
    taskkill /PID $processId /F 2>&1 | Out-Null
  }
  Write-Host "Gateway: stopped"
} else {
  Write-Host "Gateway: not running on port 7865"
}

# 3. Stop WebUI on port 5180
$port5180 = Get-NetTCPConnection -LocalPort 5180 -ErrorAction SilentlyContinue
if ($port5180) {
  $processIds = $port5180 | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    Write-Host "Stopping WebUI process PID=$processId on port 5180"
    taskkill /PID $processId /F 2>&1 | Out-Null
  }
  Write-Host "WebUI: stopped"
} else {
  Write-Host "WebUI: not running on port 5180"
}

# 4. Stop Unity MCP local HTTP server
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\stop_unity_mcp.ps1 2>&1 | Write-Host

# 5. Stop ws_receiver python processes (those with ws_receiver in command line)
$pyProcs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'python3.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue
$killed = 0
foreach ($proc in $pyProcs) {
  $cmdline = $proc.CommandLine
  if ($cmdline -and (
    $cmdline -like "*ws_receiver*" -or
    $cmdline -like "*assetclaw_matting.api.main*" -or
    $cmdline -like "*assetclaw_matting.feishu.ws*"
  )) {
    Write-Host "Stopping Python process PID=$($proc.ProcessId): $($cmdline.Substring(0, [Math]::Min(80, $cmdline.Length)))"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    $killed++
  }
}
if ($killed -eq 0) {
  Write-Host "No AssetClaw Python processes found to stop."
}

Write-Host ""
Write-Host "AssetClaw Bot: stopped."
