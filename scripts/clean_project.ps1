$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"
$RepoRoot = (Resolve-Path ".").Path

function Remove-RepoChild {
  param([Parameter(Mandatory=$true)][string]$Path)
  if (-not (Test-Path $Path)) { return }
  $resolved = (Resolve-Path $Path).Path
  if (-not $resolved.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean outside repo: $resolved"
  }
  Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Cleaning Python caches..."
Get-ChildItem -Recurse -Directory -Force |
  Where-Object {
    $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") -or
    $_.Name -like "*.egg-info"
  } |
  ForEach-Object { Remove-RepoChild $_.FullName }

Write-Host "Cleaning legacy tunnel artifacts..."
Remove-Item "logs\cloudflared.log" -Force -ErrorAction SilentlyContinue
Remove-Item "logs\cloudflared.out.log" -Force -ErrorAction SilentlyContinue
Remove-Item "logs\cloudflared.err.log" -Force -ErrorAction SilentlyContinue
Remove-Item "logs\cloudflared.pid" -Force -ErrorAction SilentlyContinue

Write-Host "Cleaning test artifacts in storage/..."
Remove-Item "storage\README_copy*.md" -Force -ErrorAction SilentlyContinue
Remove-Item "storage\README_moved.md" -Force -ErrorAction SilentlyContinue
Remove-Item "storage\README_copy2_moved.md" -Force -ErrorAction SilentlyContinue
Remove-RepoChild "storage\debug\brain_test_dir"
Remove-RepoChild "storage\debug\script_test_dir"

Write-Host "Cleaning generated runtime records..."
$runtimeDirs = @(
  "storage\agent_jobs",
  "storage\animation_flow_runner",
  "storage\animation_flow_runs",
  "storage\cherry_html_runner",
  "storage\custom_pipeline_runs",
  "storage\direct_image_runs",
  "storage\direct_video_runs",
  "storage\sticker_cache",
  "storage\webui_uploads"
)
foreach ($dir in $runtimeDirs) {
  if (Test-Path $dir) {
    Get-ChildItem $dir -Force -ErrorAction SilentlyContinue |
      ForEach-Object { Remove-RepoChild $_.FullName }
  }
}

Write-Host "Cleaning transient root duplicates..."
Remove-Item "SpriteAtlasGeneratorTool.cs" -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Clean done."
Write-Host "Preserved: .env, data/assetclaw.db, logs/*.log (non-cloudflared), storage/batch_*, src/, tests/, docs/, Unity project assets"
