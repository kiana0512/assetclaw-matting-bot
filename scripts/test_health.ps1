# Quick smoke tests against the running gateway.
$Base = "http://127.0.0.1:7865"

Write-Host "=== GET /health ===" -ForegroundColor Cyan
Invoke-RestMethod -Uri "$Base/health" -Method GET | ConvertTo-Json

Write-Host "`n=== GET /admin/queue ===" -ForegroundColor Cyan
Invoke-RestMethod -Uri "$Base/admin/queue" -Method GET | ConvertTo-Json

Write-Host "`n=== GET /admin/batches ===" -ForegroundColor Cyan
Invoke-RestMethod -Uri "$Base/admin/batches" -Method GET | ConvertTo-Json

Write-Host "`n=== GET /admin/comfyui/status ===" -ForegroundColor Cyan
Invoke-RestMethod -Uri "$Base/admin/comfyui/status" -Method GET | ConvertTo-Json
