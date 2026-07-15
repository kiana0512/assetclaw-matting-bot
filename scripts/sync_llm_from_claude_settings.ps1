$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
if (-not (Test-Path $settingsPath)) { throw "Claude settings not found: $settingsPath" }

$settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
$envConfig = $settings.env
if (-not $envConfig) { throw "Claude settings has no env block." }

$token = $envConfig.ANTHROPIC_AUTH_TOKEN
$baseUrl = $envConfig.ANTHROPIC_BASE_URL
$sonnet = $envConfig.ANTHROPIC_DEFAULT_SONNET_MODEL

if ([string]::IsNullOrWhiteSpace($token)) { throw "ANTHROPIC_AUTH_TOKEN is missing in Claude settings." }
if ([string]::IsNullOrWhiteSpace($baseUrl)) { $baseUrl = "https://llm-proxy.lilithgames.com" }
if ([string]::IsNullOrWhiteSpace($sonnet)) { $sonnet = "claude-sonnet-4-6" }

$envText = Get-Content ".env" -Raw
$pairs = @{
  "LLM_PROXY_BASE_URL" = $baseUrl
  "LLM_PROXY_API_KEY" = $token
  "LLM_PROXY_AUTH_HEADER" = "authorization_bearer"
  "LLM_PROXY_MODEL" = $sonnet
  "LLM_PROXY_COMPLEX_MODEL" = $sonnet
  "LLM_PROXY_SUMMARY_MODEL" = $sonnet
  "LLM_PROXY_OPENAI_COMPATIBLE" = "false"
}

foreach ($key in $pairs.Keys) {
  $value = $pairs[$key]
  if ($envText -match "(?m)^$key=") {
    $envText = $envText -replace "(?m)^$key=.*", "$key=$value"
  } else {
    $envText += "`r`n$key=$value"
  }
}

Set-Content ".env" $envText -Encoding UTF8
Write-Host "Synced LLM Proxy settings from Claude Code settings."
Write-Host "Base URL: $baseUrl"
Write-Host "Model: $sonnet"
Write-Host "Token: [REDACTED]"
