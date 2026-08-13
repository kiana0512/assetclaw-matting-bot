param(
  [Parameter(Mandatory = $true)]
  [string]$Url,

  [Parameter(Mandatory = $true)]
  [string]$OutFile,

  [Parameter(Mandatory = $true)]
  [long]$TotalBytes,

  [Parameter(Mandatory = $true)]
  [string]$ExpectedMd5Base64,

  [ValidateRange(1, 32)]
  [int]$Segments = 12
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$outputPath = [System.IO.Path]::GetFullPath($OutFile)
$outputDirectory = [System.IO.Path]::GetDirectoryName($outputPath)
$partsDirectory = "${outputPath}.parts"
$completePath = "${outputPath}.complete"

[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null

$prefixBytes = 0L
if ([System.IO.File]::Exists($outputPath)) {
  $prefixBytes = [System.IO.FileInfo]::new($outputPath).Length
}

if ($prefixBytes -gt $TotalBytes) {
  throw "Existing prefix is larger than the expected file: $prefixBytes > $TotalBytes"
}

if ([System.IO.Directory]::Exists($partsDirectory)) {
  [System.IO.Directory]::Delete($partsDirectory, $true)
}
[System.IO.Directory]::CreateDirectory($partsDirectory) | Out-Null

$remainingBytes = $TotalBytes - $prefixBytes
$segmentSize = if ($remainingBytes -eq 0) { 0L } else { [long][math]::Ceiling($remainingBytes / [double]$Segments) }
$downloads = @()

for ($index = 0; $index -lt $Segments -and $remainingBytes -gt 0; $index++) {
  $start = $prefixBytes + ($index * $segmentSize)
  if ($start -ge $TotalBytes) {
    break
  }

  $end = [math]::Min($TotalBytes - 1, $start + $segmentSize - 1)
  $partPath = Join-Path $partsDirectory ("part-{0:D3}.bin" -f $index)
  $logPath = Join-Path $partsDirectory ("part-{0:D3}.stderr.log" -f $index)
  $arguments = @(
    "-L",
    "--fail",
    "--retry", "5",
    "--retry-delay", "2",
    "--retry-all-errors",
    "--range", "${start}-${end}",
    "--output", $partPath,
    $Url
  )

  $process = Start-Process -FilePath "curl.exe" -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardError $logPath
  $downloads += [pscustomobject]@{
    Index = $index
    Start = $start
    End = $end
    ExpectedBytes = $end - $start + 1
    PartPath = $partPath
    LogPath = $logPath
    Process = $process
  }
}

$lastReport = [datetime]::MinValue
while (($downloads | Where-Object { -not $_.Process.HasExited }).Count -gt 0) {
  if (((Get-Date) - $lastReport).TotalSeconds -ge 5) {
    $downloaded = $prefixBytes
    foreach ($download in $downloads) {
      if ([System.IO.File]::Exists($download.PartPath)) {
        $downloaded += [System.IO.FileInfo]::new($download.PartPath).Length
      }
    }
    $percent = [math]::Round(($downloaded / [double]$TotalBytes) * 100, 2)
    $downloadedMiB = [math]::Round($downloaded / 1MB, 2)
    $totalMiB = [math]::Round($TotalBytes / 1MB, 2)
    Write-Host "parallel_download=${percent}% (${downloadedMiB}/${totalMiB} MiB)"
    $lastReport = Get-Date
  }
  Start-Sleep -Seconds 1
}

$failed = @()
foreach ($download in $downloads) {
  $download.Process.WaitForExit()
  $actualBytes = if ([System.IO.File]::Exists($download.PartPath)) {
    [System.IO.FileInfo]::new($download.PartPath).Length
  } else {
    0L
  }

  $exitCode = $download.Process.ExitCode
  if ($actualBytes -ne $download.ExpectedBytes -or ($null -ne $exitCode -and $exitCode -ne 0)) {
    $failed += [pscustomobject]@{
      Index = $download.Index
      ExitCode = $exitCode
      ExpectedBytes = $download.ExpectedBytes
      ActualBytes = $actualBytes
      LogPath = $download.LogPath
    }
  }
}

if ($failed.Count -gt 0) {
  $failed | Format-Table -AutoSize
  throw "One or more ranged downloads failed; parts were retained for diagnosis."
}

if ([System.IO.File]::Exists($completePath)) {
  [System.IO.File]::Delete($completePath)
}

$destination = [System.IO.File]::Open($completePath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
  if ($prefixBytes -gt 0) {
    $prefix = [System.IO.File]::OpenRead($outputPath)
    try {
      $prefix.CopyTo($destination)
    } finally {
      $prefix.Dispose()
    }
  }

  foreach ($download in ($downloads | Sort-Object Index)) {
    $source = [System.IO.File]::OpenRead($download.PartPath)
    try {
      $source.CopyTo($destination)
    } finally {
      $source.Dispose()
    }
  }
} finally {
  $destination.Dispose()
}

$completedBytes = [System.IO.FileInfo]::new($completePath).Length
if ($completedBytes -ne $TotalBytes) {
  throw "Combined file length mismatch: $completedBytes != $TotalBytes"
}

$md5 = [System.Security.Cryptography.MD5]::Create()
try {
  $stream = [System.IO.File]::OpenRead($completePath)
  try {
    $md5Base64 = [Convert]::ToBase64String($md5.ComputeHash($stream))
  } finally {
    $stream.Dispose()
  }
} finally {
  $md5.Dispose()
}

if ($md5Base64 -ne $ExpectedMd5Base64) {
  throw "Content-MD5 mismatch: $md5Base64 != $ExpectedMd5Base64"
}

if ([System.IO.File]::Exists($outputPath)) {
  [System.IO.File]::Delete($outputPath)
}
[System.IO.File]::Move($completePath, $outputPath)
[System.IO.Directory]::Delete($partsDirectory, $true)

$sha256 = Get-FileHash -LiteralPath $outputPath -Algorithm SHA256
Write-Host "parallel_download=100%"
Write-Host "content_md5_base64=$md5Base64"
Write-Host "sha256=$($sha256.Hash)"
Write-Host "output=$outputPath"
