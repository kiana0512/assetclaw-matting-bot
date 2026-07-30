param(
  [string]$Label = "stable-pre-optimization",
  [string]$GitExe = "git",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SafeLabel = ($Label -replace '[^A-Za-z0-9._-]', '_').Trim('_')
if (-not $SafeLabel) { $SafeLabel = "stable" }

if (-not $PythonExe) {
  $CondaPython = Join-Path $env:USERPROFILE "miniconda3\envs\assetclaw\python.exe"
  if (Test-Path -LiteralPath $CondaPython) {
    $PythonExe = $CondaPython
  } else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw "No Python runtime found. Pass -PythonExe explicitly." }
    $PythonExe = $PythonCommand.Source
  }
}
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python not found: $PythonExe" }

$GitCommand = Get-Command $GitExe -ErrorAction SilentlyContinue
if ($GitCommand) { $GitExe = $GitCommand.Source }
if (-not (Test-Path -LiteralPath $GitExe)) { throw "Git not found: $GitExe" }

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$GitHead = (& $GitExe -C $ProjectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to read Git HEAD." }
$GitShort = $GitHead.Substring(0, 12)
$SnapshotId = "${Timestamp}_${SafeLabel}_${GitShort}"
$SnapshotRoot = Join-Path $ProjectRoot "storage\rollback_snapshots\$SnapshotId"
if (Test-Path -LiteralPath $SnapshotRoot) { throw "Snapshot already exists: $SnapshotRoot" }

$CodeDir = Join-Path $SnapshotRoot "code"
$ConfigDir = Join-Path $SnapshotRoot "config"
$DataDir = Join-Path $SnapshotRoot "data"
$RuntimeDir = Join-Path $SnapshotRoot "runtime"
$StateDir = Join-Path $SnapshotRoot "state_json"
$ExternalDir = Join-Path $SnapshotRoot "external_versions"
New-Item -ItemType Directory -Force $CodeDir,$ConfigDir,$DataDir,$RuntimeDir,$StateDir,$ExternalDir | Out-Null

try {
  # Immutable tracked source at the exact stable commit.
  $TrackedHeadZip = Join-Path $CodeDir "tracked_head.zip"
  $TrackedHeadZipArg = "--output=$TrackedHeadZip"
  & $GitExe -C $ProjectRoot archive --format=zip $TrackedHeadZipArg HEAD
  if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
  & $GitExe -C $ProjectRoot bundle create (Join-Path $CodeDir "repository.bundle") HEAD
  if ($LASTEXITCODE -ne 0) { throw "git bundle failed" }
  $TrackedPatch = Join-Path $CodeDir "tracked_worktree.patch"
  $TrackedPatchArg = "--output=$TrackedPatch"
  & $GitExe -C $ProjectRoot diff --binary HEAD $TrackedPatchArg
  if ($LASTEXITCODE -ne 0) { throw "git diff failed" }
  & $GitExe -C $ProjectRoot status --short | Set-Content -Encoding utf8 (Join-Path $CodeDir "git_status.txt")
  & $GitExe -C $ProjectRoot branch --show-current | Set-Content -Encoding utf8 (Join-Path $CodeDir "git_branch.txt")

  # Preserve small untracked source/docs without copying ignored runtime or business binaries.
  $Untracked = @(& $GitExe -C $ProjectRoot -c core.quotepath=false ls-files --others --exclude-standard)
  $CopiedUntracked = @()
  foreach ($Relative in $Untracked) {
    $Normalized = $Relative.Replace('\','/')
    if ($Normalized -match '^(storage|data|logs|tmp|\.git)/') { continue }
    $Source = Join-Path $ProjectRoot $Relative
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { continue }
    $Destination = Join-Path (Join-Path $CodeDir "untracked") $Relative
    New-Item -ItemType Directory -Force (Split-Path $Destination -Parent) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
    $CopiedUntracked += $Relative
  }
  $CopiedUntracked | Set-Content -Encoding utf8 (Join-Path $CodeDir "untracked_files.txt")

  # Encrypt the exact .env with Windows DPAPI LocalMachine so operators on this host can restore it.
  # Plaintext is never written into the snapshot.
  $EnvPath = Join-Path $ProjectRoot ".env"
  if (Test-Path -LiteralPath $EnvPath) {
    Add-Type -AssemblyName System.Security
    $EnvBytes = [System.IO.File]::ReadAllBytes($EnvPath)
    $ProtectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
      $EnvBytes,
      $null,
      [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    [System.IO.File]::WriteAllBytes((Join-Path $ConfigDir "env.dpapi"), $ProtectedBytes)
    (Get-FileHash -LiteralPath $EnvPath -Algorithm SHA256).Hash | Set-Content -Encoding ascii (Join-Path $ConfigDir "env.sha256")
    Get-Content -LiteralPath $EnvPath | ForEach-Object {
      if ($_ -match '^\s*#' -or $_ -notmatch '=') { $_ }
      else { ($_.Split('=',2)[0] + '=<REDACTED>') }
    } | Set-Content -Encoding utf8 (Join-Path $ConfigDir "env.redacted")
  }

  # Online SQLite backup preserves WAL consistency while services keep running.
  $DatabasePath = Join-Path $ProjectRoot "data\assetclaw.db"
  if (Test-Path -LiteralPath $DatabasePath) {
    & $PythonExe (Join-Path $PSScriptRoot "create_sqlite_backup.py") $DatabasePath (Join-Path $DataDir "assetclaw.db") --report (Join-Path $DataDir "assetclaw.db.report.json") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "SQLite online backup failed" }
  }

  # Preserve JSON/YAML runtime identity and task state, not large frames/videos/artifacts.
  $StorageRoot = Join-Path $ProjectRoot "storage"
  if (Test-Path -LiteralPath $StorageRoot) {
    $RollbackSnapshotsRoot = Join-Path $StorageRoot "rollback_snapshots"
    $StateFiles = Get-ChildItem -LiteralPath $StorageRoot -Recurse -File -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Extension -in @('.json','.jsonl','.yaml','.yml') -and
        $_.Length -le 10MB -and
        $_.FullName -notlike "$RollbackSnapshotsRoot\*"
      }
    foreach ($File in $StateFiles) {
      $Relative = $File.FullName.Substring($StorageRoot.Length).TrimStart('\')
      $Destination = Join-Path $StateDir $Relative
      New-Item -ItemType Directory -Force (Split-Path $Destination -Parent) | Out-Null
      Copy-Item -LiteralPath $File.FullName -Destination $Destination
    }
  }

  # Runtime health snapshot. Failures are recorded and never stop or restart services.
  $Health = [ordered]@{}
  try { $Health.gateway = Invoke-RestMethod "http://127.0.0.1:7865/health" -TimeoutSec 3 } catch { $Health.gateway_error = $_.Exception.Message }
  try { $Health.webui_http_status = (Invoke-WebRequest "http://127.0.0.1:5180" -UseBasicParsing -TimeoutSec 3).StatusCode } catch { $Health.webui_error = $_.Exception.Message }
  try { $Health.comfyui = Invoke-RestMethod "http://127.0.0.1:8188/system_stats" -TimeoutSec 3 } catch { $Health.comfyui_error = $_.Exception.Message }
  $Health | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $RuntimeDir "health.json")

  Get-Process | Where-Object { $_.ProcessName -match 'python|node|chrome|msedge' } |
    Select-Object Id,ProcessName,StartTime,Path |
    ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $RuntimeDir "processes.json")

  & $PythonExe --version 2>&1 | Set-Content -Encoding utf8 (Join-Path $RuntimeDir "python_version.txt")
  & $PythonExe -m pip freeze | Set-Content -Encoding utf8 (Join-Path $RuntimeDir "pip_freeze.txt")

  # Record the external ImageClip repository without mutating it.
  $ImageClipRoot = "C:\imageclip"
  if ((Test-Path -LiteralPath $ImageClipRoot) -and (Test-Path -LiteralPath (Join-Path $ImageClipRoot ".git"))) {
    $ImageClipSafeDirectory = $ImageClipRoot.Replace('\','/')
    & $GitExe -c "safe.directory=$ImageClipSafeDirectory" -C $ImageClipRoot rev-parse HEAD | Set-Content -Encoding ascii (Join-Path $ExternalDir "imageclip_head.txt")
    & $GitExe -c "safe.directory=$ImageClipSafeDirectory" -C $ImageClipRoot status --short | Set-Content -Encoding utf8 (Join-Path $ExternalDir "imageclip_status.txt")
    & $GitExe -c "safe.directory=$ImageClipSafeDirectory" -C $ImageClipRoot bundle create (Join-Path $ExternalDir "imageclip_repository.bundle") HEAD
    if ($LASTEXITCODE -ne 0) { throw "ImageClip git bundle failed" }
  }

  $WorkflowCandidates = @(
    "C:\Users\$env:USERNAME\Desktop\ComfyUI-aki-v3\ComfyUI\user\default\workflows\ImageClip.json",
    "C:\imageclip\ImageClip.json"
  )
  $WorkflowHashes = @()
  foreach ($Candidate in $WorkflowCandidates) {
    if (Test-Path -LiteralPath $Candidate) {
      $WorkflowHashes += [ordered]@{
        path = $Candidate
        size_bytes = (Get-Item -LiteralPath $Candidate).Length
        sha256 = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash
      }
    }
  }
  $WorkflowHashes | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $ExternalDir "workflow_hashes.json")

  # Hash every backup payload and write the completion marker last.
  $PayloadFiles = Get-ChildItem -LiteralPath $SnapshotRoot -Recurse -File | Where-Object { $_.Name -ne 'snapshot_complete.json' }
  $Payload = foreach ($File in $PayloadFiles) {
    [ordered]@{
      path = $File.FullName.Substring($SnapshotRoot.Length).TrimStart('\').Replace('\','/')
      size_bytes = $File.Length
      sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
    }
  }
  $Manifest = [ordered]@{
    schema_version = "1.0"
    snapshot_id = $SnapshotId
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    timezone = [System.TimeZoneInfo]::Local.Id
    project_root = $ProjectRoot
    git_head = $GitHead
    git_branch = (& $GitExe -C $ProjectRoot branch --show-current).Trim()
    label = $Label
    python_executable = $PythonExe
    env_protection = "Windows DPAPI LocalMachine"
    database_restore_policy = "Never automatic; preserve current DB for ordinary code rollback"
    services_were_stopped = $false
    payload = @($Payload)
  }
  $Manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $SnapshotRoot "snapshot_complete.json")
  Write-Output $SnapshotRoot
} catch {
  $_ | Out-String | Set-Content -Encoding utf8 (Join-Path $SnapshotRoot "SNAPSHOT_FAILED.txt")
  throw
}
