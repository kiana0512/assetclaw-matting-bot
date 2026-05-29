# AssetClaw Win3090 Skill Node — Skill Reference

## What This Node Is

AssetClaw Win3090 is a local execution node for Windows 10 with an RTX 3090 GPU.

**You can:** Create batch image processing jobs, monitor queues, check ComfyUI.
**You cannot:** Execute shell commands, delete files, read secrets, write outside allowed paths.

All AI reasoning happens in the cloud. This node only executes.

## Current Capabilities (First Phase: matting_v1)

### batch.create
Create a batch ComfyUI job from a directory of images.
```json
{"skill":"batch.create","arguments":{"input_dir":"E:\\input","output_dir":"E:\\output","workflow_type":"matting_v1"}}
```
Returns: `batch_id`, `total_count`, `status`

### batch.start
Start a CREATED batch. Worker will begin processing.
```json
{"skill":"batch.start","arguments":{"batch_id":"BATCH_XXXX"}}
```

### batch.status
Get batch progress.
```json
{"skill":"batch.status","arguments":{"batch_id":"BATCH_XXXX"}}
```
Returns: total/succeeded/failed/running/queued counts

### batch.list
List recent batches.
```json
{"skill":"batch.list","arguments":{"limit":10}}
```

### batch.cancel
Cancel all queued tasks. Confirm with user first.
```json
{"skill":"batch.cancel","arguments":{"batch_id":"BATCH_XXXX"}}
```

### queue.status
Global queue statistics.
```json
{"skill":"queue.status","arguments":{}}
```

### task.status / task.list_failed
Task details and failed task investigation.

### worker.status / comfyui.status
Check execution infrastructure.

### file.list_allowed
List files under allowed path (metadata only).
```json
{"skill":"file.list_allowed","arguments":{"path":"E:\\batch_inputs","max_items":50}}
```

### log.tail
Read recent logs (auto-sanitised).
```json
{"skill":"log.tail","arguments":{"log_name":"worker","lines":50}}
```

## What You CANNOT Do

- Execute shell commands (`ALLOW_SHELL_EXEC=false`)
- Delete files (`ALLOW_FILE_DELETE=false`)
- Read file contents (`ALLOW_FILE_READ_CONTENT=false`)
- Access paths outside E: drive
- Access `.env`, `.ssh`, `Windows`, `AppData`, `Program Files`
- Run local LLM (GPU reserved for ComfyUI)

## Skill Calling Format

```
POST http://node:7865/skills/v1/call
X-Skill-Token: <token>
Content-Type: application/json

{"skill": "queue.status", "arguments": {}, "requested_by": "arkclaw"}
```
