# Batch Matting — Standard Operating Procedure

## Prerequisites

- Gateway is running (`python -m assetclaw_matting.cli.main gateway`)
- Worker is running (`python -m assetclaw_matting.cli.main worker`)
- ComfyUI is running (or `COMFYUI_FAKE_MODE=true` for testing)

## Step-by-Step

### 1. Prepare Input Directory

Place PNG/JPG/WEBP/BMP images in any directory under `ALLOWED_ROOTS`.
Example: `E:\batch_inputs\project_001\`

### 2. Create a Batch

```powershell
python -m assetclaw_matting.cli.main batch-create `
  --input-dir  "E:\batch_inputs\project_001" `
  --output-dir "E:\batch_outputs\project_001" `
  --workflow-type matting_v1 `
  --notify-chat-id "oc_your_feishu_chat_id"
```

Note the `BATCH_XXXXXXXXXXXX` ID from the output.

### 3. Start the Batch

```powershell
python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXXXXXXXXXXX
```

The worker will now pick up tasks automatically.

### 4. Monitor Progress

```powershell
# CLI
python -m assetclaw_matting.cli.main batch-status --batch-id BATCH_XXXXXXXXXXXX
python -m assetclaw_matting.cli.main queue

# Feishu
# Send: batch status BATCH_XXXXXXXXXXXX

# API
Invoke-RestMethod "http://127.0.0.1:7865/admin/batches/BATCH_XXXXXXXXXXXX"
```

### 5. Check Results

When batch status = `SUCCEEDED`:
- Output files are at `E:\batch_outputs\project_001\*_matting.png`
- Each output filename = `{original_stem}_matting.png`

### 6. Handle Failures

If some tasks failed:
```powershell
python -m assetclaw_matting.cli.main task-list --batch-id BATCH_XXXXXXXXXXXX --status FAILED
```
Check the `error` column for details. Common causes:
- ComfyUI OOM → reduce image size or restart ComfyUI
- Workflow patch error → verify matting_api.json has LoadImage node
- ComfyUI offline → restart ComfyUI

## Via Skill API (for OpenClaw integration)

```powershell
# Create
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "X-Skill-Token: your_token" `
  -H "Content-Type: application/json" `
  -d '{"skill":"batch.create","arguments":{"input_dir":"E:\\input","output_dir":"E:\\output"}}'

# Start
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "X-Skill-Token: your_token" `
  -H "Content-Type: application/json" `
  -d '{"skill":"batch.start","arguments":{"batch_id":"BATCH_XXX"}}'
```
