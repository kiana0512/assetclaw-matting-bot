param(
  [string]$LocalUrl = "http://127.0.0.1:5180",
  [string]$Username = "assetclaw",
  [string]$Password = ""
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$PowerShellExe = (Get-Process -Id $PID).Path
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

if (-not $Password) {
  $Password = [Guid]::NewGuid().ToString("N").Substring(0, 16)
}
$basicToken = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${Username}:${Password}"))
$authHeaders = @{ Authorization = "Basic $basicToken" }

$TunnelOutLog = Join-Path $ProjectRoot "logs\cloudflared.out.log"
$TunnelErrLog = Join-Path $ProjectRoot "logs\cloudflared.err.log"
$TunnelPidFile = Join-Path $ProjectRoot "logs\cloudflared.pid"
$PublicUrlFile = Join-Path $ProjectRoot "logs\public_webui_url.txt"
$AccessFile = Join-Path $ProjectRoot "logs\public_webui_access.txt"

function Get-CloudflaredExe {
  $candidates = @(
    $env:CLOUDFLARED_EXE,
    (Join-Path $ProjectRoot "tools\cloudflared.exe"),
    (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe")
  ) | Where-Object { $_ }
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path $candidate).Path }
  }
  $command = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  throw "cloudflared.exe not found. Install it with: winget install --id Cloudflare.cloudflared"
}

function Stop-PidFromFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $value = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($value -and ($value -as [int])) {
    Stop-Process -Id ([int]$value) -Force -ErrorAction SilentlyContinue
  }
}

function Test-WebUi {
  try {
    Invoke-WebRequest $LocalUrl -Headers $authHeaders -UseBasicParsing -TimeoutSec 3 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Wait-WebUi {
  for ($i = 0; $i -lt 30; $i++) {
    if (Test-WebUi) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Get-ListenerProcessIds([int]$Port) {
  $ids = @(
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
  )
  if ($ids.Count -eq 0) {
    $ids = @(
      netstat -ano |
        Select-String "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$" |
        ForEach-Object { if ($_.Matches.Count) { [int]$_.Matches[0].Groups[1].Value } } |
        Sort-Object -Unique
    )
  }
  return $ids
}

function Test-WebUiAuthGuard {
  try {
    Invoke-WebRequest $LocalUrl -UseBasicParsing -TimeoutSec 3 | Out-Null
    return $false
  } catch {
    return ($_.Exception.Response.StatusCode.value__ -eq 401)
  }
}

function Find-PublicUrl {
  foreach ($path in @($TunnelOutLog, $TunnelErrLog)) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $match = [regex]::Matches((Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue), "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($match.Count -gt 0) { return $match[$match.Count - 1].Value }
  }
  return $null
}

Write-Host "Restarting the WebUI with password protection..."
$port = ([Uri]$LocalUrl).Port
Get-ListenerProcessIds $port |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

$env:ASSETCLAW_WEBUI_HOST = "127.0.0.1"
$env:ASSETCLAW_WEBUI_PORT = [string]$port
$env:ASSETCLAW_WEBUI_SHARE_USERNAME = $Username
$env:ASSETCLAW_WEBUI_SHARE_PASSWORD = $Password
Start-Process -FilePath $PowerShellExe `
  -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File scripts\start_external_webui.ps1" `
  -RedirectStandardOutput "logs\webui_console.out.log" `
  -RedirectStandardError "logs\webui_console.err.log" `
  -WindowStyle Hidden

if (-not (Wait-WebUi)) {
  throw "Password-protected WebUI did not become ready at $LocalUrl. Check logs\webui_console.err.log."
}
if (-not (Test-WebUiAuthGuard)) {
  throw "WebUI authentication guard is not active. Refusing to expose an unprotected public URL."
}

$cloudflared = Get-CloudflaredExe
Stop-PidFromFile $TunnelPidFile
Remove-Item -LiteralPath $TunnelOutLog,$TunnelErrLog,$PublicUrlFile,$AccessFile -Force -ErrorAction SilentlyContinue

Write-Host "Starting Cloudflare quick tunnel..."
$tunnel = Start-Process -FilePath $cloudflared `
  -ArgumentList @("tunnel", "--url", $LocalUrl, "--no-autoupdate") `
  -RedirectStandardOutput $TunnelOutLog `
  -RedirectStandardError $TunnelErrLog `
  -WindowStyle Hidden `
  -PassThru
Set-Content -LiteralPath $TunnelPidFile -Value $tunnel.Id -Encoding ASCII

$publicUrl = $null
for ($i = 0; $i -lt 45; $i++) {
  $publicUrl = Find-PublicUrl
  if ($publicUrl) { break }
  Start-Sleep -Seconds 1
}
if (-not $publicUrl) {
  throw "Tunnel started but no public URL was returned. Check logs\cloudflared.err.log."
}

@(
  "URL=$publicUrl",
  "USERNAME=$Username",
  "PASSWORD=$Password"
) | Set-Content -LiteralPath $AccessFile -Encoding UTF8
Set-Content -LiteralPath $PublicUrlFile -Value $publicUrl -Encoding ASCII
try { Set-Clipboard -Value $publicUrl } catch {}

Write-Host ""
Write-Host "================ PUBLIC WEBUI READY ================" -ForegroundColor Green
Write-Host "访问地址:  $publicUrl"
Write-Host "用户名:    $Username"
Write-Host "访问密码:  $Password"
Write-Host "凭据文件:  $AccessFile"
Write-Host "停止隧道:  双击 stop_public_webui.bat"
Write-Host "===================================================="
