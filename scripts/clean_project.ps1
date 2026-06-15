$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

Write-Host "Cleaning Python caches..."
Get-ChildItem -Recurse -Directory -Force |
  Where-Object {
    $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") -or
    $_.Name -like "*.egg-info"
  } |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Cleaning legacy tunnel artifacts..."
Remove-Item "logs\cloudflared.log" -Force -ErrorAction SilentlyContinue
Remove-Item "logs\cloudflared.err.log" -Force -ErrorAction SilentlyContinue

Write-Host "Cleaning test artifacts in storage/..."
Remove-Item "storage\README_copy*.md" -Force -ErrorAction SilentlyContinue
Remove-Item "storage\README_moved.md" -Force -ErrorAction SilentlyContinue
Remove-Item "storage\README_copy2_moved.md" -Force -ErrorAction SilentlyContinue
Remove-Item "storage\debug\brain_test_dir" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "storage\debug\script_test_dir" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Cleaning generated runtime records..."
$runtimeDirs = @(
  "storage\agent_jobs",
  "storage\animation_flow_runner",
  "storage\animation_flow_runs",
  "storage\custom_pipeline_runs",
  "storage\webui_uploads"
)
foreach ($dir in $runtimeDirs) {
  if (Test-Path $dir) {
    Get-ChildItem $dir -Force -ErrorAction SilentlyContinue |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Write-Host "Cleaning transient root duplicates..."
Remove-Item "SpriteAtlasGeneratorTool.cs" -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Clean done."
Write-Host "Preserved: .env, data/assetclaw.db, logs/*.log (non-cloudflared), storage/batch_*, src/, tests/, docs/, Unity project assets"
