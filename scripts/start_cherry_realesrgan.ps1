param(
  [int]$Port = 49125
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = "C:\Users\zhangqichao\Desktop\ComfyUI-aki-v3\python\python.exe"
$Server = "C:\imageclip\cherry_realesrgan_server.py"
$Model = "C:\imageclip\models\RealESRGAN_x4plus_anime_6B.pth"
$HealthUrl = "http://127.0.0.1:$Port/health"

function Test-CherryRealEsrgan {
  try {
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $HealthUrl
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

if (Test-CherryRealEsrgan) {
  Write-Host "Cherry Real-ESRGAN is already healthy at $HealthUrl"
  exit 0
}

foreach ($path in @($Python, $Server, $Model)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Cherry Real-ESRGAN dependency is missing: $path"
  }
}

$logDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Start-Process -FilePath $Python `
  -ArgumentList @("-u", $Server, "--model", $Model, "--port", "$Port") `
  -WorkingDirectory "C:\imageclip" `
  -RedirectStandardOutput (Join-Path $logDir "cherry_realesrgan.out.log") `
  -RedirectStandardError (Join-Path $logDir "cherry_realesrgan.err.log") `
  -WindowStyle Hidden

for ($attempt = 0; $attempt -lt 60; $attempt++) {
  Start-Sleep -Seconds 1
  if (Test-CherryRealEsrgan) {
    Write-Host "Cherry Real-ESRGAN is ready at $HealthUrl"
    exit 0
  }
}

throw "Cherry Real-ESRGAN did not become healthy within 60 seconds. Check logs\cherry_realesrgan.err.log."
