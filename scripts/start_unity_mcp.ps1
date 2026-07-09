param(
  [string]$HttpUrl = "http://127.0.0.1:8080",
  [string]$PackageVersion = "9.6.6",
  [switch]$NoStopExisting
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

function Get-UvxExe {
  $candidates = @(
    (Join-Path $env:USERPROFILE "miniconda3\Scripts\uvx.exe"),
    (Join-Path $env:USERPROFILE ".local\bin\uvx.exe")
  )
  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { return $candidate }
  }

  $cmd = Get-Command uvx -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "uvx not found. MCP For Unity needs uvx to start the local HTTP server."
}

function Get-PortFromUrl {
  param([string]$Url)
  try {
    $uri = [System.Uri]$Url
    if ($uri.Port -gt 0) { return $uri.Port }
  } catch {}
  return 8080
}

function Stop-UnityMcpOnPort {
  param([int]$Port)
  $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if (-not $listeners) { return }

  $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $pids) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    $looksLikeMcp = Test-LooksLikeUnityMcp $proc
    if ($looksLikeMcp) {
      $rootPid = Get-UnityMcpRootPid -ProcessId $processId
      Write-Host "Stopping old Unity MCP PID=$rootPid on port $Port"
      Stop-ProcessTree -RootPid $rootPid
    } else {
      Write-Host "Port $Port is used by PID=$processId, but it does not look like Unity MCP. Leaving it untouched."
    }
  }
}

function Test-LooksLikeUnityMcp {
  param($Proc)
  if (-not $Proc -or -not $Proc.CommandLine) { return $false }
  return (
    $Proc.CommandLine -like "*mcp-for-unity*" -or
    $Proc.CommandLine -like "*mcpforunityserver*"
  )
}

function Stop-ProcessTree {
  param([int]$RootPid)
  $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $children = @($all | Where-Object { $_.ParentProcessId -eq $RootPid })
  foreach ($child in $children) {
    Stop-ProcessTree -RootPid $child.ProcessId
  }
  Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
}

function Get-UnityMcpRootPid {
  param([int]$ProcessId)
  $current = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  while ($current -and $current.ParentProcessId) {
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($current.ParentProcessId)" -ErrorAction SilentlyContinue
    if (-not (Test-LooksLikeUnityMcp $parent)) { break }
    $current = $parent
  }
  return $current.ProcessId
}

function Test-UnityMcp {
  param([string]$Url)
  $endpoint = $Url.TrimEnd("/") + "/mcp"
  try {
    Invoke-WebRequest $endpoint -UseBasicParsing -TimeoutSec 3 | Out-Null
    return $true
  } catch {
    $response = $_.Exception.Response
    if ($response -and $response.StatusCode) {
      $code = [int]$response.StatusCode
      return ($code -in 200, 400, 405, 406)
    }
    return $false
  }
}

$uvx = Get-UvxExe
$port = Get-PortFromUrl $HttpUrl
if (-not $NoStopExisting) {
  Stop-UnityMcpOnPort -Port $port
}

if (Test-UnityMcp -Url $HttpUrl) {
  Write-Host "Unity MCP: already reachable at $HttpUrl/mcp"
  exit 0
}

$fromPackage = "mcpforunityserver==$PackageVersion"
$arguments = @(
  "--from", $fromPackage,
  "mcp-for-unity",
  "--transport", "http",
  "--http-url", $HttpUrl,
  "--project-scoped-tools"
)

Write-Host "Starting Unity MCP: $uvx $($arguments -join ' ')"
Start-Process -FilePath $uvx `
  -ArgumentList $arguments `
  -RedirectStandardOutput "logs\unity_mcp.out.log" `
  -RedirectStandardError "logs\unity_mcp.err.log" `
  -WindowStyle Hidden

for ($i = 0; $i -lt 45; $i++) {
  Start-Sleep -Seconds 1
  if (Test-UnityMcp -Url $HttpUrl) {
    Write-Host "Unity MCP: OK at $HttpUrl/mcp"
    exit 0
  }
}

Write-Host "Unity MCP: failed to become ready at $HttpUrl/mcp"
Write-Host "Logs: logs\unity_mcp.out.log / logs\unity_mcp.err.log"
exit 1
