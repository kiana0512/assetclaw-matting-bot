# OpenClaw Cloud Agent Integration Guide

## 1. Overview

```
Feishu Message
    → Gateway (FastAPI)
    → OpenClaw Bridge
    → [Cloud] OpenClaw API  (AI reasoning, NOT local GPU)
    → tool_call: "batch.create"
    → Skill Gateway (/skills/v1/call)
    → batch_service.create_batch()
    → SQLite + Worker queue
    → ComfyUI (local GPU, only for image processing)
    → Result → OpenClaw → Feishu reply
```

## 2. Message Flow

### Local command mode (`OPENCLAW_MESSAGE_MODE=local_command_first`)
1. User sends "batch list" in Feishu.
2. Bridge detects known command → runs locally → replies.
3. No OpenClaw call made.

### OpenClaw relay mode
1. User sends "帮我抠一下 E:\input 里的图片" in Feishu.
2. Bridge checks: not a known hardcoded command.
3. Bridge calls `POST {OPENCLAW_BASE_URL}/api/v1/chat` with:
   - conversation_id = chat_id
   - user_id = sender_id
   - text = user message
   - machine_context = {machine_id, fake_mode, agent_runs_on_gpu}
   - available_skills = [list from GET /skills/v1/manifest]
4. OpenClaw returns tool_call: `{"skill": "batch.create", "arguments": {...}}`.
5. Bridge calls Skill Gateway → executes → returns result.
6. Result is sent back to OpenClaw or directly replied to Feishu.

## 3. OpenClaw API Protocol (Draft)

### POST /api/v1/chat
Request:
```json
{
  "conversation_id": "oc_xxx",
  "user_id": "ou_xxx",
  "text": "帮我抠图",
  "machine_context": {
    "machine_id": "win3090-worker-01",
    "agent_runs_on_gpu": false,
    "comfyui_fake_mode": false
  },
  "available_skills": ["batch.create", "batch.start", "queue.status", ...]
}
```

Response (text):
```json
{"type": "text", "text": "收到！请提供输入目录和输出目录。"}
```

Response (tool_call):
```json
{
  "type": "tool_call",
  "text": "好的，正在创建批次...",
  "tool_calls": [
    {
      "skill": "batch.create",
      "arguments": {
        "input_dir": "E:\\input",
        "output_dir": "E:\\output",
        "workflow_type": "matting_v1"
      },
      "call_id": "tc_001"
    }
  ]
}
```

## 4. Skill Manifest Example

```
GET /skills/v1/manifest
X-Skill-Token: your_token

{
  "machine_id": "win3090-worker-01",
  "gpu": "RTX 3090 24GB",
  "agent_runs_on_gpu": false,
  "available_skills": [
    {"name": "batch.create", "danger_level": "medium", "implemented": true},
    {"name": "queue.status", "danger_level": "low",    "implemented": true},
    {"name": "frame.extract","danger_level": "medium", "implemented": false}
  ]
}
```

## 5. Skill Call Example

```
POST /skills/v1/call
X-Skill-Token: your_token
Content-Type: application/json

{
  "skill": "batch.create",
  "arguments": {
    "input_dir": "E:\\batch_inputs\\project_001",
    "output_dir": "E:\\batch_outputs\\project_001",
    "workflow_type": "matting_v1",
    "notify_chat_id": "oc_xxx"
  },
  "request_id": "req_abc123",
  "requested_by": "openclaw"
}

→ 200 OK
{
  "ok": true,
  "skill": "batch.create",
  "result": {"batch_id": "BATCH_ABC123...", "total_count": 42, "status": "CREATED"},
  "message": "Skill batch.create executed successfully"
}
```

## 6. Security Boundaries

- OpenClaw can ONLY call whitelisted skills.
- All paths are validated against ALLOWED_ROOTS.
- DENY_PATH_PATTERNS block sensitive directories.
- Shell execution is impossible via skills.
- File deletion is impossible via skills.
- All calls logged to `skill_calls` table.

## 7. Adding a New Skill

1. Implement the function in the appropriate `skills/*.py` file.
2. Add an entry to `SKILL_CATALOG` in `skills/registry.py`.
3. Set `implemented: True` and provide the `fn` reference.
4. Update `docs/SKILLS.md`.
5. Redeploy gateway — OpenClaw picks up new skill on next `/manifest` call.

## 8. Connecting to Company OpenClaw API

1. Set in `.env`:
   ```
   OPENCLAW_ENABLED=true
   OPENCLAW_BASE_URL=https://your.openclaw.api
   OPENCLAW_API_KEY=your_api_key
   OPENCLAW_BOT_ID=your_bot_id
   ```
2. Verify the protocol matches `openclaw/client.py` `_PATH_CHAT` and `_PATH_EVENT`.
3. Adjust the response schema in `openclaw/schemas.py` if needed.
4. Set `OPENCLAW_MESSAGE_MODE=local_command_first` to keep local commands fast.

## 9. Why Not Run Local LLM?

- RTX 3090 24GB is fully occupied by ComfyUI workflows during batch processing.
- Running a local LLM would compete for VRAM and stall image jobs.
- Cloud inference adds latency (<1s for typical queries) but zero GPU impact.
- OpenClaw runs on separate infrastructure — 3090 stays dedicated to image work.

## 10. Future: RAG Document Integration

OpenClaw can be equipped with a RAG pipeline that includes:
- This repo's SKILLS.md and ARCHITECTURE.md
- Workflow documentation
- Past batch execution summaries

The gateway can push summaries to OpenClaw via `POST /openclaw/webhook` or via
`send_event()` calls after batch completion.
