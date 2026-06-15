$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot\external_webui"

$env:ASSETCLAW_AGENT_URL = if ($env:ASSETCLAW_AGENT_URL) { $env:ASSETCLAW_AGENT_URL } else { "http://127.0.0.1:7865" }

Write-Host "Starting AssetClaw External WebUI on http://127.0.0.1:5180 ..."
npm run dev -- --host 127.0.0.1 --port 5180
