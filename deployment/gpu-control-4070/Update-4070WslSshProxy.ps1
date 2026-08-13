$ErrorActionPreference = 'Stop'

$Distro = 'Ubuntu'
$ListenAddress = '10.3.34.238'
$ListenPort = 2222
$ControlAddress = '10.3.34.11'
$FirewallName = 'GPUControl-4070-SSH-From-4090'
$FirewallDisplayName = 'GPU Control SSH 2222 from 4090'
$WslExe = "$env:SystemRoot\System32\wsl.exe"
$LogDirectory = 'C:\ProgramData\GPUControl\logs'
$LogPath = Join-Path $LogDirectory 'wsl-ssh-proxy.log'

function Write-ChangeLog([string]$Message) {
  New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
  $line = '{0} {1}' -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $Message
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

wsl.exe -d $Distro -u root -- systemctl start ssh
if ($LASTEXITCODE -ne 0) {
  throw "Unable to start ssh in WSL distribution: $Distro"
}

$AddressJson = & $WslExe -d $Distro -u root -- ip -j -4 addr show dev eth0
$InterfaceInfo = $AddressJson | ConvertFrom-Json
$WslIp = @($InterfaceInfo.addr_info | Where-Object { $_.family -eq 'inet' } | Select-Object -ExpandProperty local -First 1)[0]

if (-not $WslIp) {
  throw 'Unable to discover WSL2 IPv4 address'
}

$ParsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($WslIp, [ref]$ParsedAddress)) {
  throw "Invalid WSL IPv4 address: $WslIp"
}
if ($WslIp -eq $ListenAddress -or $WslIp.StartsWith('127.')) {
  throw "Unsafe WSL target address: $WslIp"
}

Set-Service iphlpsvc -StartupType Automatic
Start-Service iphlpsvc

$ExistingProxyLines = & netsh interface portproxy show v4tov4
$ExpectedMappingExists = $false
foreach ($line in $ExistingProxyLines) {
  $fields = @($line.Trim() -split '\s+' | Where-Object { $_ })
  if ($fields.Count -eq 4 -and
      $fields[0] -eq $ListenAddress -and
      $fields[1] -eq [string]$ListenPort -and
      $fields[2] -eq $WslIp -and
      $fields[3] -eq '22') {
    $ExpectedMappingExists = $true
    break
  }
}

if (-not $ExpectedMappingExists) {
  & netsh interface portproxy delete v4tov4 `
    listenaddress=$ListenAddress listenport=$ListenPort | Out-Null
  & netsh interface portproxy add v4tov4 `
    listenaddress=$ListenAddress listenport=$ListenPort `
    connectaddress=$WslIp connectport=22 protocol=tcp
  if ($LASTEXITCODE -ne 0) {
    throw 'Failed to create Windows portproxy rule'
  }
  Write-ChangeLog "portproxy_updated listen=${ListenAddress}:${ListenPort} target=${WslIp}:22"
}

$FirewallIsCorrect = $false
$ExistingFirewall = Get-NetFirewallRule -Name $FirewallName -ErrorAction SilentlyContinue
if ($ExistingFirewall) {
  $AddressFilter = $ExistingFirewall | Get-NetFirewallAddressFilter
  $PortFilter = $ExistingFirewall | Get-NetFirewallPortFilter
  $FirewallIsCorrect = (
    $ExistingFirewall.Enabled -eq 'True' -and
    $ExistingFirewall.Direction -eq 'Inbound' -and
    $ExistingFirewall.Action -eq 'Allow' -and
    $AddressFilter.LocalAddress -contains $ListenAddress -and
    $AddressFilter.RemoteAddress -contains $ControlAddress -and
    $PortFilter.Protocol -eq 'TCP' -and
    $PortFilter.LocalPort -contains [string]$ListenPort
  )
}

if (-not $FirewallIsCorrect) {
  $ExistingFirewall | Remove-NetFirewallRule
  New-NetFirewallRule `
    -Name $FirewallName `
    -DisplayName $FirewallDisplayName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $ListenAddress `
    -LocalPort $ListenPort `
    -RemoteAddress $ControlAddress `
    -Profile Any | Out-Null
  Write-ChangeLog "firewall_updated local=${ListenAddress}:${ListenPort} remote=${ControlAddress}"
}

Write-Output "WSL_IPV4=$WslIp"
Write-Output "PORTPROXY_CHANGED=$(-not $ExpectedMappingExists)"
Write-Output "FIREWALL_CHANGED=$(-not $FirewallIsCorrect)"
