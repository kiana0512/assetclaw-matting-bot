$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

Write-Host "注意：当前生产推荐使用 scripts\test_deepseek_api.ps1；本脚本仅用于旧 LLM Proxy 兼容测试。"

$envMap = @{}
Get-Content ".env" | ForEach-Object {
  if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
    $envMap[$Matches[1].Trim()] = $Matches[2].Trim()
  }
}

$key = $envMap["LLM_PROXY_API_KEY"]
$base = $envMap["LLM_PROXY_BASE_URL"].TrimEnd("/")
$model = $envMap["LLM_PROXY_MODEL"]
$compatible = $envMap["LLM_PROXY_OPENAI_COMPATIBLE"]
$authHeader = $envMap["LLM_PROXY_AUTH_HEADER"]

if ([string]::IsNullOrWhiteSpace($key)) { throw "LLM_PROXY_API_KEY is empty." }
if ([string]::IsNullOrWhiteSpace($model)) { $model = "claude-sonnet-4-6" }

if ($compatible -eq "false") {
  if ($base.EndsWith("/v1/messages")) { $url = $base }
  elseif ($base.EndsWith("/v1")) { $url = "$base/messages" }
  else { $url = "$base/v1/messages" }

  if ($authHeader -eq "x-api-key") {
    $headers = @{ "x-api-key" = $key; "anthropic-version" = "2023-06-01" }
  } else {
    $headers = @{ "Authorization" = "Bearer $key"; "anthropic-version" = "2023-06-01" }
  }
  $body = @{
    model = $model
    max_tokens = 64
    messages = @(@{ role = "user"; content = "ping" })
  } | ConvertTo-Json -Depth 5
} else {
  if ($base.EndsWith("/chat/completions")) { $url = $base }
  else { $url = "$base/chat/completions" }
  $headers = @{ "Authorization" = "Bearer $key" }
  $body = @{
    model = $model
    messages = @(@{ role = "user"; content = "ping" })
    max_tokens = 64
  } | ConvertTo-Json -Depth 5
}

try {
  $resp = Invoke-WebRequest $url -Method Post -Headers $headers -ContentType "application/json" -Body $body -UseBasicParsing
  Write-Host "LLM Proxy OK: $($resp.StatusCode)"
  $resp.Content
} catch {
  $message = $_.ErrorDetails.Message
  if ([string]::IsNullOrWhiteSpace($message)) { $message = $_.Exception.Message }
  $safe = $message.Replace($key, "[REDACTED_KEY]")
  Write-Host "LLM Proxy FAILED"
  Write-Host $safe
  exit 1
}
