# ComfyUI Workflow Integration Guide

## Requirements

- ComfyUI must have at least one **LoadImage** node.
- ComfyUI must have at least one **SaveImage** node.
- Workflow must be exported in **API format** (not the regular save format).

## Exporting a Workflow

1. Open ComfyUI in your browser.
2. Go to Settings → Enable Dev Mode Options → toggle ON.
3. Build your workflow (e.g., BRIA RMBG or similar matting model).
4. Click **Save (API Format)** (not the regular Save button).
5. Save as `E:\assetclaw-matting-bot\workflows\matting_api.json`.

## Workflow Patching

The gateway automatically patches the workflow before each run:
- Finds all nodes with `class_type == "LoadImage"`.
- Replaces `inputs.image` with the uploaded input filename.
- If multiple LoadImage nodes exist, patches only the first and logs a warning.

## Workflow Validation

Before your first real run, verify:
```powershell
# Check ComfyUI is online
Invoke-RestMethod -Uri http://127.0.0.1:7865/admin/comfyui/status

# The response should show status=online, not offline
```

## Debug Outputs

If a workflow run fails to find SaveImage outputs, the history JSON is saved to:
```
storage/debug/history_{task_id}.json
```

This lets you inspect what nodes produced output and debug the parsing.

## Fake Mode

`COMFYUI_FAKE_MODE=true` skips ComfyUI entirely. The worker uses Pillow to
convert the input image to RGBA and saves it as the output. This is safe for
testing the full pipeline (Feishu → Gateway → Worker → output dir) without a GPU.

## Recommended Workflow: BRIA RMBG

1. Install the BRIA RMBG custom node in ComfyUI.
2. Build: `LoadImage → BRIA_RMBG_ModelLoader → BRIA_RMBG → SaveImage`
3. Export API format → `workflows/matting_api.json`.
4. Set `COMFYUI_FAKE_MODE=false`.
5. Run a test batch with 1 image.
