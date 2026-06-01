from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BrainMessage(BaseModel):
    channel: str = "feishu"
    conversation_id: str = ""
    user_id: str = ""
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ToolCall(BaseModel):
    skill: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class BrainResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    provider: str = ""


BrainToolCall = ToolCall
