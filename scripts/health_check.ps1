# Health check for AssetClaw Gateway
# Usage: powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1

$Base = "http://127.0.0.1:7865"

# Read SKILL_API_TOKEN from .env if not in environment
$SkillToken = $env:SKILL_API_TOKEN
if (-not $SkillToken) {
    $EnvFile = "E:\assetclaw-matting-bot\.env"
    if (Test-Path $EnvFile) {
        $line = Select-String -Path $EnvFile -Pattern '^SKILL_API_TOKEN=' | Select-Object -First 1
        if ($line) { $SkillToken = ($line.Line -split '=', 2)[1].Trim() }
    }
}
if (-not $SkillToken) { $SkillToken = "please_change_me" }

# ── 1. Gateway health ──────────────────────────────────────────────────────────
Write-Host "`n=== Gateway Health ===" -ForegroundColor Cyan
try {
    $h = Invoke-RestMethod "$Base/health" -Method GET
    Write-Host ($h | ConvertTo-Json -Depth 2) -ForegroundColor White
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# ── 2. Feishu events endpoint (GET returns 405, that's OK) ────────────────────
Write-Host "`n=== Feishu Events Endpoint ===" -ForegroundColor Cyan
try {
    $fe = Invoke-WebRequest "$Base/feishu/events" -Method GET -ErrorAction Stop
    Write-Host "  GET $Base/feishu/events -> $($fe.StatusCode)" -ForegroundColor White
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 405) {
        Write-Host "  GET -> 405 Method Not Allowed (expected — use POST for real events)" -ForegroundColor Green
    } elseif ($code -eq 422) {
        Write-Host "  GET -> 422 (endpoint reachable, POST required)" -ForegroundColor Green
    } else {
        Write-Host "  GET -> $code (endpoint may not be running)" -ForegroundColor Yellow
    }
}

# ── 3. Brain test ─────────────────────────────────────────────────────────────
Write-Host "`n=== Brain Test ===" -ForegroundColor Cyan
try {
    $bt = Invoke-RestMethod "$Base/brain/test" -Method POST `
        -ContentType "application/json" `
        -Body '{"text":"hello"}'
    Write-Host "  ok=$($bt.ok)  provider=$($bt.provider)" -ForegroundColor White
    Write-Host "  response: $($bt.response.Substring(0, [Math]::Min(120, $bt.response.Length)))..." -ForegroundColor Gray
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# ── 4. Skill manifest ──────────────────────────────────────────────────────────
Write-Host "`n=== Skill Manifest ===" -ForegroundColor Cyan
try {
    $m = Invoke-RestMethod "$Base/skills/v1/manifest" `
        -Method GET -Headers @{"X-Skill-Token" = $SkillToken}
    $impl = ($m.available_skills | Where-Object { $_.implemented }).Count
    Write-Host "  Node: $($m.node_name)" -ForegroundColor White
    Write-Host "  Skills: $($m.available_skills.Count) total, $impl implemented" -ForegroundColor White
    $fileSkills = $m.available_skills | Where-Object { $_.name -like "file.*" }
    Write-Host "  File skills: $($fileSkills | ForEach-Object { $_.name } | Join-String -Separator ', ')" -ForegroundColor White
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# ── 5. Quick queue check ───────────────────────────────────────────────────────
Write-Host "`n=== Queue Status ===" -ForegroundColor Cyan
try {
    $q = Invoke-RestMethod "$Base/admin/queue" -Method GET
    Write-Host ($q | ConvertTo-Json) -ForegroundColor White
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# ── 6. Public URL from logs ────────────────────────────────────────────────────
Write-Host "`n=== Public URL (cloudflared) ===" -ForegroundColor Cyan
$pubFile = "E:\assetclaw-matting-bot\logs\public_url.txt"
if (Test-Path $pubFile) {
    Get-Content $pubFile | ForEach-Object { Write-Host "  $_" -ForegroundColor White }
} else {
    Write-Host "  logs\public_url.txt not found — run scripts\expose_gateway_cloudflared.ps1 first" -ForegroundColor Yellow
}

Write-Host "`nHealth check complete." -ForegroundColor Green
