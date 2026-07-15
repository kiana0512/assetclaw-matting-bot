$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$envMap = @{}
Get-Content ".env" | ForEach-Object {
  if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
    $envMap[$Matches[1].Trim()] = $Matches[2].Trim()
  }
}

$key = $envMap["DEEPSEEK_API_KEY"]
$base = $envMap["DEEPSEEK_BASE_URL"]
$model = $envMap["DEEPSEEK_MODEL"]
if ([string]::IsNullOrWhiteSpace($base)) { $base = "https://api.deepseek.com" }
if ([string]::IsNullOrWhiteSpace($model)) { $model = "deepseek-v4-pro" }
if ([string]::IsNullOrWhiteSpace($key)) { throw "DEEPSEEK_API_KEY is empty." }

$base = $base.TrimEnd("/")
if ($base.EndsWith("/chat/completions")) { $url = $base } else { $url = "$base/chat/completions" }
$headers = @{
  "Authorization" = "Bearer $key"
  "Content-Type" = "application/json"
}

function Hide-Secrets([string]$text) {
  if ([string]::IsNullOrEmpty($text)) { return $text }
  return ($text -replace [regex]::Escape($key), "[REDACTED_KEY]") -replace "sk-[A-Za-z0-9_-]{8,}", "[REDACTED_KEY]"
}

function Invoke-DeepSeekTest($name, $body) {
  Write-Host "== $name =="
  try {
    $json = $body | ConvertTo-Json -Depth 10
    $resp = Invoke-WebRequest $url -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body $json -UseBasicParsing
    Write-Host "OK: $($resp.StatusCode)"
    $content = Hide-Secrets $resp.Content
    Write-Host $content
  } catch {
    $message = $_.ErrorDetails.Message
    if ([string]::IsNullOrWhiteSpace($message)) { $message = $_.Exception.Message }
    Write-Host "FAILED"
    Write-Host (Hide-Secrets $message)
    throw
  }
}

Invoke-DeepSeekTest "Minimal chat completions" @{
  model = $model
  messages = @(@{ role = "user"; content = "ping" })
  stream = $false
  temperature = 0.1
  thinking = @{ type = "disabled" }
}

Invoke-DeepSeekTest "JSON mode" @{
  model = $model
  messages = @(@{ role = "user"; content = "Return JSON only: {`"ok`":true}" })
  stream = $false
  temperature = 0.1
  thinking = @{ type = "disabled" }
  response_format = @{ type = "json_object" }
}

Write-Host "== Brain Router /brain/test =="
try {
  $body = @{ text = "看看 E 盘有哪些文件" } | ConvertTo-Json
  $resp = Invoke-WebRequest "http://127.0.0.1:7865/brain/test" -Method Post -ContentType "application/json; charset=utf-8" -Body $body -UseBasicParsing
  Write-Host "OK: $($resp.StatusCode)"
  Write-Host (Hide-Secrets $resp.Content)
} catch {
  $message = $_.ErrorDetails.Message
  if ([string]::IsNullOrWhiteSpace($message)) { $message = $_.Exception.Message }
  Write-Host "FAILED"
  Write-Host (Hide-Secrets $message)
  throw
}
