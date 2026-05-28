"""JSON Schema definitions for agent tools (OpenAI-compatible function calling format)."""
from __future__ import annotations

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "batch_create",
        "description": "Create a new batch from an input directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "input_dir": {"type": "string", "description": "Absolute path to input image directory"},
                "output_dir": {"type": "string", "description": "Absolute path to output directory"},
                "workflow_type": {"type": "string", "default": "matting_v1"},
                "notify_chat_id": {"type": "string", "description": "Feishu chat_id for notifications"},
                "note": {"type": "string"},
            },
            "required": ["input_dir", "output_dir"],
        },
    },
    {
        "name": "batch_start",
        "description": "Start a CREATED batch so the worker can pick up tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "batch_status",
        "description": "Get the current status and progress of a batch.",
        "parameters": {
            "type": "object",
            "properties": {"batch_id": {"type": "string"}},
            "required": ["batch_id"],
        },
    },
    {
        "name": "batch_list",
        "description": "List recent batches.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "batch_cancel",
        "description": "Cancel a batch (cancels all QUEUED tasks; RUNNING tasks finish naturally).",
        "parameters": {
            "type": "object",
            "properties": {"batch_id": {"type": "string"}},
            "required": ["batch_id"],
        },
    },
    {
        "name": "queue_status",
        "description": "Get global queue statistics.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "worker_status",
        "description": "Get worker activity summary.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "comfyui_status",
        "description": "Check whether ComfyUI is online.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "task_list_failed",
        "description": "List failed tasks in a batch.",
        "parameters": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["batch_id"],
        },
    },
    {
        "name": "task_retry_failed",
        "description": "Retry failed tasks in a batch. (Not yet implemented)",
        "parameters": {
            "type": "object",
            "properties": {"batch_id": {"type": "string"}},
            "required": ["batch_id"],
        },
    },
    {
        "name": "log_summarize",
        "description": "Summarize recent gateway or worker logs. (Not yet implemented)",
        "parameters": {
            "type": "object",
            "properties": {
                "log_type": {"type": "string", "enum": ["gateway", "worker"]},
                "lines": {"type": "integer", "default": 50},
            },
        },
    },
]
