param(
  [string]$ControllerHost = "10.3.34.11",
  [int]$ControllerPort = 443,
  [string]$CaBundle = "C:\Users\zhangqichao\Downloads\GPU_CONTROL_LAN_CA.crt",
  [string]$AssetPython = "C:\Users\zhangqichao\miniconda3\envs\assetclaw\python.exe"
)

$ErrorActionPreference = "Continue"
chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

function Write-Section([string]$Name) {
  Write-Host ""
  Write-Host "=== $Name ==="
}

Write-Section "HOST"
Write-Host "hostname=$env:COMPUTERNAME"
Get-CimInstance Win32_OperatingSystem |
  Select-Object Caption, Version, BuildNumber, OSArchitecture |
  Format-List

Write-Section "NETWORK"
Get-NetAdapter |
  Where-Object Status -eq "Up" |
  Select-Object Name, InterfaceDescription, ifIndex, MacAddress, LinkSpeed |
  Format-Table -AutoSize
Get-NetIPConfiguration |
  Where-Object IPv4DefaultGateway |
  Select-Object InterfaceAlias, InterfaceIndex, IPv4Address, IPv4DefaultGateway, DNSServer |
  Format-List
$tcp = Test-NetConnection -ComputerName $ControllerHost -Port $ControllerPort -InformationLevel Detailed
$tcp |
  Select-Object ComputerName, RemoteAddress, RemotePort, InterfaceAlias, SourceAddress, TcpTestSucceeded |
  Format-List

Write-Section "WSL"
wsl.exe --version 2>&1
wsl.exe --status 2>&1
wsl.exe --list --verbose 2>&1
foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
  Get-WindowsOptionalFeature -Online -FeatureName $feature |
    Select-Object FeatureName, State, RestartRequired |
    Format-Table -AutoSize
}

Write-Section "GPU"
nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,compute_cap,pci.bus_id --format=csv,noheader

Write-Section "DISK"
$drive = [System.IO.DriveInfo]::new("C")
[pscustomobject]@{
  Drive = $drive.Name
  TotalGiB = [math]::Round($drive.TotalSize / 1GB, 3)
  FreeGiB = [math]::Round($drive.AvailableFreeSpace / 1GB, 3)
} | Format-List

Write-Section "DOCKER"
$docker = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($docker) {
  docker version
} else {
  Write-Host "docker=NOT_INSTALLED"
}

Write-Section "GPU_CONTROL_TLS"
if (-not (Test-Path -LiteralPath $AssetPython -PathType Leaf)) {
  Write-Host "asset_python=NOT_FOUND: $AssetPython"
} elseif (-not (Test-Path -LiteralPath $CaBundle -PathType Leaf)) {
  Write-Host "ca_bundle=NOT_FOUND: $CaBundle"
} else {
  Get-FileHash -Algorithm SHA256 -LiteralPath $CaBundle | Format-List Algorithm, Hash, Path
  & $AssetPython "scripts\check_gpu_control_tls.py" `
    --base-url "https://${ControllerHost}:${ControllerPort}" `
    --ca-bundle $CaBundle
}

Write-Section "SUMMARY"
Write-Host "Expected worker_id=worker-4070ti-animation-host-01"
Write-Host "Expected GPU UUID=GPU-70c028e4-dd91-4337-8f96-29daa437d1c3"
Write-Host "Expected Windows MAC=34-5A-60-47-C6-1D"
Write-Host "Ubuntu 22.04.5 host preparation is complete; Docker/NVIDIA Container Toolkit remain pending the locked GPU Control delivery."
