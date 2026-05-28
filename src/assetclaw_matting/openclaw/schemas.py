"""Data models for the OpenClaw cloud agent protocol."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class OpenClawToolCall(BaseModel):
    """A skill invocation requested by the cloud agent."""
    skill: str
    arguments: dict[str, Any] = {}
    call_id: str = ""


class OpenClawResponse(BaseModel):
    """Response returned by the OpenClaw cloud agent."""
    # type: text | tool_call | mixed
    type: str = "text"
    text: str = ""
    tool_calls: list[OpenClawToolCall] = []


class OpenClawRequest(BaseModel):
    """Message sent to the OpenClaw cloud agent."""
    conversation_id: str
    user_id: str
    text: str
    machine_context: dict[str, Any] = {}
    available_skills: list[str] = []
    recent_batch_summary: Optional[str] = None


class SkillCallbackPayload(BaseModel):
    """Payload sent back to OpenClaw after executing a skill."""
    call_id: str
    skill: str
    result: dict[str, Any]
    ok: bool
    error: Optional[str] = None
