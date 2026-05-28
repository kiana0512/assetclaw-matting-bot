# Architecture Overview

## System Layers

```
┌────────────────────────────────────────────────────────────┐
│  Layer 1: Feishu Channel                                   │
│  • Receives text messages from users                       │
│  • Sends text/image replies                                │
│  • No business logic here — pure message pass-through      │
└───────────────────────────┬────────────────────────────────┘
                            │ text message
┌───────────────────────────▼────────────────────────────────┐
│  Layer 2: OpenClaw Agent Bridge                            │
│  • Tries local hardcoded commands first                    │
│  • Forwards unknown queries to cloud OpenClaw API          │
│  • Receives tool_call instructions from OpenClaw           │
│  • Dispatches tool_calls to Skill Gateway                  │
│  • NEVER runs AI locally — cloud API only                  │
└─────────────┬─────────────────────────────────────────────┘
              │ skill call
┌─────────────▼─────────────────────────────────────────────┐
│  Layer 3: Skill Gateway  (/skills/v1/*)                    │
│  • Exposes controlled skills to OpenClaw                   │
│  • Auth: X-Skill-Token on every request                    │
│  • Path whitelist + deny patterns                          │
│  • Audit log to skill_calls table                          │
│  • No shell exec, no file delete, no arbitrary reads       │
└─────────────┬─────────────────────────────────────────────┘
              │ batch/task operations
┌─────────────▼─────────────────────────────────────────────┐
│  Layer 4: Batch / Task Control Plane                       │
│  • SQLite: batches, tasks, events, skill_calls             │
│  • batch_service, task_service                             │
│  • Admin REST API (/admin/*)                               │
│  • Worker REST API (/worker/*)                             │
└─────────────┬─────────────────────────────────────────────┘
              │ HTTP polling
┌─────────────▼─────────────────────────────────────────────┐
│  Layer 5: Worker / Execution Plane  (Windows 3090)         │
│  • Single worker, polls gateway every N seconds            │
│  • Calls local ComfyUI API                                 │
│  • GPU used ONLY for ComfyUI — never for AI inference      │
│  • Writes output to configured output_path                 │
└─────────────┬─────────────────────────────────────────────┘
              │ REST API
┌─────────────▼─────────────────────────────────────────────┐
│  ComfyUI  http://127.0.0.1:8188                            │
│  • Local GPU inference                                     │
│  • Accepts workflow API JSON                               │
└────────────────────────────────────────────────────────────┘

External:
┌────────────────────────────────────────────────────────────┐
│  OpenClaw Cloud API  (external HTTPS)                      │
│  • All AI reasoning and planning                           │
│  • Returns text | tool_call | mixed                        │
│  • Does NOT access GPU machine directly                    │
│  • All access goes through Skill Gateway                   │
└────────────────────────────────────────────────────────────┘
```

## Key Design Principles

1. **GPU isolation** — The 3090 only runs ComfyUI. No local LLM, no agent inference.
2. **Cloud brain** — OpenClaw runs externally. Skill Gateway is the only entry point.
3. **Defense in depth** — Every skill call is authenticated, logged, and path-validated.
4. **Feishu = dumb terminal** — Feishu is a message pipe, not a business logic layer.
5. **Gradual enablement** — OPENCLAW_ENABLED=false for local testing; flip to true for production.
