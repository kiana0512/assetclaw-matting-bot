# Create and start a batch matting job.
# Edit INPUT_DIR and OUTPUT_DIR before running.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent

# ---- Edit these ----
$InputDir      = "E:\assetclaw-matting-bot\storage\batch_inputs"
$OutputDir     = "E:\assetclaw-matting-bot\storage\batch_outputs"
$WorkflowType  = "matting_v1"
$NotifyChatId  = ""   # optional Feishu chat_id for progress notifications
# --------------------

conda activate assetclaw-matting
Set-Location $ProjectRoot

Write-Host "Creating batch from: $InputDir" -ForegroundColor Cyan
$output = python -m assetclaw_matting.cli.main batch-create `
    --input-dir "$InputDir" `
    --output-dir "$OutputDir" `
    --workflow-type "$WorkflowType"

Write-Host $output

# Extract batch ID and start it
$batchId = ($output | Select-String -Pattern 'BATCH_\w+').Matches[0].Value
if ($batchId) {
    Write-Host "Starting batch: $batchId" -ForegroundColor Green
    python -m assetclaw_matting.cli.main batch-start --batch-id "$batchId"
    Write-Host "Batch started. Now run scripts\run_worker.ps1 to process tasks." -ForegroundColor Green
} else {
    Write-Host "Could not extract batch ID from output. Start batch manually." -ForegroundColor Yellow
}
