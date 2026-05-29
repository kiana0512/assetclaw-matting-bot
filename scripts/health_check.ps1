# Full health check for AssetClaw Win3090 Skill Node
$Base = "http://127.0.0.1:7865"
$SkillToken = $env:SKILL_API_TOKEN
if (-not $SkillToken) { $SkillToken = "please_change_me" }

Write-Host "=== NVIDIA GPU ===" -ForegroundColor Cyan
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>$null `
    | ForEach-Object { Write-Host "  GPU: $_" }

Write-Host "`n=== Gateway Health ===" -ForegroundColor Cyan
try {
    $h = Invoke-RestMethod "$Base/health" -Method GET
    Write-Host ($h | ConvertTo-Json -Depth 2)
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

Write-Host "`n=== Queue Status ===" -ForegroundColor Cyan
try {
    $q = Invoke-RestMethod "$Base/admin/queue" -Method GET
    Write-Host ($q | ConvertTo-Json)
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

Write-Host "`n=== Skill Manifest ===" -ForegroundColor Cyan
try {
    $m = Invoke-RestMethod "$Base/skills/v1/manifest" `
        -Method GET -Headers @{"X-Skill-Token"=$SkillToken}
    Write-Host "  Node: $($m.node_name)"
    Write-Host "  Skills: $($m.available_skills.Count) total"
    $impl = ($m.available_skills | Where-Object { $_.implemented }).Count
    Write-Host "  Implemented: $impl"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

Write-Host "`n=== ComfyUI Status ===" -ForegroundColor Cyan
try {
    $c = Invoke-RestMethod "$Base/admin/comfyui/status" -Method GET
    Write-Host ($c | ConvertTo-Json)
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

Write-Host "`n=== MCP Info ===" -ForegroundColor Cyan
try {
    $mcp = Invoke-RestMethod "$Base/mcp/info" -Method GET
    Write-Host ($mcp | ConvertTo-Json)
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

Write-Host "`nHealth check complete." -ForegroundColor Green
