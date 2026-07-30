param(
  [Parameter(Mandatory=$true)][string]$SnapshotRoot
)

$ErrorActionPreference = "Stop"
$ResolvedSnapshot = (Resolve-Path -LiteralPath $SnapshotRoot).Path
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ManifestPath = Join-Path $ResolvedSnapshot "snapshot_complete.json"
$EnvPath = Join-Path $ProjectRoot ".env"
$EncryptedEnvPath = Join-Path $ResolvedSnapshot "config\env.dpapi"
$ExpectedHashPath = Join-Path $ResolvedSnapshot "config\env.sha256"

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Missing snapshot manifest: $ManifestPath" }
if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { throw "Missing project .env: $EnvPath" }
if (-not (Test-Path -LiteralPath $ExpectedHashPath -PathType Leaf)) { throw "Missing expected .env hash: $ExpectedHashPath" }

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
Add-Type -AssemblyName System.Security
$PlainBytes = [System.IO.File]::ReadAllBytes($EnvPath)
try {
  $ProtectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
    $PlainBytes,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::LocalMachine
  )
  [System.IO.File]::WriteAllBytes($EncryptedEnvPath, $ProtectedBytes)

  $RoundTripBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
    [System.IO.File]::ReadAllBytes($EncryptedEnvPath),
    $null,
    [System.Security.Cryptography.DataProtectionScope]::LocalMachine
  )
  try {
    $Sha = [System.Security.Cryptography.SHA256]::Create()
    try { $ActualHash = ([BitConverter]::ToString($Sha.ComputeHash($RoundTripBytes))).Replace('-','') }
    finally { $Sha.Dispose() }
    $ExpectedHash = (Get-Content -LiteralPath $ExpectedHashPath -Raw -Encoding ascii).Trim()
    if ($ActualHash -ne $ExpectedHash) { throw "DPAPI round-trip hash mismatch." }
  } finally {
    [Array]::Clear($RoundTripBytes, 0, $RoundTripBytes.Length)
  }
} finally {
  [Array]::Clear($PlainBytes, 0, $PlainBytes.Length)
}

$PayloadFiles = Get-ChildItem -LiteralPath $ResolvedSnapshot -Recurse -File |
  Where-Object { $_.Name -ne 'snapshot_complete.json' }
$Payload = foreach ($File in $PayloadFiles) {
  [ordered]@{
    path = $File.FullName.Substring($ResolvedSnapshot.Length).TrimStart('\').Replace('\','/')
    size_bytes = $File.Length
    sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
  }
}
$Manifest.env_protection = "Windows DPAPI LocalMachine"
$Manifest.payload = @($Payload)
$Manifest | Add-Member -NotePropertyName resealed_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
$Manifest | Add-Member -NotePropertyName env_roundtrip_sha256_verified -NotePropertyValue $true -Force
$Manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $ManifestPath

[ordered]@{
  ok = $true
  snapshot_id = $Manifest.snapshot_id
  env_protection = $Manifest.env_protection
  env_roundtrip_sha256_verified = $true
  payload_files = @($Payload).Count
} | ConvertTo-Json -Depth 4
