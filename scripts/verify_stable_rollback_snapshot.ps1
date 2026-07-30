param(
  [Parameter(Mandatory=$true)][string]$SnapshotRoot
)

$ErrorActionPreference = "Stop"
$Resolved = (Resolve-Path -LiteralPath $SnapshotRoot).Path
$ManifestPath = Join-Path $Resolved "snapshot_complete.json"
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Missing completion marker: $ManifestPath" }
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$Failures = @()
foreach ($Entry in $Manifest.payload) {
  $Path = Join-Path $Resolved ($Entry.path.Replace('/','\'))
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    $Failures += "missing: $($Entry.path)"
    continue
  }
  $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
  if ($Actual -ne $Entry.sha256) { $Failures += "hash mismatch: $($Entry.path)" }
}
if ($Failures.Count) { throw ($Failures -join [Environment]::NewLine) }

$DbReportPath = Join-Path $Resolved "data\assetclaw.db.report.json"
if (Test-Path -LiteralPath $DbReportPath) {
  $DbReport = Get-Content -LiteralPath $DbReportPath -Raw -Encoding utf8 | ConvertFrom-Json
  if ($DbReport.integrity_check -ne 'ok' -or $DbReport.quick_check -ne 'ok') {
    throw "SQLite report is not healthy."
  }
}

[ordered]@{
  ok = $true
  snapshot_id = $Manifest.snapshot_id
  git_head = $Manifest.git_head
  payload_files = @($Manifest.payload).Count
  env_protection = $Manifest.env_protection
  database_restore_policy = $Manifest.database_restore_policy
} | ConvertTo-Json -Depth 4
