$ErrorActionPreference = 'Stop'

$SourceScript = 'C:\assetclaw-matting-bot\deployment\gpu-control-4070\Update-4070WslSshProxy.ps1'
$SourceMaintainer = 'C:\assetclaw-matting-bot\deployment\gpu-control-4070\Maintain-4070WslSsh.ps1'
$InstallDirectory = 'C:\ProgramData\GPUControl'
$InstalledScript = Join-Path $InstallDirectory 'Update-4070WslSshProxy.ps1'
$InstalledMaintainer = Join-Path $InstallDirectory 'Maintain-4070WslSsh.ps1'
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$TaskArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$InstalledMaintainer`""
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
Copy-Item -LiteralPath $SourceScript -Destination $InstalledScript -Force
Copy-Item -LiteralPath $SourceMaintainer -Destination $InstalledMaintainer -Force

$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $TaskArguments
$Principal = New-ScheduledTaskPrincipal `
  -UserId $UserId `
  -LogonType Interactive `
  -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew `
  -RestartCount 999 `
  -RestartInterval (New-TimeSpan -Minutes 1)

$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$MaintainerTask = New-ScheduledTask `
  -Action $Action `
  -Trigger $LogonTrigger `
  -Principal $Principal `
  -Settings $Settings `
  -Description 'Hidden long-running maintainer: keep the per-user Ubuntu WSL instance alive and repair only the 4070 SSH portproxy when eth0 changes.'

Stop-ScheduledTask -TaskName 'GPUControl-4070-WSL-Start' -ErrorAction SilentlyContinue
Stop-ScheduledTask -TaskName 'GPUControl-4070-WSL-Watchdog' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'GPUControl-4070-WSL-Start' -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'GPUControl-4070-WSL-Watchdog' -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
  -TaskName 'GPUControl-4070-WSL-Maintainer' `
  -InputObject $MaintainerTask `
  -Force | Out-Null
Start-ScheduledTask -TaskName 'GPUControl-4070-WSL-Maintainer'

Write-Output "INSTALLED_SCRIPT=$InstalledScript"
Write-Output "INSTALLED_MAINTAINER=$InstalledMaintainer"
Write-Output "TASK_USER=$UserId"
Get-ScheduledTask -TaskName 'GPUControl-4070-WSL-Maintainer' |
  Select-Object TaskName,State,@{Name='UserId';Expression={$_.Principal.UserId}},@{Name='LogonType';Expression={$_.Principal.LogonType}},@{Name='RunLevel';Expression={$_.Principal.RunLevel}}
