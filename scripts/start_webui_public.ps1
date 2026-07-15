param(
  [string]$LocalUrl = "http://127.0.0.1:5180",
  [switch]$NoRestartWebUi
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$PowerShellExe = (Get-Process -Id $PID).Path
if ([string]::IsNullOrWhiteSpace($PowerShellExe) -or -not (Test-Path -LiteralPath $PowerShellExe -PathType Leaf)) {
  throw "Unable to resolve current PowerShell executable."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

$TunnelOutLog = Join-Path $ProjectRoot "logs\cloudflared.out.log"
$TunnelErrLog = Join-Path $ProjectRoot "logs\cloudflared.err.log"
$TunnelPidFile = Join-Path $ProjectRoot "logs\cloudflared.pid"
$PublicUrlFile = Join-Path $ProjectRoot "logs\public_webui_url.txt"

function Get-CloudflaredExe {
  $candidates = @(
    $env:CLOUDFLARED_EXE,
    (Join-Path $ProjectRoot "tools\cloudflared.exe"),
    (Join-Path $ProjectRoot "cloudflared.exe"),
    (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe")
  ) | Where-Object { $_ }

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
  }

  $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }

  throw @"
cloudflared.exe not found.

Install Cloudflare Tunnel first, then run this script again:
  winget install --id Cloudflare.cloudflared

Or put cloudflared.exe at:
  $ProjectRoot\tools\cloudflared.exe
"@
}

function Stop-OldTunnel {
  if (Test-Path $TunnelPidFile) {
    $oldPid = Get-Content $TunnelPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid -and ($oldPid -as [int])) {
      Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
    }
  }
}

function Test-WebUi {
  try {
    Invoke-WebRequest $LocalUrl -UseBasicParsing -TimeoutSec 3 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Stop-WebUiOnly {
  $uri = [Uri]$LocalUrl
  $port = $uri.Port
  $listeners = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
  if (-not $listeners) { return }

  $processIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    Write-Host "Stopping WebUI PID=${processId} on port ${port}..."
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
  }
}

function Start-WebUiOnly {
  Write-Host "Starting WebUI only. Backend Agent/Gateway will not be touched..."
  Start-Process -FilePath $PowerShellExe `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File scripts\start_external_webui.ps1" `
    -RedirectStandardOutput "logs\webui_console.out.log" `
    -RedirectStandardError "logs\webui_console.err.log" `
    -WindowStyle Hidden
}

function Wait-WebUi {
  for ($i = 0; $i -lt 30; $i++) {
    if (Test-WebUi) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Get-TunnelUrl {
  $text = ""
  foreach ($path in @($TunnelOutLog, $TunnelErrLog)) {
    if (Test-Path $path) {
      $text += "`n" + (Get-Content $path -Raw -ErrorAction SilentlyContinue)
    }
  }

  $matches = [regex]::Matches($text, "https://[a-z0-9-]+\.trycloudflare\.com")
  if ($matches.Count -gt 0) {
    return $matches[$matches.Count - 1].Value
  }
  return $null
}

function Wait-TunnelUrl {
  param([int]$TimeoutSeconds = 45)

  for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
    $url = Get-TunnelUrl
    if ($url) { return $url }
    Start-Sleep -Seconds 1
  }
  return $null
}

function Show-PublicUrl {
  param([string]$Url)

  Write-Host ""
  Write-Host "============================================================"
  Write-Host "  WEBUI PUBLIC URL"
  Write-Host ""
  Write-Host "  $Url"
  Write-Host ""
  Write-Host "  The URL has been copied to your clipboard."
  Write-Host "  Send this URL to your teammates."
  Write-Host "============================================================"
  Write-Host ""
}

Write-Host ""
Write-Host "AssetClaw Public WebUI"
Write-Host "Local WebUI: $LocalUrl"
Write-Host ""

$cloudflared = Get-CloudflaredExe
Write-Host "cloudflared: $cloudflared"

if (-not $NoRestartWebUi) {
  Stop-WebUiOnly
  Start-WebUiOnly
} elseif (-not (Test-WebUi)) {
  throw "WebUI is not reachable at $LocalUrl. Run without -NoRestartWebUi to restart WebUI only."
}

if (-not (Wait-WebUi)) {
  throw "WebUI did not become ready at $LocalUrl. Backend Agent/Gateway was not touched."
}

Write-Host "Starting Cloudflare quick tunnel..."
Stop-OldTunnel
Remove-Item -LiteralPath $TunnelOutLog, $TunnelErrLog, $PublicUrlFile -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $cloudflared `
  -ArgumentList @("tunnel", "--url", $LocalUrl, "--no-autoupdate") `
  -RedirectStandardOutput $TunnelOutLog `
  -RedirectStandardError $TunnelErrLog `
  -WindowStyle Hidden `
  -PassThru

Set-Content -Path $TunnelPidFile -Value $proc.Id -Encoding ASCII

$publicUrl = Wait-TunnelUrl
if (-not $publicUrl) {
  Write-Host "Cloudflare tunnel did not print a public URL yet."
  Write-Host "Check logs:"
  Write-Host "  logs\cloudflared.out.log"
  Write-Host "  logs\cloudflared.err.log"
  exit 1
}

Set-Content -Path $PublicUrlFile -Value $publicUrl -Encoding ASCII
try {
  Set-Clipboard -Value $publicUrl
} catch {
  Write-Host "Clipboard copy failed, but the URL is printed below."
}

Show-PublicUrl $publicUrl
Write-Host "Local WebUI:    $LocalUrl"
Write-Host "URL file:       logs\public_webui_url.txt"
Write-Host "Tunnel logs:    logs\cloudflared.out.log / logs\cloudflared.err.log"
Write-Host "Stop command:   & `"$PowerShellExe`" -NoProfile -ExecutionPolicy Bypass -File scripts\stop_webui_public.ps1"
Write-Host ""
Write-Host "Note: trycloudflare free quick tunnels usually generate a new URL each start."
Write-Host ""
