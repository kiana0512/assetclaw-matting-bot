"""ArkClaw wire protocol data models."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ArkClawToolCall(BaseModel):
    """A skill invocation requested by the ArkClaw cloud agent."""
    skill: str
    arguments: dict[str, Any] = {}
    call_id: str = ""


class ArkClawResponse(BaseModel):
    """Response from the ArkClaw cloud agent."""
    # type: text | tool_call | mixed
    type: str = "text"
    text: str = ""
    tool_calls: list[ArkClawToolCall] = []


class ArkClawRequest(BaseModel):
    """Message sent to the ArkClaw cloud agent."""
    conversation_id: str
    user_id: str
    text: str
    machine_context: dict[str, Any] = {}
    available_skills: list[str] = []
    recent_queue_summary: Optional[str] = None
    security_policy_summary: Optional[str] = None


class ArkClawSkillCallback(BaseModel):
    """Payload sent back to ArkClaw after skill execution."""
    conversation_id: str
    request_id: str
    skill: str
    result: dict[str, Any]
    ok: bool
    error: Optional[str] = None
