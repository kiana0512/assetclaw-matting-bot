param(
  [int]$MaxTrackedFileMiB = 20
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

function Get-TrackedFiles {
  $output = & git -c core.quotepath=false ls-files
  if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed"
  }
  return @($output)
}

$tracked = Get-TrackedFiles
$violations = [System.Collections.Generic.List[string]]::new()
$allowedMarkers = @(
  "data/.gitkeep",
  "logs/.gitkeep",
  "storage/.gitkeep"
)

foreach ($path in $tracked) {
  $normalized = $path.Replace("\", "/").Trim('"')
  $isRuntime =
    $normalized.StartsWith("data/", [System.StringComparison]::OrdinalIgnoreCase) -or
    $normalized.StartsWith("logs/", [System.StringComparison]::OrdinalIgnoreCase) -or
    $normalized.StartsWith("storage/", [System.StringComparison]::OrdinalIgnoreCase)

  if ($isRuntime -and $normalized -notin $allowedMarkers) {
    $violations.Add("runtime file is tracked: $normalized")
  }
  if ($normalized -like ".pytest-tmp*/*" -or $normalized.StartsWith(".pytest_cache/")) {
    $violations.Add("pytest artifact is tracked: $normalized")
  }
  if ($normalized.StartsWith("external_webui/dist/")) {
    $violations.Add("generated frontend build is tracked: $normalized")
  }
  if ($normalized.StartsWith(".idea/") -or $normalized.StartsWith(".vscode/")) {
    $violations.Add("editor state is tracked: $normalized")
  }

  $fullPath = Join-Path $ProjectRoot $normalized
  if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
    $sizeMiB = (Get-Item -LiteralPath $fullPath).Length / 1MB
    if ($sizeMiB -gt $MaxTrackedFileMiB) {
      $violations.Add(("tracked file exceeds {0} MiB: {1} ({2:N1} MiB)" -f $MaxTrackedFileMiB, $normalized, $sizeMiB))
    }
  }
}

if ($violations.Count -gt 0) {
  Write-Host "Repository hygiene check FAILED:" -ForegroundColor Red
  $violations | Sort-Object -Unique | ForEach-Object { Write-Host "  - $_" }
  exit 1
}

Write-Host "Repository hygiene check passed: $($tracked.Count) tracked files inspected."
