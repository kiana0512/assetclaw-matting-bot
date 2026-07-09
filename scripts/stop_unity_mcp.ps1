param(
  [string]$HttpUrl = "http://127.0.0.1:8080"
)

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

function Get-PortFromUrl {
  param([string]$Url)
  try {
    $uri = [System.Uri]$Url
    if ($uri.Port -gt 0) { return $uri.Port }
  } catch {}
  return 8080
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

$port = Get-PortFromUrl $HttpUrl
$listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listeners) {
  Write-Host "Unity MCP: not running on port $port"
  exit 0
}

$stopped = 0
$pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($processId in $pids) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
  $looksLikeMcp = Test-LooksLikeUnityMcp $proc
  if ($looksLikeMcp) {
    $rootPid = Get-UnityMcpRootPid -ProcessId $processId
    Write-Host "Stopping Unity MCP PID=$rootPid on port $port"
    Stop-ProcessTree -RootPid $rootPid
    $stopped++
  } else {
    Write-Host "Port $port is used by PID=$processId, but it does not look like Unity MCP. Leaving it untouched."
  }
}

if ($stopped -gt 0) {
  Write-Host "Unity MCP: stopped"
} else {
  Write-Host "Unity MCP: no matching process stopped"
}
