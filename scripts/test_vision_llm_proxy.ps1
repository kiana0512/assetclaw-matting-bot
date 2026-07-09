$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Set-Location "E:\assetclaw-matting-bot"

function Get-CondaExe {
  $condaExe = Join-Path $env:USERPROFILE "miniconda3\Scripts\conda.exe"
  if (Test-Path $condaExe) { return $condaExe }
  $cmd = Get-Command conda -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw "conda not found. Install Miniconda or add conda to PATH."
}

$conda = Get-CondaExe
$env:PYTHONPATH = "E:\assetclaw-matting-bot\src;E:\assetclaw-matting-bot"

& $conda run --no-capture-output -n assetclaw python -X utf8 scripts\test_vision_llm_proxy.py

Write-Host "Vision LLM Proxy diagnostic complete"
