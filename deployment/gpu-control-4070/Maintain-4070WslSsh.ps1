$ErrorActionPreference = 'Stop'

$Distro = 'Ubuntu'
$UpdateScript = 'C:\ProgramData\GPUControl\Update-4070WslSshProxy.ps1'
$LogDirectory = 'C:\ProgramData\GPUControl\logs'
$LogPath = Join-Path $LogDirectory 'wsl-ssh-maintainer.log'
$WslExe = "$env:SystemRoot\System32\wsl.exe"

function Write-MaintainerLog([string]$Level, [string]$Message) {
  New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
  $line = '{0} level={1} {2}' -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Level, $Message
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Get-KeepAliveProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*-d Ubuntu*sleep infinity*' })
}

Write-MaintainerLog 'INFO' 'maintainer_started'

while ($true) {
  try {
    $KeepAliveProcesses = Get-KeepAliveProcesses
    if ($KeepAliveProcesses.Count -eq 0) {
      $Process = Start-Process `
        -FilePath $WslExe `
        -ArgumentList @('-d', $Distro, '-u', 'gpucontrol', '--', '/bin/sleep', 'infinity') `
        -WindowStyle Hidden `
        -PassThru
      Write-MaintainerLog 'INFO' "keepalive_started pid=$($Process.Id)"
      Start-Sleep -Seconds 3
    }

    $Output = & $UpdateScript 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Update script exit code: $LASTEXITCODE"
    }

    $Changed = @($Output | Where-Object { $_ -match 'CHANGED=True' })
    if ($Changed.Count -gt 0) {
      Write-MaintainerLog 'INFO' ($Changed -join ' ')
    }
  } catch {
    Write-MaintainerLog 'ERROR' ($_.Exception.Message -replace '[\r\n]+', ' ')
  }

  Start-Sleep -Seconds 60
}
