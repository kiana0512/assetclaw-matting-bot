# Expose local Gateway via cloudflared quick tunnel
# Usage: powershell -ExecutionPolicy Bypass -File scripts\expose_gateway_cloudflared.ps1
#
# Requires: cloudflared in PATH
#   winget install Cloudflare.cloudflared
#   or: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
#
# NOTE: Quick tunnel URL changes on every restart.
#       After restart, update Feishu backend with the new URL.

$ErrorActionPreference = "Continue"
$ProjectRoot = "E:\assetclaw-matting-bot"
Set-Location $ProjectRoot

# ── Check cloudflared ─────────────────────────────────────────────────────────
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: cloudflared not found in PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install via winget:" -ForegroundColor Yellow
    Write-Host "  winget install Cloudflare.cloudflared" -ForegroundColor White
    Write-Host ""
    Write-Host "Or download from:" -ForegroundColor Yellow
    Write-Host "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" -ForegroundColor White
    exit 1
}

Write-Host "Starting cloudflared quick tunnel -> http://127.0.0.1:7865 ..." -ForegroundColor Cyan
Write-Host "(Waiting for tunnel URL, up to 60s)" -ForegroundColor Gray
Write-Host ""

# ── Start cloudflared, capture output ─────────────────────────────────────────
# Start as a background job so we can read its merged stdout+stderr
$cfExe = (Get-Command cloudflared).Source
$cfJob = Start-Job -ScriptBlock {
    param($exe)
    & $exe tunnel --url http://127.0.0.1:7865 2>&1
} -ArgumentList $cfExe

# Poll job output for URL (up to 60 seconds)
$PublicUrl = $null
$allOutput = @()

for ($i = 1; $i -le 60; $i++) {
    Start-Sleep 1
    Write-Host "." -NoNewline

    $chunk = Receive-Job $cfJob -Keep 2>$null
    if ($chunk) { $allOutput += $chunk }

    foreach ($line in $allOutput) {
        if ("$line" -match 'https://[a-z0-9\-]+\.trycloudflare\.com') {
            $PublicUrl = $Matches[0]
            break
        }
    }
    if ($PublicUrl) { break }
}
Write-Host ""

# ── Handle URL not found ──────────────────────────────────────────────────────
if (-not $PublicUrl) {
    Write-Host ""
    Write-Host "Could not auto-detect URL within 60 seconds." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please manually look for the URL in cloudflared output above," -ForegroundColor Yellow
    Write-Host "then fill in Feishu callback URL as:" -ForegroundColor Yellow
    Write-Host "  https://<your-url>.trycloudflare.com/feishu/events" -ForegroundColor White
    Write-Host ""
    Write-Host "And update .env:" -ForegroundColor Yellow
    Write-Host "  PUBLIC_BASE_URL=https://<your-url>.trycloudflare.com" -ForegroundColor White
    Write-Host ""
    Write-Host "cloudflared job is still running. Press Ctrl+C to stop." -ForegroundColor Gray
    Wait-Job $cfJob | Out-Null
    exit 0
}

$FeishuCallbackUrl = "$PublicUrl/feishu/events"

# ── Update .env ───────────────────────────────────────────────────────────────
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    $content = [System.IO.File]::ReadAllText($EnvFile)
    if ($content -match '(?m)^PUBLIC_BASE_URL=') {
        $content = [regex]::Replace($content, '(?m)^PUBLIC_BASE_URL=.*$', "PUBLIC_BASE_URL=$PublicUrl")
    } else {
        $content = $content.TrimEnd() + "`nPUBLIC_BASE_URL=$PublicUrl`n"
    }
    [System.IO.File]::WriteAllText($EnvFile, $content, [System.Text.Encoding]::UTF8)
    Write-Host ".env PUBLIC_BASE_URL updated." -ForegroundColor Green
} else {
    Write-Host ".env not found, skipping update." -ForegroundColor Yellow
}

# ── Write logs/public_url.txt ─────────────────────────────────────────────────
$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }
$Now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$logContent = @"
PUBLIC_BASE_URL=$PublicUrl
FEISHU_CALLBACK_URL=$FeishuCallbackUrl
generated_at=$Now
note=cloudflared quick tunnel URL changes on each restart -- re-fill Feishu backend each time
"@
[System.IO.File]::WriteAllText(
    (Join-Path $LogsDir "public_url.txt"),
    $logContent,
    [System.Text.Encoding]::UTF8
)

# ── Copy to clipboard ─────────────────────────────────────────────────────────
try { Set-Clipboard $FeishuCallbackUrl } catch {}

# ── Print summary ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 62) -ForegroundColor Green
Write-Host "  AssetClaw Gateway 隧道已建立" -ForegroundColor Green
Write-Host ("=" * 62) -ForegroundColor Green
Write-Host ""
Write-Host "  Public URL:" -ForegroundColor Cyan
Write-Host "  $PublicUrl" -ForegroundColor White
Write-Host ""
Write-Host "  飞书开放平台 > 开发配置 > 事件与回调 > 请求地址：" -ForegroundColor Cyan
Write-Host "  $FeishuCallbackUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "  [已复制到剪贴板]" -ForegroundColor Green
Write-Host "  已写入: logs\public_url.txt" -ForegroundColor Green
Write-Host "  已更新: .env PUBLIC_BASE_URL" -ForegroundColor Green
Write-Host ""
Write-Host "  注意：quick tunnel 每次重启地址会变，重启后需重新填飞书后台。" -ForegroundColor Yellow
Write-Host ("=" * 62) -ForegroundColor Green
Write-Host ""
Write-Host "cloudflared 仍在运行，按 Ctrl+C 停止隧道。" -ForegroundColor Gray

# Keep cloudflared alive
Wait-Job $cfJob | Out-Null
