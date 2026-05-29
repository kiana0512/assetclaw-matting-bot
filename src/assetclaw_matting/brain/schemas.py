"""Brain Router shared data models."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class BrainMessage(BaseModel):
    channel: str = "feishu"          # feishu | api | cli
    conversation_id: str = ""
    user_id: str = ""
    text: str = ""
    attachments: list[dict[str, Any]] = []


class BrainContext(BaseModel):
    machine_id: str = ""
    gpu: str = "RTX 3090 24GB"
    agent_runs_on_gpu: bool = False
    queue_summary: str = ""
    comfyui_status: str = ""
    worker_status: str = ""
    allowed_roots: list[str] = []
    available_workflows: list[str] = ["matting_v1"]
    skills_manifest: dict[str, Any] = {}
    recent_batches: list[dict[str, Any]] = []
    security_policy_summary: str = ""


class BrainToolCall(BaseModel):
    skill: str
    arguments: dict[str, Any] = {}
    requires_confirmation: bool = False
    call_id: str = ""


class BrainResponse(BaseModel):
    text: str = ""
    tool_calls: list[BrainToolCall] = []
    attachments: list[dict[str, Any]] = []
    requires_confirmation: bool = False
    raw: dict[str, Any] = {}
    provider: str = ""
