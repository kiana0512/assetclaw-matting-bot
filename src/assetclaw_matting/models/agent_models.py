from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    result: Optional[Any] = None
    error: Optional[str] = None


class AgentResult(BaseModel):
    reply: str
    tool_calls: list[ToolCall] = []
    used_llm: bool = False


class AgentContext(BaseModel):
    chat_id: Optional[str] = None
    sender_id: Optional[str] = None
    channel: str = "feishu"
