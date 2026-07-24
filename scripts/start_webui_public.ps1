param(
  [string]$LocalUrl = "http://127.0.0.1:5180"
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

$localUri = [Uri]$LocalUrl
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

function Stop-Listener([int]$Port) {
  Get-ListenerProcessIds $Port |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

function Get-WebUiState {
  try {
    Invoke-WebRequest $LocalUrl -UseBasicParsing -TimeoutSec 3 | Out-Null
    return "ready"
  } catch {
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode.value__ -eq 401) {
      return "auth"
    }
    return "down"
  }
}

function Wait-WebUi {
  for ($i = 0; $i -lt 30; $i++) {
    if ((Get-WebUiState) -eq "ready") { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Find-PublicUrl {
  foreach ($path in @($TunnelOutLog, $TunnelErrLog)) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $content = Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace([string]$content)) { continue }
    $match = [regex]::Matches([string]$content, "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($match.Count -gt 0) { return $match[$match.Count - 1].Value }
  }
  return $null
}

# An older version restarted 5180 with Basic Auth. Repair only that WebUI
# listener; the Gateway on 7865 and all workers are outside this script.
$state = Get-WebUiState
if ($state -eq "auth") {
  Write-Host "Removing the old WebUI login prompt..."
  Stop-Listener $localUri.Port
  $state = "down"
}
if ($state -eq "down") {
  Write-Host "Starting the password-free WebUI..."
  $env:ASSETCLAW_WEBUI_HOST = "127.0.0.1"
  $env:ASSETCLAW_WEBUI_PORT = [string]$localUri.Port
  Remove-Item Env:ASSETCLAW_WEBUI_SHARE_USERNAME -ErrorAction SilentlyContinue
  Remove-Item Env:ASSETCLAW_WEBUI_SHARE_PASSWORD -ErrorAction SilentlyContinue
  Start-Process -FilePath $PowerShellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "start_external_webui.ps1")) `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput (Join-Path $ProjectRoot "logs\webui_console.out.log") `
    -RedirectStandardError (Join-Path $ProjectRoot "logs\webui_console.err.log") `
    -WindowStyle Hidden | Out-Null
}
if (-not (Wait-WebUi)) {
  throw "WebUI did not become ready at $LocalUrl. Check logs\webui_console.err.log."
}

$cloudflared = Get-CloudflaredExe
Stop-PidFromFile $TunnelPidFile
Remove-Item -LiteralPath $TunnelOutLog,$TunnelErrLog,$PublicUrlFile,$AccessFile -Force -ErrorAction SilentlyContinue

Write-Host "Starting password-free Cloudflare quick tunnel..."
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
  "LOCAL_URL=$LocalUrl",
  "PASSWORD=disabled"
) | Set-Content -LiteralPath $AccessFile -Encoding UTF8
Set-Content -LiteralPath $PublicUrlFile -Value $publicUrl -Encoding ASCII
try { Set-Clipboard -Value $publicUrl } catch {}
try { Start-Process $publicUrl } catch { Start-Process $LocalUrl }

Write-Host ""
Write-Host "============= PASSWORD-FREE WEBUI READY =============" -ForegroundColor Green
Write-Host "Local URL (no password): $LocalUrl"
Write-Host "Public URL (no password): $publicUrl"
Write-Host "The public URL was copied to the clipboard. Open it directly."
Write-Host "To stop the public tunnel, run stop_public_webui.bat"
Write-Host "====================================================="
