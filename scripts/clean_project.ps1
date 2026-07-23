param(
  [switch]$Apply,
  [switch]$IncludeRuntimeCaches
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$RepoRootWithSeparator = $ProjectRoot.TrimEnd("\") + "\"

function Test-SafeRepoPath {
  param([Parameter(Mandatory=$true)][string]$Path)
  $candidate = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $ProjectRoot $Path }
  $absolute = [System.IO.Path]::GetFullPath($candidate)
  if (-not $absolute.StartsWith($RepoRootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean outside repository: $absolute"
  }
  if ($absolute -eq $ProjectRoot) {
    throw "Refusing to clean repository root"
  }
  return $absolute
}

function Remove-SafePath {
  param([Parameter(Mandatory=$true)][string]$Path)
  $absolute = Test-SafeRepoPath $Path
  if (-not (Test-Path -LiteralPath $absolute)) { return }
  if ($Apply) {
    Remove-Item -LiteralPath $absolute -Recurse -Force
    Write-Host "removed: $absolute"
  } else {
    Write-Host "would remove: $absolute"
  }
}

Write-Host $(if ($Apply) { "Cleaning repository..." } else { "Dry run only. Re-run with -Apply to delete listed files." })

$cacheDirectoryNames = @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
$scanRoots = @("src", "tests", "scripts", "tools", "feishu_frame_tool", "external_webui")
$cacheDirs = [System.Collections.Generic.List[System.IO.DirectoryInfo]]::new()
foreach ($scanRoot in $scanRoots) {
  $absoluteScanRoot = Join-Path $ProjectRoot $scanRoot
  if (-not (Test-Path -LiteralPath $absoluteScanRoot -PathType Container)) { continue }
  Get-ChildItem -LiteralPath $absoluteScanRoot -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object {
      $_.FullName -notlike "*\node_modules\*" -and
      ($_.Name -in $cacheDirectoryNames -or $_.Name -like "*.egg-info")
    } |
    ForEach-Object { $cacheDirs.Add($_) }
}

Get-ChildItem -LiteralPath $ProjectRoot -Directory -Force -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -in $cacheDirectoryNames -or $_.Name -like ".pytest-tmp*" } |
  ForEach-Object { $cacheDirs.Add($_) }

$cacheDirs |
  Sort-Object { $_.FullName.Length } -Descending -Unique |
  ForEach-Object { Remove-SafePath $_.FullName }

$transientFiles = @(
  "logs\cloudflared.log",
  "logs\cloudflared.out.log",
  "logs\cloudflared.err.log",
  "logs\cloudflared.pid",
  "public_url.txt",
  "logs\public_url.txt"
)
foreach ($path in $transientFiles) { Remove-SafePath $path }
Remove-SafePath "external_webui\dist"
Remove-SafePath "external_webui\node_modules\.vite-temp"

if ($IncludeRuntimeCaches) {
  $runtimeCacheDirs = @(
    "storage\agent_jobs",
    "storage\animation_flow_runner",
    "storage\cherry_html_runner",
    "storage\sticker_cache",
    "storage\webui_uploads"
  )
  foreach ($path in $runtimeCacheDirs) { Remove-SafePath $path }
  Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "storage") -Directory -Filter "cherry_probe_*" -Force -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-SafePath $_.FullName }
} else {
  Write-Host "Runtime caches preserved. Add -IncludeRuntimeCaches to include explicitly disposable runtime caches."
}

Write-Host "Preserved: .env, data databases/backups, logs (except obsolete tunnel files), task inputs/outputs, and business assets."
Write-Host $(if ($Apply) { "Clean complete." } else { "Dry run complete; nothing was deleted." })
