from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FeishuMessageEvent:
    trace_id: str
    event_id: Optional[str]
    message_id: str
    chat_id: str
    chat_type: str          # "p2p" or "group"
    open_id: Optional[str]
    user_id: Optional[str]
    text: str
    raw_event: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeishuProcessResult:
    ok: bool
    trace_id: str
    reply_text: Optional[str] = None
    error: Optional[dict[str, Any]] = None
